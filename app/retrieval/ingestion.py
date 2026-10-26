"""Document ingestion pipeline: load, chunk, embed, and store in ChromaDB.

Best practices applied:
- Plain chunk text in `documents`; structured fields in `metadatas`.
- Explicit Gemini embeddings (batched, not one-by-one).
- Stable chunk IDs (`{docId}_chunk_{index}`) with upsert + obsolete cleanup.
- Content hash recorded for repeatable re-ingestion.
- Local persistent ChromaDB via the Python client.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from google import genai
from google.genai import types

from app.config import Settings, settings

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown"}

CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("networking", ("dns", "network", "vpn", "ip_configuration", "ip-")),
    ("web", ("http", "504", "502", "503", "gateway", "web_server")),
    ("database", ("database", "db_", "sql", "postgres", "mysql")),
    ("authentication", ("auth", "password", "session", "sso", "login", "account")),
    ("storage", ("upload", "multipart", "storage", "file_")),
]


# One client per API key for the whole process. Building a client takes about 1.5 s and
# each new client opens a fresh connection; the client holds no ticket or user data,
# and its HTTP connection pool is safe to share across request threads.
_GENAI_CLIENTS: dict[str, genai.Client] = {}
_GENAI_CLIENTS_LOCK = threading.Lock()


def get_genai_client(api_key: str | None = None) -> genai.Client:
    key = api_key or settings.google_api_key
    if not key:
        raise ValueError(
            "GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    with _GENAI_CLIENTS_LOCK:
        client = _GENAI_CLIENTS.get(key)
        if client is None:
            # Without an HTTP timeout a hung request would hold its worker thread
            # forever after the coordinator has already given up on the stage.
            timeout_ms = int(settings.model_call_timeout_seconds * 1000)
            client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
            _GENAI_CLIENTS[key] = client
        return client


def get_chroma_client(cfg: Settings | None = None) -> chromadb.PersistentClient:
    cfg = cfg or settings
    cfg.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(cfg.chroma_dir))


def get_chroma_collection(cfg: Settings | None = None) -> Collection:
    """Local persistent collection using cosine distance."""
    cfg = cfg or settings
    client = get_chroma_client(cfg)
    return client.get_or_create_collection(
        name=cfg.chroma_collection,
        metadata={"hnsw:space": "cosine"},
    )


def infer_category(source: str) -> str:
    lowered = source.lower()
    for category, needles in CATEGORY_RULES:
        if any(needle in lowered for needle in needles):
            return category
    return "general"


def make_doc_id(source: str) -> str:
    """Stable document id derived from the filename (without extension)."""
    stem = Path(source).stem
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    return slug or hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_chunk_id(doc_id: str, chunk_index: int) -> str:
    """Stable chunk id so re-ingestion can upsert the same logical chunk."""
    return f"{doc_id}_chunk_{chunk_index}"


def load_documents(docs_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read Markdown/text files from the technical docs directory."""
    docs_dir = docs_dir or settings.docs_dir
    if not docs_dir.exists():
        raise FileNotFoundError(f"Documents directory not found: {docs_dir}")

    documents: list[dict[str, Any]] = []
    for path in sorted(docs_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        source = path.name
        documents.append(
            {
                "source": source,
                "path": str(path.relative_to(docs_dir)).replace("\\", "/"),
                "doc_id": make_doc_id(source),
                "category": infer_category(source),
                "text": text,
                "content_hash": content_hash(text),
            }
        )
    return documents


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """
    Split text into overlapping character windows (~400–600 tokens).

    Defaults: chunk_size_chars≈2000 (~500 tokens), overlap≈200 (~50 tokens).
    Prefer paragraph / sentence boundaries when possible.
    """
    chunk_size = chunk_size or settings.chunk_size_chars
    overlap = overlap if overlap is not None else settings.chunk_overlap_chars
    if overlap >= chunk_size:
        raise ValueError("chunk overlap must be smaller than chunk size")

    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            window = text[start:end]
            break_at = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
            if break_at > chunk_size * 0.4:
                end = start + break_at + (1 if window[break_at] == "." else 0)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def embed_texts(
    texts: list[str],
    *,
    task_type: str,
    client: genai.Client | None = None,
    cfg: Settings | None = None,
) -> list[list[float]]:
    """
    Generate Gemini embeddings with controlled batching.

    Uses gemini-embedding-001's multi-string embed_content so each batch
    returns one embedding per input. Batches stay small to respect rate limits.
    """
    cfg = cfg or settings
    client = client or get_genai_client()
    if not texts:
        return []

    batch_size = max(1, cfg.embed_batch_size)
    embeddings: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        # Count the request only when a ticket analysis is in progress.
        from app.usage import note_embed_api_call

        note_embed_api_call()
        result = client.models.embed_content(
            model=cfg.embedding_model,
            contents=batch,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=cfg.embedding_dimensionality,
            ),
        )
        if not result.embeddings or len(result.embeddings) != len(batch):
            raise RuntimeError(
                f"Expected {len(batch)} embeddings, got "
                f"{0 if not result.embeddings else len(result.embeddings)}"
            )
        embeddings.extend(list(item.values) for item in result.embeddings)
        logger.info(
            "Embedded batch %s–%s / %s (%s)",
            start + 1,
            start + len(batch),
            len(texts),
            task_type,
        )

    return embeddings


def _existing_ids_for_doc(collection: Collection, doc_id: str) -> list[str]:
    """Fetch current chunk ids for a document (for obsolete-chunk cleanup)."""
    try:
        existing = collection.get(where={"docId": doc_id}, include=[])
    except Exception:
        # Older collections may not have docId yet.
        return []
    return list(existing.get("ids") or [])


def _build_chunk_records(doc: dict[str, Any], cfg: Settings) -> dict[str, list]:
    chunks = chunk_text(doc["text"], cfg.chunk_size_chars, cfg.chunk_overlap_chars)
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for index, chunk in enumerate(chunks):
        ids.append(make_chunk_id(doc["doc_id"], index))
        documents.append(chunk)  # plain text only — never JSON-wrap the chunk
        metadatas.append(
            {
                "source": doc["source"],
                "docId": doc["doc_id"],
                "chunkIndex": index,
                "category": doc["category"],
                "path": doc["path"],
                "contentHash": doc["content_hash"],
                "totalChunks": len(chunks),
            }
        )

    return {
        "ids": ids,
        "documents": documents,
        "metadatas": metadatas,
        "chunk_count": len(chunks),
    }


def _upsert_in_batches(
    collection: Collection,
    *,
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict[str, Any]],
    batch_size: int,
) -> None:
    size = max(1, batch_size)
    for start in range(0, len(ids), size):
        end = start + size
        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            embeddings=embeddings[start:end],
            metadatas=metadatas[start:end],
        )
        logger.info("Upserted chunks %s–%s / %s", start + 1, min(end, len(ids)), len(ids))


def ingest_document(
    doc: dict[str, Any],
    *,
    collection: Collection | None = None,
    client: genai.Client | None = None,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    """
    Idempotently ingest one document:
    1. Build stable chunk IDs from docId + chunkIndex.
    2. Embed and upsert the new/updated chunks.
    3. Delete obsolete chunks left over from a previous longer version.
    """
    cfg = cfg or settings
    collection = collection or get_chroma_collection(cfg)
    client = client or get_genai_client(cfg.google_api_key)

    records = _build_chunk_records(doc, cfg)
    if records["chunk_count"] == 0:
        return {
            "doc_id": doc["doc_id"],
            "source": doc["source"],
            "chunks": 0,
            "deleted_obsolete": 0,
            "skipped": True,
        }

    embeddings = embed_texts(
        records["documents"],
        task_type="RETRIEVAL_DOCUMENT",
        client=client,
        cfg=cfg,
    )

    previous_ids = set(_existing_ids_for_doc(collection, doc["doc_id"]))
    new_ids = set(records["ids"])
    obsolete_ids = sorted(previous_ids - new_ids)

    _upsert_in_batches(
        collection,
        ids=records["ids"],
        documents=records["documents"],
        embeddings=embeddings,
        metadatas=records["metadatas"],
        batch_size=cfg.chroma_upsert_batch_size,
    )

    if obsolete_ids:
        collection.delete(ids=obsolete_ids)
        logger.info(
            "Removed %s obsolete chunks for docId=%s",
            len(obsolete_ids),
            doc["doc_id"],
        )

    return {
        "doc_id": doc["doc_id"],
        "source": doc["source"],
        "category": doc["category"],
        "chunks": records["chunk_count"],
        "deleted_obsolete": len(obsolete_ids),
        "content_hash": doc["content_hash"],
        "skipped": False,
    }


def ingest_documents(
    *,
    docs_dir: Path | None = None,
    reset: bool = False,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    """Ingest all technical documents into the persistent ChromaDB collection."""
    cfg = cfg or settings
    documents = load_documents(docs_dir or cfg.docs_dir)
    if not documents:
        raise ValueError(f"No .md/.txt documents found in {cfg.docs_dir}")

    client = get_genai_client(cfg.google_api_key)

    if reset:
        chroma = get_chroma_client(cfg)
        try:
            chroma.delete_collection(cfg.chroma_collection)
            logger.info("Deleted existing collection %s", cfg.chroma_collection)
        except Exception:
            pass

    collection = get_chroma_collection(cfg)
    per_doc: list[dict[str, Any]] = []
    total_chunks = 0
    total_deleted = 0

    for index, doc in enumerate(documents, start=1):
        logger.info(
            "Ingesting %s/%s source=%s docId=%s category=%s",
            index,
            len(documents),
            doc["source"],
            doc["doc_id"],
            doc["category"],
        )
        result = ingest_document(
            doc,
            collection=collection,
            client=client,
            cfg=cfg,
        )
        per_doc.append(result)
        total_chunks += int(result["chunks"])
        total_deleted += int(result["deleted_obsolete"])

    summary = {
        "documents": len(documents),
        "chunks": total_chunks,
        "deleted_obsolete": total_deleted,
        "collection": cfg.chroma_collection,
        "chroma_dir": str(cfg.chroma_dir),
        "distance_metric": "cosine",
        "sources": [doc["source"] for doc in documents],
        "per_document": per_doc,
    }
    logger.info(
        "Ingestion complete: documents=%s chunks=%s obsolete_removed=%s",
        summary["documents"],
        summary["chunks"],
        summary["deleted_obsolete"],
    )
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = ingest_documents(reset=False)
    print("Ingestion complete:")
    for key in ("documents", "chunks", "deleted_obsolete", "collection", "chroma_dir"):
        print(f"  {key}: {summary[key]}")
    print("  sources:")
    for source in summary["sources"]:
        print(f"    - {source}")


if __name__ == "__main__":
    main()
