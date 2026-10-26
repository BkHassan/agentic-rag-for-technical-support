"""Shared retrieval fixtures: unrelated documents at typical background distances."""

from __future__ import annotations

from typing import Any


def background_docs(start: float = 0.42, step: float = 0.01, count: int = 4) -> list[dict[str, Any]]:
    """Distinct unrelated documents that a real top-k search returns behind the best match."""
    names = [
        "vpn_and_remote_access",
        "http_502_bad_gateway",
        "file_upload_failures",
        "dns_resolution_failures",
        "http_503_service_unavailable",
    ]
    return [
        {
            "text": f"Unrelated background documentation ({name}).",
            "source": f"{name}.md",
            "doc_id": name,
            "chunk_index": 0,
            "category": "other",
            "distance": round(start + index * step, 4),
            "score": round(1 - (start + index * step), 4),
        }
        for index, name in enumerate(names[:count])
    ]
