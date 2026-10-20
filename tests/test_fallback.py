"""Safe-fallback scenarios with mocked retrieval (no Gemini / Chroma dependency)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agent import generate_grounded_recommendations
from app.rag.guardrails import (
    FALLBACK_MESSAGE,
    is_relevant,
    should_return_fallback,
    validate_retrieval_evidence,
)
from app.schemas import GroundedAdvice, SupportTicket, TicketCategory

# Relevant database documentation (cosine distance below threshold).
RELEVANT_DB_DOCS = [
    {
        "text": (
            "Database connection failures: verify credentials, test connectivity "
            "with a native client, and check pool exhaustion."
        ),
        "source": "database_connection_failures.md",
        "doc_id": "database_connection_failures",
        "chunk_index": 0,
        "category": "database",
        "distance": 0.18,
        "score": 0.82,
    }
]

# Nearest neighbors that are still irrelevant (high cosine distance).
IRRELEVANT_DOCS = [
    {
        "text": "How to rotate VPN certificates for remote contractors.",
        "source": "vpn_and_remote_access.md",
        "doc_id": "vpn_and_remote_access",
        "chunk_index": 0,
        "category": "networking",
        "distance": 0.91,
        "score": 0.09,
    }
]


def test_is_relevant_and_empty_checks() -> None:
    assert is_relevant([]) is False
    assert is_relevant(None) is False
    assert is_relevant(RELEVANT_DB_DOCS) is True
    assert is_relevant(IRRELEVANT_DOCS) is False
    assert should_return_fallback([], retrieval_performed=True) is True
    assert should_return_fallback(IRRELEVANT_DOCS, retrieval_performed=True) is True
    assert should_return_fallback(RELEVANT_DB_DOCS, retrieval_performed=True) is False


def test_relevant_documentation_generates_grounded_recommendations() -> None:
    """Relevant database documentation is retrieved → grounded recommendations."""
    ticket = SupportTicket(
        ticket_id="T-DB-OK",
        description="Users cannot connect because the database connection keeps timing out.",
    )
    advice = GroundedAdvice(
        recommendations=[
            "Verify database credentials against current secrets.",
            "Test connectivity from the app host with a native DB client.",
            "Check connection pool size versus active connections.",
        ],
        cited_sources=["database_connection_failures.md"],
        summary=(
            "Documented steps cover credentials, connectivity, and pools. "
            "Uncertainty: exact root cause not yet confirmed."
        ),
    )
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = SimpleNamespace(
        parsed=advice,
        text=None,
    )

    result = generate_grounded_recommendations(
        ticket,
        category=TicketCategory.DATABASE,
        evidence=RELEVANT_DB_DOCS,
        retrieval_performed=True,
        client=fake_client,
    )

    assert result.fallback_triggered is False
    assert result.sufficient_evidence is True
    assert result.summary != FALLBACK_MESSAGE
    assert any("pool" in item.lower() or "credential" in item.lower() for item in result.recommendations)
    assert result.sources[0].source == "database_connection_failures.md"
    fake_client.models.generate_content.assert_called_once()


def test_no_documents_retrieved_returns_fixed_fallback() -> None:
    """No documents are retrieved → application returns the fixed fallback."""
    ticket = SupportTicket(
        ticket_id="T-EMPTY",
        description="Users cannot connect because the database connection keeps timing out.",
    )
    fake_client = MagicMock()

    result = generate_grounded_recommendations(
        ticket,
        category=TicketCategory.DATABASE,
        evidence=[],
        retrieval_performed=True,
        client=fake_client,
    )

    assert result.fallback_triggered is True
    assert result.sufficient_evidence is False
    assert result.recommendations == [FALLBACK_MESSAGE]
    assert result.summary == FALLBACK_MESSAGE
    fake_client.models.generate_content.assert_not_called()


def test_irrelevant_documents_retrieved_returns_fixed_fallback() -> None:
    """Documents are retrieved but are irrelevant → application returns the fixed fallback."""
    ticket = SupportTicket(
        ticket_id="T-IRREL",
        description="Users cannot connect because the database connection keeps timing out.",
    )
    fake_client = MagicMock()

    assert is_relevant(IRRELEVANT_DOCS) is False
    validation = validate_retrieval_evidence(
        IRRELEVANT_DOCS,
        retrieval_performed=True,
    )
    assert validation.sufficient_evidence is False

    result = generate_grounded_recommendations(
        ticket,
        category=TicketCategory.DATABASE,
        evidence=IRRELEVANT_DOCS,
        retrieval_performed=True,
        client=fake_client,
        validation=validation,
    )

    assert result.fallback_triggered is True
    assert result.sufficient_evidence is False
    assert result.recommendations == [FALLBACK_MESSAGE]
    assert result.summary == FALLBACK_MESSAGE
    fake_client.models.generate_content.assert_not_called()
