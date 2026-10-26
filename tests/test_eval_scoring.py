"""Offline tests for evaluation scoring (no Gemini / Chroma required)."""

from __future__ import annotations

from app.rag.guardrails import FALLBACK_MESSAGE
from app.schemas import AgentOutcome
from evals.scoring import (
    expected_outcome_of,
    observed_outcome_of,
    score_answer_grounding,
    score_outcome,
    score_ticket,
    score_tool_selection,
)

QUESTIONS = ["Which system or feature is affected?", "What exactly happens?"]


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
    result = score_outcome(
        expected_outcome="fallback",
        observed_outcome="fallback",
        sufficient_evidence=False,
        recommendations=[FALLBACK_MESSAGE],
        clarifying_questions=[],
    )
    assert result["passed"]


def test_fallback_rejects_invented_recommendations() -> None:
    result = score_outcome(
        expected_outcome="fallback",
        observed_outcome="grounded_answer",
        sufficient_evidence=True,
        recommendations=["Restart Kafka brokers and rebalance partitions."],
        clarifying_questions=[],
    )
    assert not result["passed"]
    assert result["detail"] == "Expected fallback but returned grounded_answer"


def test_grounded_recommendations_when_evidence_adequate() -> None:
    result = score_outcome(
        expected_outcome="grounded_answer",
        observed_outcome="grounded_answer",
        sufficient_evidence=True,
        recommendations=["Verify DB credentials against secrets."],
        clarifying_questions=[],
    )
    assert result["passed"]


def test_clarification_passes_only_with_questions_and_no_recommendations() -> None:
    assert score_outcome(
        expected_outcome="clarification_needed",
        observed_outcome="clarification_needed",
        sufficient_evidence=False,
        recommendations=[],
        clarifying_questions=QUESTIONS,
    )["passed"]
    assert not score_outcome(
        expected_outcome="clarification_needed",
        observed_outcome="clarification_needed",
        sufficient_evidence=False,
        recommendations=[],
        clarifying_questions=[],
    )["passed"]


def test_clarification_and_documentation_fallback_are_not_interchangeable() -> None:
    fallback_instead = score_outcome(
        expected_outcome="clarification_needed",
        observed_outcome="fallback",
        sufficient_evidence=False,
        recommendations=[FALLBACK_MESSAGE],
        clarifying_questions=[],
    )
    assert not fallback_instead["passed"]
    assert fallback_instead["detail"] == "Expected clarification_needed but returned fallback"

    clarification_instead = score_outcome(
        expected_outcome="fallback",
        observed_outcome="clarification_needed",
        sufficient_evidence=False,
        recommendations=[],
        clarifying_questions=QUESTIONS,
    )
    assert not clarification_instead["passed"]


def test_observed_outcome_accepts_enum_values() -> None:
    assert observed_outcome_of({"outcome": AgentOutcome.CLARIFICATION_NEEDED}) == "clarification_needed"
    assert observed_outcome_of({"fallback_triggered": True}) == "fallback"


def test_dataset_v1_boolean_maps_to_outcomes() -> None:
    assert expected_outcome_of({"fallback_expected": True}) == "fallback"
    assert expected_outcome_of({"fallback_expected": False}) == "grounded_answer"
    assert expected_outcome_of({"expected_outcome": "clarification_needed"}) == "clarification_needed"


def test_answer_grounding_requires_expected_source_overlap() -> None:
    ok = score_answer_grounding(
        expected_outcome="grounded_answer",
        observed_outcome="grounded_answer",
        cited_sources=["database_connection_failures.md"],
        expected_supporting_documents=["database_connection_failures.md"],
        evidence_sources=["database_connection_failures.md"],
        recommendations=["Check connection pool size."],
    )
    assert ok["passed"]

    bad = score_answer_grounding(
        expected_outcome="grounded_answer",
        observed_outcome="grounded_answer",
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
        "expected_outcome": "grounded_answer",
    }
    trace = {
        "gemini_requested_retrieval": True,
        "evidence": [{"source": "database_connection_failures.md"}],
        "latency_ms": 12.0,
        "error": None,
        "response": {
            "category": "database",
            "outcome": "grounded_answer",
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
    assert scored["checks"]["outcome"]["passed"]


def test_score_ticket_clarification_without_search() -> None:
    ticket = {
        "ticket_id": "E-AMB-003",
        "group": "ambiguous_or_underspecified",
        "retrieval_appropriate": False,
        "expected_supporting_documents": [],
        "expected_outcome": "clarification_needed",
    }
    trace = {
        "gemini_requested_retrieval": False,
        "evidence": [],
        "latency_ms": 5.0,
        "error": None,
        "response": {
            "category": "other",
            "outcome": "clarification_needed",
            "clarifying_questions": QUESTIONS,
            "recommendations": [],
            "sources": [],
            "sufficient_evidence": False,
            "fallback_triggered": False,
        },
    }
    scored = score_ticket(ticket, trace)
    assert scored["passed"]
    assert scored["checks"]["answer_grounding"]["skipped"] is True
    assert scored["observed_outcome"] == "clarification_needed"


def test_api_error_is_not_counted_as_fallback_success() -> None:
    ticket = {
        "ticket_id": "E-IRR-001",
        "group": "insufficient_or_irrelevant_evidence",
        "expected_category": "application",
        "retrieval_appropriate": True,
        "expected_supporting_documents": [],
        "expected_outcome": "fallback",
    }
    trace = {
        "gemini_requested_retrieval": False,
        "evidence": [],
        "latency_ms": 10.0,
        "error": "429 quota exceeded",
        "response": {
            "category": "other",
            "outcome": "fallback",
            "recommendations": ["Insufficient supporting documentation was found."],
            "sources": [],
            "sufficient_evidence": False,
            "fallback_triggered": True,
        },
    }
    scored = score_ticket(ticket, trace)
    assert scored["errored"] is True
    assert scored["passed"] is False
    assert scored["checks"]["outcome"]["skipped"] is True
