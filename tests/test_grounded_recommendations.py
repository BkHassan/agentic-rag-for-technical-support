"""Tests for evidence-grounded recommendation generation (no live API required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agent import (
    assess_evidence,
    generate_grounded_recommendations,
    handle_support_ticket,
)
from app.rag.guardrails import FALLBACK_MESSAGE, validate_retrieval_evidence
from app.schemas import GroundedAdvice, SupportTicket, TicketCategory, TicketClassification


DB_EVIDENCE = [
    {
        "text": (
            "Common causes include wrong credentials, pool exhaustion, and network "
            "blocks on the DB port. Test connectivity with a native client and check "
            "pool size versus active connections."
        ),
        "source": "database_connection_failures.md",
        "doc_id": "database_connection_failures",
        "chunk_index": 0,
        "category": "database",
        "distance": 0.18,
        "score": 0.82,
    }
]


def test_assess_evidence_requires_relevant_distance() -> None:
    weak = [{"source": "a.md", "distance": 0.90, "text": "unrelated"}]
    strong = [{"source": "a.md", "distance": 0.20, "text": "relevant"}]
    assert assess_evidence(weak, retrieval_performed=True)["sufficient_evidence"] is False
    assert assess_evidence(strong, retrieval_performed=True)["sufficient_evidence"] is True
    assert validate_retrieval_evidence(strong, retrieval_performed=False).sufficient_evidence is False


def test_generate_grounded_recommendations_uses_evidence_not_filename_only() -> None:
    ticket = SupportTicket(
        ticket_id="T-DB-001",
        description="Users cannot connect because the database connection keeps timing out.",
    )
    advice = GroundedAdvice(
        recommendations=[
            "Verify the database connection string and credentials against current secrets.",
            "Check connection pool size versus active connections for exhaustion.",
            "Test connectivity from the app host with a native DB client.",
        ],
        cited_sources=["database_connection_failures.md"],
        summary=(
            "Documented diagnostics focus on credentials, pool exhaustion, and "
            "connectivity checks. Root cause is not yet confirmed."
        ),
    )
    fake_response = SimpleNamespace(parsed=advice, text=None)
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response

    result = generate_grounded_recommendations(
        ticket,
        category=TicketCategory.DATABASE,
        evidence=DB_EVIDENCE,
        retrieval_performed=True,
        client=fake_client,
    )

    assert result.sufficient_evidence is True
    assert result.fallback_triggered is False
    assert result.category == TicketCategory.DATABASE
    assert any("pool" in item.lower() for item in result.recommendations)
    assert result.sources
    assert result.sources[0].source == "database_connection_failures.md"
    assert "uncertain" in result.summary.lower() or "not yet" in result.summary.lower()


def test_generate_grounded_recommendations_triggers_fallback_without_evidence() -> None:
    ticket = SupportTicket(
        ticket_id="T-X-001",
        description="Something vague is broken in production.",
    )
    result = generate_grounded_recommendations(
        ticket,
        category=TicketCategory.OTHER,
        evidence=[],
        retrieval_performed=True,
        client=MagicMock(),
    )

    assert result.sufficient_evidence is False
    assert result.fallback_triggered is True
    assert result.recommendations == [FALLBACK_MESSAGE]
    assert result.summary == FALLBACK_MESSAGE


def test_generate_drops_hallucinated_citations() -> None:
    ticket = SupportTicket(ticket_id="T-1", description="DB timeout")
    advice = GroundedAdvice(
        recommendations=["Check pool exhaustion using documented steps."],
        cited_sources=["totally_fake_source.md", "database_connection_failures.md"],
        summary="Documented: pool checks. Uncertainty: exact root cause.",
    )
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = SimpleNamespace(
        parsed=advice, text=None
    )

    result = generate_grounded_recommendations(
        ticket,
        category=TicketCategory.DATABASE,
        evidence=DB_EVIDENCE,
        retrieval_performed=True,
        client=fake_client,
    )
    cited = {src.source for src in result.sources}
    assert "totally_fake_source.md" not in cited
    assert "database_connection_failures.md" in cited


def test_handle_support_ticket_orchestrates_classify_tools_and_grounding() -> None:
    ticket = SupportTicket(
        ticket_id="T-DB-001",
        description="Database connection keeps timing out.",
    )
    classification = TicketClassification(
        category=TicketCategory.DATABASE,
        rationale="Mentions database connection timeouts.",
    )
    grounded = GroundedAdvice(
        recommendations=["Test DB connectivity with a native client."],
        cited_sources=["database_connection_failures.md"],
        summary="Documented connectivity checks apply. Uncertainty remains on root cause.",
    )
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = SimpleNamespace(
        parsed=grounded, text=None
    )

    with (
        patch("app.agent.classify_ticket", return_value=classification),
        patch(
            "app.agent.run_tool_calling_turn",
            return_value={
                "evidence": DB_EVIDENCE,
                "tool_calls": [{"name": "search_technical_documentation"}],
                "used_knowledge_base": True,
                "response_text": "",
            },
        ),
        patch(
            "app.agent.collect_evidence_for_ticket",
            return_value=(DB_EVIDENCE, True, "gemini_tool"),
        ),
    ):
        result = handle_support_ticket(ticket, client=fake_client)

    assert result.ticket_id == "T-DB-001"
    assert result.category == TicketCategory.DATABASE
    assert result.sufficient_evidence is True
    assert result.fallback_triggered is False
    assert result.sources[0].source == "database_connection_failures.md"
