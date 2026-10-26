"""Knowledge-base tool for Gemini function calling.

Gemini never executes this Python function itself. The model only emits a
function-call request; our application runs `search_technical_documentation`
against ChromaDB and sends the structured result back to Gemini.
"""

from __future__ import annotations

import logging
from typing import Any

from google.genai import types

from app.config import Settings, settings
from app.retrieval.retrieval import filter_by_relevance, retrieve

logger = logging.getLogger(__name__)

SEARCH_TECHNICAL_DOCUMENTATION_NAME = "search_technical_documentation"

SEARCH_TECHNICAL_DOCUMENTATION_DECLARATION: dict[str, Any] = {
    "name": SEARCH_TECHNICAL_DOCUMENTATION_NAME,
    "description": (
        "Search the technical support knowledge base (ChromaDB) for documentation "
        "chunks relevant to a troubleshooting query. Use this when you need "
        "evidence such as known causes, symptoms, or remediation steps before "
        "recommending actions. Returns plain chunk text, source filenames, "
        "categories, and cosine distances (lower distance = closer match)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language search query derived from the support ticket, "
                    "e.g. 'HTTP 504 gateway timeout upstream' or "
                    "'database connection timeout'."
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of chunks to return (default 5).",
            },
        },
        "required": ["query"],
    },
}


def search_technical_documentation(
    query: str,
    top_k: int | None = None,
    *,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    """
    Search ChromaDB and return structured retrieval results for the agent.

    This is the Python function Gemini may *request*. The application executes
    it and returns the payload as a function response.
    """
    cfg = cfg or settings
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return {
            "query": query,
            "result_count": 0,
            "relevant_count": 0,
            "results": [],
            "error": "query must be a non-empty string",
        }

    try:
        hits = retrieve(cleaned_query, top_k=top_k, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
        logger.exception("Knowledge-base search failed for query=%r", cleaned_query)
        return {
            "query": cleaned_query,
            "result_count": 0,
            "relevant_count": 0,
            "results": [],
            "error": str(exc),
        }

    relevant = filter_by_relevance(hits, cfg=cfg)
    structured = [_to_tool_result(item) for item in hits]

    payload = {
        "query": cleaned_query,
        "result_count": len(structured),
        "relevant_count": len(relevant),
        "relevance_max_distance": cfg.relevance_max_distance,
        "distance_metric": "cosine",
        "distance_note": "Lower distance means a closer / more relevant match.",
        "results": structured,
    }
    logger.info(
        "search_technical_documentation query=%r results=%s relevant=%s",
        cleaned_query,
        payload["result_count"],
        payload["relevant_count"],
    )
    return payload


def _to_tool_result(item: dict[str, Any]) -> dict[str, Any]:
    text = item.get("text") or item.get("document") or ""
    return {
        "text": text,
        "source": item.get("source"),
        "doc_id": item.get("doc_id"),
        "chunk_index": item.get("chunk_index"),
        "category": item.get("category"),
        "distance": item.get("distance"),
        "score": item.get("score"),
    }


def get_knowledge_base_tool() -> types.Tool:
    """Build the Tool declaration exposed to Gemini."""
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(**SEARCH_TECHNICAL_DOCUMENTATION_DECLARATION)
        ]
    )


def execute_tool_call(
    name: str,
    args: dict[str, Any] | None,
    *,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    """
    Dispatch a Gemini function-call request to the matching Python implementation.

    Gemini only provides `name` and `args`; this function performs the real work.
    """
    args = dict(args or {})
    if name == SEARCH_TECHNICAL_DOCUMENTATION_NAME:
        query = str(args.get("query", ""))
        top_k = args.get("top_k")
        return search_technical_documentation(
            query=query,
            top_k=int(top_k) if top_k is not None else None,
            cfg=cfg,
        )

    return {
        "error": f"Unknown tool: {name}",
        "result_count": 0,
        "results": [],
    }
