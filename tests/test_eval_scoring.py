"""Offline tests for evaluation scoring (no Gemini / Chroma required)."""

from __future__ import annotations

from app.rag.guardrails import FALLBACK_MESSAGE
from evals.scoring import (
    score_answer_grounding,
    score_fallback,
    score_ticket,
    score_tool_selection,
)


def test_tool_selection_requests_when_appropriate() -> None:
    assert score_tool_selection(
        retrieval_appropriate=True,
        gemini_requested_retrieval=True,
    )["passed"]
    assert not score_tool_selection(
        retrieval_appropriate=True,
        gemini_requested_retrieval=False,
    )["passed"]


def test_tool_selection_avoids_unnecessary_calls() -> None:
    assert score_tool_selection(
        retrieval_appropriate=False,
        gemini_requested_retrieval=False,
    )["passed"]
    assert not score_tool_selection(
        retrieval_appropriate=False,
        gemini_requested_retrieval=True,
    )["passed"]


def test_fallback_when_evidence_insufficient() -> None:
    result = score_fallback(
        fallback_expected=True,
        fallback_triggered=True,
        sufficient_evidence=False,
        recommendations=[FALLBACK_MESSAGE],
    )
    assert result["passed"]


def test_fallback_rejects_invented_recommendations() -> None:
    result = score_fallback(
        fallback_expected=True,
        fallback_triggered=False,
        sufficient_evidence=True,
        recommendations=["Restart Kafka brokers and rebalance partitions."],
    )
    assert not result["passed"]


def test_grounded_recommendations_when_evidence_adequate() -> None:
    result = score_fallback(
        fallback_expected=False,
        fallback_triggered=False,
        sufficient_evidence=True,
        recommendations=["Verify DB credentials against secrets."],
    )
    assert result["passed"]


def test_answer_grounding_requires_expected_source_overlap() -> None:
    ok = score_answer_grounding(
        fallback_expected=False,
        fallback_triggered=False,
        cited_sources=["database_connection_failures.md"],
        expected_supporting_documents=["database_connection_failures.md"],
        evidence_sources=["database_connection_failures.md"],
        recommendations=["Check connection pool size."],
    )
    assert ok["passed"]

    bad = score_answer_grounding(
        fallback_expected=False,
        fallback_triggered=False,
        cited_sources=["vpn_and_remote_access.md"],
        expected_supporting_documents=["database_connection_failures.md"],
        evidence_sources=["vpn_and_remote_access.md"],
        recommendations=["Check VPN routes."],
    )
    assert not bad["passed"]


def test_score_ticket_aggregates_three_behaviors() -> None:
    ticket = {
        "ticket_id": "E-REL-001",
        "group": "relevant_documentation",
        "expected_category": "database",
        "retrieval_appropriate": True,
        "expected_supporting_documents": ["database_connection_failures.md"],
        "fallback_expected": False,
    }
    trace = {
        "gemini_requested_retrieval": True,
        "evidence": [{"source": "database_connection_failures.md"}],
        "latency_ms": 12.0,
        "error": None,
        "response": {
            "category": "database",
            "recommendations": ["Verify credentials and pool usage."],
            "sources": [{"source": "database_connection_failures.md"}],
            "sufficient_evidence": True,
            "fallback_triggered": False,
        },
    }
    scored = score_ticket(ticket, trace)
    assert scored["passed"]
    assert scored["checks"]["tool_selection"]["passed"]
    assert scored["checks"]["answer_grounding"]["passed"]
    assert scored["checks"]["fallback"]["passed"]
