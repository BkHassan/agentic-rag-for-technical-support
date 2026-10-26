"""Tests for ChromaDB retrieval over ingested technical documentation."""

from __future__ import annotations

import os

import pytest

from app.retrieval.retrieval import filter_by_relevance, retrieve

REQUIRES_API_KEY = pytest.mark.skipif(
    not os.getenv("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY is required for live embedding/retrieval tests",
)


def test_filter_by_relevance_uses_distance_threshold() -> None:
    results = [
        {"text": "good", "distance": 0.20, "source": "a.md"},
        {"text": "weak", "distance": 0.80, "source": "b.md"},
    ]
    kept = filter_by_relevance(results, max_distance=0.45)
    assert len(kept) == 1
    assert kept[0]["text"] == "good"


@REQUIRES_API_KEY
@pytest.mark.parametrize(
    ("query", "expected_source_substring"),
    [
        ("Why are users receiving a 504 error?", "504"),
        ("The application cannot connect to the database.", "database"),
        ("A user cannot authenticate after a password reset.", "password"),
    ],
)
def test_retrieve_returns_relevant_source(query: str, expected_source_substring: str) -> None:
    results = retrieve(query, top_k=3)

    assert results, "expected at least one retrieval result"
    assert all(
        "source" in item and "distance" in item and ("text" in item or "document" in item)
        for item in results
    )

    top_sources = " ".join(item["source"].lower() for item in results)
    top_text = " ".join((item.get("text") or item.get("document") or "").lower() for item in results)
    combined = f"{top_sources} {top_text}"

    assert expected_source_substring.lower() in combined, (
        f"Expected evidence related to '{expected_source_substring}' for query '{query}'. "
        f"Got sources: {[item['source'] for item in results]}"
    )


@REQUIRES_API_KEY
def test_retrieve_includes_structured_metadata_and_distances() -> None:
    results = retrieve("DNS resolution failure hostname", top_k=2)
    assert results
    first = results[0]
    assert isinstance(first["distance"], float)
    assert isinstance(first["score"], float)
    assert first["metadata"].get("source")
    assert first["metadata"].get("docId")
    assert first["metadata"].get("category")
    assert "chunkIndex" in first["metadata"]
    assert first["source"] == first["metadata"]["source"]
    assert first["text"]  # plain text, not JSON-wrapped
    assert not str(first["text"]).lstrip().startswith("{")
