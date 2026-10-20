"""Retrieval over the persistent ChromaDB technical documentation collection.

Returns plain chunk text, source metadata, and cosine distances so the agent
can apply a relevance threshold before generating recommendations.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, settings
from app.rag.ingestion import embed_texts, get_chroma_collection, get_genai_client


def retrieve(
    query: str,
    *,
    top_k: int | None = None,
    category: str | None = None,
    cfg: Settings | None = None,
) -> list[dict[str, Any]]:
    """
    Return the most relevant document chunks for a search query.

    Each result includes:
    - text: plain chunk text (ready for LLM context)
    - source / doc_id / category / chunk_index: attribution metadata
    - distance: Chroma cosine distance (lower is closer / more similar)
    - score: convenience value computed as 1 - distance (not a Chroma native field)
    - metadata: full metadata payload
    - id: stable chunk id

    Important: with cosine space, smaller distance means better match.
    Do not treat distance as a similarity score where larger is better.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    cfg = cfg or settings
    top_k = top_k or cfg.retrieval_top_k

    client = get_genai_client(cfg.google_api_key)
    collection = get_chroma_collection(cfg)

    if collection.count() == 0:
        raise RuntimeError(
            "ChromaDB collection is empty. Run `python -m app.rag.ingestion` first."
        )

    query_embedding = embed_texts(
        [query.strip()],
        task_type="RETRIEVAL_QUERY",
        client=client,
        cfg=cfg,
    )[0]

    where = {"category": category} if category else None

    raw = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    results: list[dict[str, Any]] = []
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    ids = (raw.get("ids") or [[]])[0]

    for doc, meta, distance, chunk_id in zip(documents, metadatas, distances, ids):
        meta = meta or {}
        distance_value = float(distance)
        results.append(
            {
                "id": chunk_id,
                "text": doc,
                "document": doc,  # alias kept for older call sites / tests
                "source": meta.get("source", "unknown"),
                "doc_id": meta.get("docId"),
                "category": meta.get("category"),
                "chunk_index": meta.get("chunkIndex"),
                "distance": distance_value,
                "score": 1.0 - distance_value,
                "metadata": meta,
            }
        )
    return results


def filter_by_relevance(
    results: list[dict[str, Any]],
    *,
    max_distance: float | None = None,
    cfg: Settings | None = None,
) -> list[dict[str, Any]]:
    """
    Keep only results within the cosine-distance threshold.

    Cosine space: lower distance means a closer match.
    """
    from app.rag.guardrails import select_relevant_chunks

    cfg = cfg or settings
    threshold = cfg.relevance_max_distance if max_distance is None else max_distance
    return select_relevant_chunks(results, max_distance=threshold)


def format_results(results: list[dict[str, Any]]) -> str:
    """Pretty-print retrieval results for manual inspection."""
    if not results:
        return "No results."

    lines: list[str] = []
    for i, item in enumerate(results, start=1):
        preview = (item.get("text") or item.get("document") or "").replace("\n", " ")
        if len(preview) > 220:
            preview = preview[:217] + "..."
        lines.append(
            f"{i}. source={item['source']}  category={item.get('category')}  "
            f"docId={item.get('doc_id')}  "
            f"distance={item['distance']:.4f}  score={item['score']:.4f}\n"
            f"   {preview}"
        )
    return "\n".join(lines)


def main() -> None:
    demo_queries = [
        "Why are users receiving a 504 error?",
        "The application cannot connect to the database.",
        "A user cannot authenticate after a password reset.",
    ]
    for query in demo_queries:
        print("=" * 80)
        print(f"Query: {query}")
        print("-" * 80)
        hits = retrieve(query)
        print(format_results(hits))
        relevant = filter_by_relevance(hits)
        print(
            f"Relevant under threshold "
            f"(max_distance={settings.relevance_max_distance}): {len(relevant)}/{len(hits)}"
        )
        print()


if __name__ == "__main__":
    main()
