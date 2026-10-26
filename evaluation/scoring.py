"""Deterministic scoring helpers for tool selection, grounding, and the response outcome."""

from __future__ import annotations

from typing import Any

from app.policies.evidence import FALLBACK_MESSAGE

GROUNDED_ANSWER = "grounded_answer"
CLARIFICATION_NEEDED = "clarification_needed"
FALLBACK = "fallback"
OUTCOMES = (GROUNDED_ANSWER, CLARIFICATION_NEEDED, FALLBACK)


def score_tool_selection(
    *,
    retrieval_appropriate: bool,
    gemini_requested_retrieval: bool,
) -> dict[str, Any]:
    """
    Pass when Gemini requests retrieval iff the ticket expects it.

    - retrieval appropriate → tool call expected
    - retrieval not appropriate → tool call should be avoided
    """
    passed = retrieval_appropriate == gemini_requested_retrieval
    if retrieval_appropriate and gemini_requested_retrieval:
        detail = "Requested retrieval when appropriate"
    elif not retrieval_appropriate and not gemini_requested_retrieval:
        detail = "Avoided unnecessary retrieval"
    elif retrieval_appropriate and not gemini_requested_retrieval:
        detail = "Failed to request retrieval when documentation was needed"
    else:
        detail = "Requested retrieval when it was not needed"
    return {
        "metric": "tool_selection",
        "passed": passed,
        "detail": detail,
        "expected_retrieval": retrieval_appropriate,
        "observed_retrieval": gemini_requested_retrieval,
    }


def expected_outcome_of(ticket_spec: dict[str, Any]) -> str:
    """Expected outcome, accepting dataset 1.0's boolean `fallback_expected`."""
    if ticket_spec.get("expected_outcome"):
        return str(ticket_spec["expected_outcome"])
    return FALLBACK if ticket_spec.get("fallback_expected") else GROUNDED_ANSWER


def observed_outcome_of(response: dict[str, Any]) -> str:
    """Observed outcome, accepting responses recorded before `outcome` existed."""
    outcome = response.get("outcome")
    if outcome:
        return str(getattr(outcome, "value", outcome))
    return FALLBACK if response.get("fallback_triggered") else GROUNDED_ANSWER


def score_outcome(
    *,
    expected_outcome: str,
    observed_outcome: str,
    sufficient_evidence: bool,
    recommendations: list[str],
    clarifying_questions: list[str],
) -> dict[str, Any]:
    """
    Pass when the agent chose the expected outcome and its payload is consistent:

    - grounded_answer: sufficient evidence and real recommendations (not the fallback text)
    - clarification_needed: questions for the reporter and no recommendations
    - fallback: the fixed fallback message and no claim of sufficient evidence
    """
    recommendations = [item for item in recommendations if item and item.strip()]
    questions = [item for item in clarifying_questions if item and item.strip()]
    used_fallback_text = any(item.strip() == FALLBACK_MESSAGE for item in recommendations)

    if expected_outcome == GROUNDED_ANSWER:
        consistent = sufficient_evidence and bool(recommendations) and not used_fallback_text
    elif expected_outcome == CLARIFICATION_NEEDED:
        consistent = bool(questions) and not recommendations and not sufficient_evidence
    else:
        consistent = not sufficient_evidence and (not recommendations or used_fallback_text)

    passed = observed_outcome == expected_outcome and consistent
    if passed:
        detail = f"Returned {expected_outcome} as expected"
    elif observed_outcome != expected_outcome:
        detail = f"Expected {expected_outcome} but returned {observed_outcome}"
    else:
        detail = f"Returned {observed_outcome} but the payload was inconsistent with it"

    return {
        "metric": "outcome",
        "passed": passed,
        "detail": detail,
        "expected_outcome": expected_outcome,
        "observed_outcome": observed_outcome,
        "sufficient_evidence": sufficient_evidence,
    }


def score_answer_grounding(
    *,
    expected_outcome: str,
    observed_outcome: str,
    cited_sources: list[str],
    expected_supporting_documents: list[str],
    evidence_sources: list[str],
    recommendations: list[str],
) -> dict[str, Any]:
    """
    For tickets that should be grounded:
    - at least one cited source overlaps the expected supporting documents
    - cited sources appear in retrieved evidence
    - recommendations are present and are not the fixed fallback message

    For clarification and fallback tickets, grounding is scored as N/A (not failed);
    a wrong outcome is caught by the outcome check.
    """
    if expected_outcome != GROUNDED_ANSWER or observed_outcome != GROUNDED_ANSWER:
        return {
            "metric": "answer_grounding",
            "passed": True,
            "skipped": True,
            "detail": "Grounding applies only when a grounded answer is expected and returned",
            "overlap_with_expected": [],
            "unsupported_citations": [],
        }

    expected = {name.lower() for name in expected_supporting_documents}
    cited = {name.lower() for name in cited_sources if name}
    evidence = {name.lower() for name in evidence_sources if name}
    overlap = sorted(cited & expected) if expected else sorted(cited & evidence)
    unsupported = sorted(cited - evidence) if cited else []

    has_recs = bool([item for item in recommendations if item and item.strip()])
    not_fallback = all(item.strip() != FALLBACK_MESSAGE for item in recommendations if item)
    has_expected_overlap = bool(cited & expected) if expected else bool(cited)
    citations_from_evidence = not unsupported

    passed = has_recs and not_fallback and has_expected_overlap and citations_from_evidence
    if passed:
        detail = "Citations overlap expected docs and recommendations are evidence-backed"
    elif not has_expected_overlap:
        detail = "Cited sources do not overlap expected supporting documents"
    elif unsupported:
        detail = "Cited sources were not present in retrieved evidence"
    else:
        detail = "Missing grounded recommendations"

    return {
        "metric": "answer_grounding",
        "passed": passed,
        "skipped": False,
        "detail": detail,
        "overlap_with_expected": overlap,
        "unsupported_citations": unsupported,
        "cited_sources": sorted(cited),
        "expected_supporting_documents": sorted(expected),
    }


def _unscored_check(metric: str, reason: str) -> dict[str, Any]:
    return {
        "metric": metric,
        "passed": False,
        "skipped": True,
        "errored": True,
        "detail": reason,
    }


def score_ticket(ticket_spec: dict[str, Any], trace_row: dict[str, Any]) -> dict[str, Any]:
    """Score one eval ticket against an analysis trace row."""
    response = trace_row.get("response") or {}
    expected_outcome = expected_outcome_of(ticket_spec)
    error = trace_row.get("error")
    if error:
        reason = "Not scored: the agent call failed before a valid result"
        checks = {
            name: _unscored_check(name, reason)
            for name in ("tool_selection", "answer_grounding", "outcome")
        }
        return {
            "ticket_id": ticket_spec.get("ticket_id"),
            "group": ticket_spec.get("group"),
            "passed": False,
            "errored": True,
            "checks": checks,
            "expected_outcome": expected_outcome,
            "observed_outcome": None,
            "observed_category": None,
            "expected_category": ticket_spec.get("expected_category"),
            "latency_ms": trace_row.get("latency_ms"),
            "error": error,
        }

    observed_outcome = observed_outcome_of(response)
    recommendations = list(response.get("recommendations") or [])
    cited_sources = [
        src.get("source")
        for src in (response.get("sources") or [])
        if isinstance(src, dict) and src.get("source")
    ]
    evidence_sources = [
        item.get("source")
        for item in (trace_row.get("evidence") or [])
        if isinstance(item, dict) and item.get("source")
    ]

    tool = score_tool_selection(
        retrieval_appropriate=bool(ticket_spec.get("retrieval_appropriate")),
        gemini_requested_retrieval=bool(trace_row.get("gemini_requested_retrieval")),
    )
    outcome = score_outcome(
        expected_outcome=expected_outcome,
        observed_outcome=observed_outcome,
        sufficient_evidence=bool(response.get("sufficient_evidence")),
        recommendations=recommendations,
        clarifying_questions=list(response.get("clarifying_questions") or []),
    )
    grounding = score_answer_grounding(
        expected_outcome=expected_outcome,
        observed_outcome=observed_outcome,
        cited_sources=cited_sources,
        expected_supporting_documents=list(
            ticket_spec.get("expected_supporting_documents") or []
        ),
        evidence_sources=evidence_sources,
        recommendations=recommendations,
    )

    checks = [tool, outcome, grounding]
    return {
        "ticket_id": ticket_spec.get("ticket_id"),
        "group": ticket_spec.get("group"),
        "passed": all(item["passed"] for item in checks),
        "errored": False,
        "checks": {
            "tool_selection": tool,
            "answer_grounding": grounding,
            "outcome": outcome,
        },
        "expected_outcome": expected_outcome,
        "observed_outcome": observed_outcome,
        "observed_category": response.get("category") or trace_row.get("classification_category"),
        "expected_category": ticket_spec.get("expected_category"),
        "latency_ms": trace_row.get("latency_ms"),
        "error": trace_row.get("error"),
    }
