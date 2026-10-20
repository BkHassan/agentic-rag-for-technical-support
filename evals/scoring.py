"""Deterministic scoring helpers for tool selection, grounding, and fallback."""

from __future__ import annotations

from typing import Any

from app.rag.guardrails import FALLBACK_MESSAGE


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


def score_fallback(
    *,
    fallback_expected: bool,
    fallback_triggered: bool,
    sufficient_evidence: bool,
    recommendations: list[str],
) -> dict[str, Any]:
    """
    Pass when fallback fires only for insufficient evidence tickets, and when
    adequate-evidence tickets receive non-fallback recommendations.
    """
    recommendations = [item for item in recommendations if item and item.strip()]
    used_fallback_text = any(item.strip() == FALLBACK_MESSAGE for item in recommendations)

    if fallback_expected:
        passed = (
            fallback_triggered is True
            and sufficient_evidence is False
            and (not recommendations or used_fallback_text)
        )
        detail = (
            "Returned application fallback for insufficient evidence"
            if passed
            else "Expected fallback but agent produced grounded recommendations"
        )
    else:
        passed = (
            fallback_triggered is False
            and sufficient_evidence is True
            and bool(recommendations)
            and not used_fallback_text
        )
        detail = (
            "Generated recommendations with adequate evidence"
            if passed
            else "Expected grounded recommendations but fallback was used or evidence was weak"
        )

    return {
        "metric": "fallback",
        "passed": passed,
        "detail": detail,
        "fallback_expected": fallback_expected,
        "fallback_triggered": fallback_triggered,
        "sufficient_evidence": sufficient_evidence,
    }


def score_answer_grounding(
    *,
    fallback_expected: bool,
    fallback_triggered: bool,
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

    For fallback tickets, grounding is scored as N/A (not failed).
    """
    if fallback_expected or fallback_triggered:
        return {
            "metric": "answer_grounding",
            "passed": True,
            "skipped": True,
            "detail": "Grounding not required when fallback is expected/triggered",
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


def score_ticket(ticket_spec: dict[str, Any], trace_row: dict[str, Any]) -> dict[str, Any]:
    """Score one eval ticket against an analysis trace row."""
    response = trace_row.get("response") or {}
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
    fallback = score_fallback(
        fallback_expected=bool(ticket_spec.get("fallback_expected")),
        fallback_triggered=bool(response.get("fallback_triggered")),
        sufficient_evidence=bool(response.get("sufficient_evidence")),
        recommendations=list(response.get("recommendations") or []),
    )
    grounding = score_answer_grounding(
        fallback_expected=bool(ticket_spec.get("fallback_expected")),
        fallback_triggered=bool(response.get("fallback_triggered")),
        cited_sources=cited_sources,
        expected_supporting_documents=list(
            ticket_spec.get("expected_supporting_documents") or []
        ),
        evidence_sources=evidence_sources,
        recommendations=list(response.get("recommendations") or []),
    )

    checks = [tool, fallback, grounding]
    return {
        "ticket_id": ticket_spec.get("ticket_id"),
        "group": ticket_spec.get("group"),
        "passed": all(item["passed"] for item in checks),
        "checks": {
            "tool_selection": tool,
            "answer_grounding": grounding,
            "fallback": fallback,
        },
        "observed_category": response.get("category") or trace_row.get("classification_category"),
        "expected_category": ticket_spec.get("expected_category"),
        "latency_ms": trace_row.get("latency_ms"),
        "error": trace_row.get("error"),
    }
