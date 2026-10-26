"""Query embeddings for the same ticket text are requested from Gemini once."""

from __future__ import annotations

from unittest.mock import patch

from app.config import Settings
from app.rag.retrieval import clear_query_embed_cache, embed_queries

CFG = Settings(google_api_key="test", embedding_model="embed-test", embedding_dimensionality=4)


def test_identical_query_text_is_embedded_once() -> None:
    clear_query_embed_cache()
    with patch("app.rag.retrieval.embed_texts", return_value=[[0.1, 0.2, 0.3, 0.4]]) as embed:
        first = embed_queries(["database timeout"], client=None, cfg=CFG)
        second = embed_queries(["database timeout"], client=None, cfg=CFG)

    embed.assert_called_once()
    assert first == second == [[0.1, 0.2, 0.3, 0.4]]
