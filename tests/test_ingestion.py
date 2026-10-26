"""Unit tests for ingestion helpers (no API key required)."""

from __future__ import annotations

from app.retrieval.ingestion import (
    chunk_text,
    content_hash,
    infer_category,
    make_chunk_id,
    make_doc_id,
)


def test_make_doc_id_is_stable_slug() -> None:
    assert make_doc_id("HTTP_504_Gateway_Timeout.md") == "http_504_gateway_timeout"
    assert make_doc_id("password_reset_and_account_access.md") == (
        "password_reset_and_account_access"
    )


def test_make_chunk_id_is_stable_and_index_based() -> None:
    assert make_chunk_id("http_504_gateway_timeout", 0) == "http_504_gateway_timeout_chunk_0"
    assert make_chunk_id("http_504_gateway_timeout", 11) == "http_504_gateway_timeout_chunk_11"


def test_infer_category_from_filename() -> None:
    assert infer_category("dns_resolution_failures.md") == "networking"
    assert infer_category("http_504_gateway_timeout.md") == "web"
    assert infer_category("database_connection_failures.md") == "database"
    assert infer_category("password_reset_and_account_access.md") == "authentication"
    assert infer_category("file_upload_failures.md") == "storage"


def test_content_hash_changes_with_text() -> None:
    assert content_hash("alpha") != content_hash("beta")
    assert content_hash("same") == content_hash("same")


def test_chunk_text_respects_size_and_overlap() -> None:
    text = ("Paragraph one. " * 40) + "\n\n" + ("Paragraph two. " * 40)
    chunks = chunk_text(text, chunk_size=200, overlap=40)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 220 for chunk in chunks)  # allow boundary slack
    # Overlap should create shared words between adjacent chunks.
    assert any(
        token in chunks[1]
        for token in chunks[0].split()[-5:]
    )


def test_obsolete_chunk_id_set_difference() -> None:
    """Simulate shrinking a doc from 3 chunks to 2 — chunk_2 becomes obsolete."""
    previous = {
        make_chunk_id("demo_doc", 0),
        make_chunk_id("demo_doc", 1),
        make_chunk_id("demo_doc", 2),
    }
    current = {
        make_chunk_id("demo_doc", 0),
        make_chunk_id("demo_doc", 1),
    }
    obsolete = sorted(previous - current)
    assert obsolete == ["demo_doc_chunk_2"]
