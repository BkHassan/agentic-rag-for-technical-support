"""Offline tests for the evaluation runner. The pipeline is replaced; no Gemini or Chroma calls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors as genai_errors

import evaluation.run_evaluation as runner
from app.orchestration.coordinator import TicketAnalysisTrace
from app.orchestration.workflow import _call_bounded
from app.schemas import AgentOutcome, AgentResponse, DocumentSource, TicketCategory
from app.usage import current_usage, note_gemini_call, note_gemini_response, reset_usage, start_usage
from evaluation.scoring import FAILED, INFRASTRUCTURE_ERROR, NOT_EXECUTED, PASSED, score_ticket

FALLBACK_TEXT = "Insufficient supporting documentation was found to provide reliable troubleshooting recommendations. Please review the ticket manually."

GROUNDED_SPEC = {
    "ticket_id": "T-G", "group": "relevant_documentation", "description": "DB connection keeps timing out.",
    "expected_category": "database", "retrieval_appropriate": True,
    "expected_supporting_documents": ["database_connection_failures.md"], "expected_outcome": "grounded_answer",
}
FALLBACK_SPEC = {
    "ticket_id": "T-F", "group": "irrelevant_documentation", "description": "Zoom screen share is black.",
    "expected_category": "application", "retrieval_appropriate": True,
    "expected_supporting_documents": [], "expected_outcome": "fallback",
}


def _grounded_trace(ticket_id: str = "T-G", **overrides: Any) -> TicketAnalysisTrace:
    trace = TicketAnalysisTrace(
        response=AgentResponse(
            ticket_id=ticket_id, category=TicketCategory.DATABASE, outcome=AgentOutcome.GROUNDED_ANSWER,
            recommendations=["Check the pool size."],
            sources=[DocumentSource(source="database_connection_failures.md")],
            sufficient_evidence=True, fallback_triggered=False,
        ),
        classification_category="database", gemini_requested_retrieval=True, retrieval_source="researcher",
        retrieval_performed=True, evidence=[{"source": "database_connection_failures.md"}],
        validation_sufficient=True, validation_reason="accepted", tool_calls=[], latency_ms=100.0,
        accepted_sources=["database_connection_failures.md"], tool_executions=1, gemini_calls=3,
    )
    for key, value in overrides.items():
        setattr(trace, key, value)
    return trace


def _fallback_trace_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "response": {"outcome": "fallback", "recommendations": [FALLBACK_TEXT], "sources": [],
                     "sufficient_evidence": False, "fallback_triggered": True},
        "gemini_requested_retrieval": True, "retrieval_performed": False, "evidence": [],
        "validation_sufficient": False, "error": None, "latency_ms": 50.0,
    }
    row.update(overrides)
    return row


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(runner.settings, "google_api_key", "test-key")
    monkeypatch.setattr(runner, "_git_info", lambda: {"git_commit": "abc123", "git_dirty": False})
    return tmp_path


def _dataset(tmp_path: Path, tickets: list[dict[str, Any]]) -> Path:
    path = tmp_path / "eval_set.json"
    path.write_text(json.dumps({"version": "9.9", "tickets": tickets}), encoding="utf-8")
    return path


def _spec(ticket_id: str) -> dict[str, Any]:
    return {**GROUNDED_SPEC, "ticket_id": ticket_id}


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "results" / "latest_eval_set.json").read_text(encoding="utf-8"))


# --- Statuses ------------------------------------------------------------------------


def test_retrieval_outage_is_an_infrastructure_error_even_when_fallback_was_expected() -> None:
    scored = score_ticket(FALLBACK_SPEC, _fallback_trace_row(retrieval_error="retrieval failed: ConnectionError"))
    assert scored["status"] == INFRASTRUCTURE_ERROR
    assert scored["passed"] is False

    healthy = score_ticket(FALLBACK_SPEC, _fallback_trace_row())
    assert healthy["status"] == PASSED


@pytest.mark.parametrize(
    ("error", "error_type", "status"),
    [
        ("The analysis timed out.", None, INFRASTRUCTURE_ERROR),
        ("The language model is temporarily unavailable.", None, INFRASTRUCTURE_ERROR),
        ("429 RESOURCE_EXHAUSTED", "ClientError", INFRASTRUCTURE_ERROR),
        ("Ticket classification failed.", None, FAILED),
        ("The analysis could not be completed.", None, FAILED),
        ("boom", "KeyError", FAILED),
    ],
)
def test_errors_are_split_into_outages_and_system_failures(error: str, error_type: str | None, status: str) -> None:
    scored = score_ticket(GROUNDED_SPEC, {"response": {}, "error": error, "error_type": error_type})
    assert scored["status"] == status
    assert scored["passed"] is False


def test_citations_are_checked_against_accepted_not_merely_retrieved_evidence() -> None:
    trace = runner._trace_row(_grounded_trace(accepted_sources=["other.md"]))
    scored = score_ticket(GROUNDED_SPEC, trace)
    grounding = scored["checks"]["answer_grounding"]
    assert grounding["passed"] is False
    assert grounding["unsupported_citations"] == ["database_connection_failures.md"]


def test_evidence_gate_diagnostic_does_not_change_the_ticket_pass() -> None:
    trace = _fallback_trace_row(retrieval_performed=True, validation_sufficient=True)
    scored = score_ticket(FALLBACK_SPEC, trace)
    assert scored["diagnostics"]["evidence_gate"]["passed"] is False
    assert scored["status"] == PASSED


# --- Run loop ------------------------------------------------------------------------


def test_errors_are_recorded_per_ticket_and_a_quota_stop_lists_the_remaining(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def pipeline(ticket):
        if ticket.ticket_id == "T-2":
            raise genai_errors.ServerError(503, {"error": {"message": "The model is overloaded."}})
        if ticket.ticket_id == "T-3":
            raise genai_errors.ClientError(429, {"error": {"message": "Quota exceeded: requests per day."}})
        return _grounded_trace(ticket.ticket_id)

    monkeypatch.setattr(runner, "analyze_ticket_with_trace", pipeline)
    dataset = _dataset(workspace, [_spec("T-1"), _spec("T-2"), _spec("T-3"), _spec("T-4")])

    code = runner.main(["--dataset", str(dataset), "--skip-ingest-check"])

    report = _report(workspace)
    summary = report["summary"]
    assert code == 1
    assert summary["complete"] is False
    assert summary["remaining_ticket_ids"] == ["T-4"]
    assert summary["status_counts"] == {PASSED: 1, FAILED: 0, INFRASTRUCTURE_ERROR: 2, NOT_EXECUTED: 1}
    assert summary["pass_rate_of_selected"] == 0.25
    assert report["stop_reason"] == "daily Gemini quota exhausted"
    assert [row["score"]["status"] for row in report["results"]] == [PASSED, INFRASTRUCTURE_ERROR, INFRASTRUCTURE_ERROR]
    assert report["dataset_version"] == "9.9" and report["model"] == runner.settings.llm_model
    assert "google_api_key" not in json.dumps(report).lower()
    markdown = (workspace / "results" / "latest_eval_set.md").read_text(encoding="utf-8")
    assert "INCOMPLETE" in markdown and "`T-4`" in markdown


def test_interrupt_keeps_earlier_results_and_marks_the_run_incomplete(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def pipeline(ticket):
        if ticket.ticket_id == "T-2":
            raise KeyboardInterrupt
        return _grounded_trace(ticket.ticket_id)

    monkeypatch.setattr(runner, "analyze_ticket_with_trace", pipeline)
    dataset = _dataset(workspace, [_spec("T-1"), _spec("T-2"), _spec("T-3")])

    assert runner.main(["--dataset", str(dataset), "--skip-ingest-check"]) == 1

    report = _report(workspace)
    assert [row["ticket"]["ticket_id"] for row in report["results"]] == ["T-1"]
    assert report["summary"]["remaining_ticket_ids"] == ["T-2", "T-3"]
    assert report["stop_reason"] == "interrupted"


def test_complete_passing_run_exits_zero_and_reports_counts(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "analyze_ticket_with_trace", lambda ticket: _grounded_trace(ticket.ticket_id))
    dataset = _dataset(workspace, [_spec("T-1"), _spec("T-2")])

    assert runner.main(["--dataset", str(dataset), "--skip-ingest-check"]) == 0
    summary = _report(workspace)["summary"]
    assert summary["complete"] is True
    assert summary["calls"]["gemini_calls"]["avg"] == 3
    assert summary["calls"]["tool_executions"]["total"] == 2


def test_calibration_set_is_refused_as_the_evaluation_set(workspace: Path) -> None:
    assert runner.main(["--dataset", str(runner.CALIBRATION_PATH), "--skip-ingest-check"]) == 2


# --- Tokens and cost -----------------------------------------------------------------


def _usage_rows(responses: int, with_usage: int, total: int) -> list[dict[str, Any]]:
    trace = {"gemini_responses": responses, "gemini_responses_with_usage": with_usage, "prompt_tokens": total // 2,
             "output_tokens": total // 2, "thinking_tokens": 0, "total_tokens": total}
    return [{"ticket": GROUNDED_SPEC, "trace": trace, "score": {"status": PASSED}}]


def test_tokens_and_cost_are_not_invented_without_usage_metadata() -> None:
    prices = {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 2.0}
    assert runner._usage_summary(_usage_rows(3, 0, 0), prices)["cost_usd"] is None
    assert runner._usage_summary(_usage_rows(3, 0, 0), prices)["available"] is False
    old_traces = [{"ticket": GROUNDED_SPEC, "trace": {}, "score": {}}]
    assert runner._usage_summary(old_traces, prices)["available"] is False


def test_cost_is_estimated_only_from_reported_tokens_and_supplied_prices() -> None:
    usage = runner._usage_summary(_usage_rows(2, 2, 2_000_000), {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 2.0})
    assert usage["complete"] is True
    assert usage["cost_usd"] == pytest.approx(3.0)
    assert runner._usage_summary(_usage_rows(2, 2, 2_000_000), None)["cost_usd"] is None
    assert runner._usage_summary(_usage_rows(3, 2, 2_000_000), {"input_usd_per_mtok": 1.0,
                                                                "output_usd_per_mtok": 2.0})["complete"] is False


def test_usage_counters_reach_bounded_worker_threads_and_record_metadata() -> None:
    token = start_usage()
    try:
        response = SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=120, candidates_token_count=30, thoughts_token_count=10, total_token_count=160))

        def model_call() -> None:
            note_gemini_call()
            note_gemini_response(response)
            note_gemini_response(SimpleNamespace())  # no metadata: counted, adds no tokens

        _call_bounded(model_call, 5.0)
        usage = current_usage()
        assert usage.gemini_calls == 1
        assert (usage.gemini_responses, usage.gemini_responses_with_usage) == (2, 1)
        assert (usage.prompt_tokens, usage.output_tokens, usage.thinking_tokens, usage.total_tokens) == (120, 30, 10, 160)
    finally:
        reset_usage(token)


# --- Baseline comparison -------------------------------------------------------------


def test_baseline_comparison_uses_only_tickets_with_identical_text() -> None:
    specs = {"T-1": _spec("T-1"), "T-2": _spec("T-2")}
    current = [{"ticket": spec, "trace": runner._trace_row(_grounded_trace(tid)),
                "score": score_ticket(spec, runner._trace_row(_grounded_trace(tid)))}
               for tid, spec in specs.items()]
    baseline = {
        "model": "same-model",
        "results": [
            {"ticket": specs["T-1"], "trace": _fallback_trace_row()},
            {"ticket": {**specs["T-2"], "description": "older wording"}, "trace": _fallback_trace_row()},
        ],
    }

    comparison = runner.compare_with_baseline(current, baseline, specs, baseline_path="b.json", model="same-model")

    assert comparison["shared_ticket_ids"] == ["T-1"]
    assert comparison["excluded_ticket_ids"] == ["T-2"]
    assert comparison["same_model"] is True
    assert comparison["baseline_summary"]["status_counts"][FAILED] == 1
    assert comparison["current_summary"]["status_counts"][PASSED] == 1
    assert comparison["changed_tickets"][0]["ticket_id"] == "T-1"
