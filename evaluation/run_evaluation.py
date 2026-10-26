"""
Reproducible evaluation of the multi-agent support pipeline.

Usage (from project root, with GOOGLE_API_KEY and ingested Chroma docs):

    python -m evaluation.run_evaluation
    python -m evaluation.run_evaluation --limit 5
    python -m evaluation.run_evaluation --ticket-id E-REL-004
    python -m evaluation.run_evaluation --baseline evaluation/results/before_specificity_check.json
    python -m evaluation.run_evaluation --rescore          # offline: re-score saved traces

Outputs (support_tickets.json; other datasets get a suffix):
    evaluation/results/latest.json   raw per-ticket traces and scores, rewritten after every ticket
    evaluation/results/latest.md     concise summary
    evaluation/results/RESULTS.md    snapshot, written only for a complete, unfiltered run

Every selected ticket ends in exactly one status: passed, failed, infrastructure_error,
or not_executed. Only "passed" counts as a pass. A run that stops early is marked
incomplete and lists the tickets it did not execute.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.orchestration.coordinator import analyze_ticket_with_trace
from app.retrieval.ingestion import get_chroma_collection, ingest_documents
from app.schemas import SupportTicket
from evaluation.scoring import (
    CLARIFICATION_NEEDED,
    FAILED,
    FALLBACK,
    GROUNDED_ANSWER,
    INFRASTRUCTURE_ERROR,
    NOT_EXECUTED,
    OUTCOMES,
    PASSED,
    STATUSES,
    score_ticket,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = Path(__file__).resolve().parent / "support_tickets.json"
CALIBRATION_PATH = Path(__file__).resolve().parent / "calibration_tickets.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
ARCHITECTURE = "coordinator: triage -> researcher (search tool) -> evidence gate -> writer"
# Regressions that must stay visible in every summary, whatever the dataset says.
KNOWN_REGRESSIONS = ("E-REL-004",)
MAX_ERROR_CHARS = 500


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_index(*, force_reingest: bool = False) -> dict[str, Any]:
    """Ensure the persistent Chroma collection has documents."""
    collection = get_chroma_collection()
    count = collection.count()
    if count > 0 and not force_reingest:
        return {"reingested": False, "chunk_count": count}
    summary = ingest_documents(reset=force_reingest or count == 0)
    return {"reingested": True, "chunk_count": summary.get("chunks"), "summary": summary}


def _trace_row(trace: Any) -> dict[str, Any]:
    return {
        "response": trace.response.model_dump(mode="json"),
        "classification_category": trace.classification_category,
        "gemini_requested_retrieval": trace.gemini_requested_retrieval,
        "retrieval_source": trace.retrieval_source,
        "retrieval_performed": trace.retrieval_performed,
        "retrieval_error": trace.retrieval_error,
        "evidence": trace.evidence,
        "accepted_sources": list(trace.accepted_sources),
        "validation_sufficient": trace.validation_sufficient,
        "validation_reason": trace.validation_reason,
        "tool_calls": [
            {"name": call.get("name"), "args": call.get("args")} for call in trace.tool_calls
        ],
        "tool_executions": trace.tool_executions,
        "latency_ms": trace.latency_ms,
        "error": trace.error,
        "error_type": None,
        "clarification_missing": trace.clarification_missing,
        "completeness": trace.completeness,
        "gemini_calls": trace.gemini_calls,
        "embed_api_calls": trace.embed_api_calls,
        "embed_cache_hits": trace.embed_cache_hits,
        "gemini_responses": trace.gemini_responses,
        "gemini_responses_with_usage": trace.gemini_responses_with_usage,
        "prompt_tokens": trace.prompt_tokens,
        "output_tokens": trace.output_tokens,
        "thinking_tokens": trace.thinking_tokens,
        "total_tokens": trace.total_tokens,
        "stage_ms": trace.stage_ms,
    }


def _error_row(exc: BaseException) -> dict[str, Any]:
    return {
        "response": {},
        "classification_category": None,
        "gemini_requested_retrieval": False,
        "retrieval_source": "error",
        "retrieval_performed": False,
        "evidence": [],
        "validation_sufficient": False,
        "validation_reason": "",
        "tool_calls": [],
        "latency_ms": None,
        "error": (str(exc) or type(exc).__name__)[:MAX_ERROR_CHARS],
        "error_type": type(exc).__name__,
    }


def run_one(ticket_spec: dict[str, Any]) -> dict[str, Any]:
    """Run and score one ticket. Never raises for an ordinary exception."""
    ticket = SupportTicket(ticket_id=ticket_spec["ticket_id"], description=ticket_spec["description"])
    try:
        trace_row = _trace_row(analyze_ticket_with_trace(ticket))
    except Exception as exc:  # noqa: BLE001 - one ticket's failure must not end the run
        if _is_quota_error(str(exc)):
            logger.error("Ticket %s: daily Gemini quota exhausted for %s. %s",
                         ticket.ticket_id, settings.llm_model, _retry_hint(str(exc)))
        else:
            logger.exception("Ticket %s raised before a result", ticket.ticket_id)
        trace_row = _error_row(exc)
    try:
        scored = score_ticket(ticket_spec, trace_row)
    except Exception as exc:  # noqa: BLE001 - a scorer defect is recorded as a failure, not a pass
        logger.exception("Scoring failed for %s", ticket.ticket_id)
        trace_row = {**trace_row, "error": f"scoring failed: {type(exc).__name__}", "error_type": "ScoringError"}
        scored = score_ticket(ticket_spec, trace_row)
    return {"ticket": ticket_spec, "trace": trace_row, "score": scored}


def _retry_hint(message: str) -> str:
    match = re.search(r"retry in ([0-9hms.]+)", message, re.IGNORECASE)
    return f"Gemini says retry in {match.group(1)}." if match else "Retry after the daily reset."


def _is_quota_error(message: str | None) -> bool:
    """Stop the suite only for the daily cap. Per-minute limits are retried inside the agent."""
    from app.orchestration.model import is_daily_quota_error

    return is_daily_quota_error(message)


# --- Summary --------------------------------------------------------------------------


def _rate(wins: int, total: int) -> float | None:
    return (wins / total) if total else None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[index]


def _status(row: dict[str, Any]) -> str:
    score = row["score"]
    if score.get("status"):
        return score["status"]
    # Rows scored before statuses existed.
    if score.get("errored"):
        return INFRASTRUCTURE_ERROR
    return PASSED if score.get("passed") else FAILED


def _usage_summary(rows: list[dict[str, Any]], prices: dict[str, float] | None) -> dict[str, Any]:
    """Token totals from Gemini usage_metadata only. Missing metadata is reported, not filled in."""
    recorded = [row["trace"] for row in rows if row["trace"].get("gemini_responses") is not None]
    if not recorded:
        return {"available": False, "reason": "These traces did not record token usage."}
    responses = sum(trace["gemini_responses"] for trace in recorded)
    with_usage = sum(trace.get("gemini_responses_with_usage") or 0 for trace in recorded)
    if responses == 0:
        return {"available": False, "reason": "No successful Gemini responses were recorded."}
    totals = {key: sum(trace.get(key) or 0 for trace in recorded)
              for key in ("prompt_tokens", "output_tokens", "thinking_tokens", "total_tokens")}
    summary: dict[str, Any] = {
        "available": with_usage > 0,
        "complete": with_usage == responses,
        "gemini_responses": responses,
        "responses_with_usage": with_usage,
        "tickets": len(recorded),
        **totals,
        "avg_total_tokens_per_ticket": totals["total_tokens"] / len(recorded),
        "cost_usd": None,
        "cost_note": "Not estimated: pass --input-usd-per-mtok and --output-usd-per-mtok to estimate.",
    }
    if with_usage == 0:
        summary["reason"] = "Gemini responses carried no usage_metadata."
        summary["cost_note"] = "Not estimated: no token usage was reported."
    elif prices:
        billable_output = totals["output_tokens"] + totals["thinking_tokens"]
        summary["cost_usd"] = (
            totals["prompt_tokens"] * prices["input_usd_per_mtok"]
            + billable_output * prices["output_usd_per_mtok"]
        ) / 1_000_000
        summary["cost_note"] = (
            "Estimate from reported tokens and the supplied prices"
            + ("" if summary["complete"] else "; some responses lacked usage, so it is a lower bound")
            + ". Embedding requests are not priced."
        )
    return summary


def summarize(
    rows: list[dict[str, Any]],
    remaining: list[str] | None = None,
    prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    remaining = list(remaining or [])
    statuses = {status: 0 for status in STATUSES}
    for row in rows:
        statuses[_status(row)] += 1
    statuses[NOT_EXECUTED] = len(remaining)
    selected = len(rows) + len(remaining)
    scored_rows = [row for row in rows if not row["score"].get("errored")]
    passed = statuses[PASSED]

    def metric_stats(name: str, source: str = "checks") -> dict[str, Any]:
        items = [row["score"].get(source, {}).get(name) for row in scored_rows]
        items = [item for item in items if item is not None]
        applicable = [item for item in items if not item.get("skipped")]
        wins = sum(1 for item in applicable if item["passed"])
        return {"passed": wins, "total": len(applicable), "rate": _rate(wins, len(applicable)),
                "skipped": len(items) - len(applicable)}

    matrix = {
        expected: {
            observed: sum(1 for row in scored_rows
                          if row["score"]["expected_outcome"] == expected
                          and row["score"]["observed_outcome"] == observed)
            for observed in OUTCOMES
        }
        for expected in OUTCOMES
    }
    by_expected = {}
    for expected in OUTCOMES:
        items = [row for row in scored_rows if row["score"]["expected_outcome"] == expected]
        wins = sum(1 for row in items if row["score"]["checks"]["outcome"]["passed"])
        by_expected[expected] = {"passed": wins, "total": len(items), "rate": _rate(wins, len(items))}

    failures = [
        {
            "ticket_id": row["score"]["ticket_id"],
            "group": row["score"]["group"],
            "status": _status(row),
            "failed_checks": [name for name, check in row["score"]["checks"].items()
                              if not check["passed"] and not check.get("errored")],
            "details": {name: check["detail"] for name, check in row["score"]["checks"].items()
                        if not check["passed"] and not check.get("errored")},
            "error": row["score"].get("error"),
        }
        for row in rows
        if _status(row) != PASSED
    ]

    executed = [row["trace"] for row in rows]
    latencies = [trace["latency_ms"] for trace in executed if trace.get("latency_ms")]

    def counts(key: str) -> dict[str, Any]:
        values = [float(trace[key]) for trace in executed if trace.get(key) is not None]
        return {"tickets": len(values), "total": sum(values) if values else None,
                "avg": (sum(values) / len(values)) if values else None}

    by_id = {row["score"]["ticket_id"]: row for row in rows}
    watched = [row["ticket"]["ticket_id"] for row in rows if row["ticket"].get("regression_target")]
    watched += [tid for tid in KNOWN_REGRESSIONS if tid not in watched]
    regressions = [
        {"ticket_id": tid,
         "status": _status(by_id[tid]) if tid in by_id else (NOT_EXECUTED if tid in remaining else "not_selected"),
         "expected_outcome": by_id[tid]["score"]["expected_outcome"] if tid in by_id else None,
         "observed_outcome": by_id[tid]["score"].get("observed_outcome") if tid in by_id else None}
        for tid in watched
    ]

    return {
        "tickets_selected": selected,
        "status_counts": statuses,
        "complete": not remaining,
        "remaining_ticket_ids": remaining,
        "pass_rate_of_selected": _rate(passed, selected),
        # Kept for older readers: these exclude infrastructure errors and unexecuted tickets.
        "tickets_attempted": len(rows),
        "tickets_evaluated": len(scored_rows),
        "tickets_passed": passed,
        "ticket_pass_rate": _rate(passed, len(scored_rows)) or 0.0,
        "tool_selection": metric_stats("tool_selection"),
        "answer_grounding": metric_stats("answer_grounding"),
        "outcome": metric_stats("outcome"),
        "evidence_gate": metric_stats("evidence_gate", source="diagnostics"),
        "outcome_by_expected": by_expected,
        "outcome_matrix": matrix,
        "known_regressions": regressions,
        "calls": {
            "gemini_calls": counts("gemini_calls"),
            "tool_executions": counts("tool_executions"),
            "embed_api_calls": counts("embed_api_calls"),
        },
        "latency_ms": {
            "count": len(latencies),
            "avg": (sum(latencies) / len(latencies)) if latencies else None,
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
        "api_errors": statuses[INFRASTRUCTURE_ERROR],
        "infrastructure_error_rate": _rate(statuses[INFRASTRUCTURE_ERROR], len(rows)),
        "usage": _usage_summary(rows, prices),
        "failures": failures,
    }


# --- Baseline comparison --------------------------------------------------------------


def compare_with_baseline(
    rows: list[dict[str, Any]],
    baseline_report: dict[str, Any],
    specs: dict[str, dict[str, Any]],
    *,
    baseline_path: str,
    model: str,
) -> dict[str, Any]:
    """
    Compare on tickets both runs executed with the same wording. Baseline traces are
    re-scored with the current scorer and dataset labels so both sides use one rule.
    """
    current = {row["ticket"]["ticket_id"]: row for row in rows}
    baseline: dict[str, dict[str, Any]] = {}
    excluded: list[str] = []
    for row in baseline_report.get("results", []):
        tid = row["ticket"]["ticket_id"]
        spec = specs.get(tid)
        if spec is None or row["ticket"].get("description") != spec["description"]:
            excluded.append(tid)
            continue
        baseline[tid] = {"ticket": spec, "trace": row["trace"], "score": score_ticket(spec, row["trace"])}
    shared = sorted(set(current) & set(baseline))
    excluded += sorted(set(current) - set(baseline) - set(excluded))
    base_summary = summarize([baseline[tid] for tid in shared])
    curr_summary = summarize([current[tid] for tid in shared])
    changed = [
        {"ticket_id": tid, "baseline": _status(baseline[tid]), "current": _status(current[tid]),
         "baseline_outcome": baseline[tid]["score"].get("observed_outcome"),
         "current_outcome": current[tid]["score"].get("observed_outcome")}
        for tid in shared
        if _status(baseline[tid]) != _status(current[tid])
        or baseline[tid]["score"].get("observed_outcome") != current[tid]["score"].get("observed_outcome")
    ]

    def side(summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "status_counts": summary["status_counts"],
            "tool_selection": summary["tool_selection"],
            "answer_grounding": summary["answer_grounding"],
            "outcome": summary["outcome"],
            "evidence_gate": summary["evidence_gate"],
            "avg_gemini_calls": summary["calls"]["gemini_calls"]["avg"],
            "avg_latency_ms": summary["latency_ms"]["avg"],
            "p95_latency_ms": summary["latency_ms"]["p95"],
        }

    return {
        "baseline": baseline_path,
        "baseline_model": baseline_report.get("model"),
        "current_model": model,
        "same_model": baseline_report.get("model") == model,
        "baseline_generated_at": baseline_report.get("generated_at"),
        "shared_ticket_ids": shared,
        "excluded_ticket_ids": sorted(set(excluded)),
        "baseline_summary": side(base_summary),
        "current_summary": side(curr_summary),
        "changed_tickets": changed,
    }


# --- Markdown -------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None, fmt: str = ".0f") -> str:
    return "n/a" if value is None else format(value, fmt)


def _stat(stats: dict[str, Any]) -> str:
    return f"{stats['passed']}/{stats['total']} ({_pct(stats['rate'])})"


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    counts = summary.get("status_counts") or {}
    run = report.get("run") or {}
    rule = report["relevance_rule"]
    complete = summary.get("complete", not report.get("stopped_for_quota"))
    lines = [
        "# Evaluation results",
        "",
        f"- Generated at: `{report['generated_at']}`"
        + (f" (re-scored from saved traces at `{report['rescored_at']}`)" if report.get("rescored_at") else ""),
        f"- Dataset: `{report['dataset']}` (version {report.get('dataset_version', 'n/a')})",
        f"- Model: `{report['model']}`; embeddings: `{report.get('embedding_model', 'n/a')}`",
        f"- Architecture: {report.get('architecture', 'n/a')}",
        f"- Code: `{run.get('git_commit', 'n/a')}`" + (" (uncommitted changes)" if run.get("git_dirty") else ""),
        f"- Sufficiency rule: best distance <= {rule['max_distance']} and contrast@{rule['contrast_rank']} >= "
        f"{rule['min_contrast']} (calibrated separately on `evaluation/calibration_tickets.json`)",
        "",
        f"**Run {'complete' if complete else 'INCOMPLETE'}.** "
        + (f"Stopped: {report.get('stop_reason')}. " if report.get("stop_reason") else "")
        + f"Passed **{counts.get(PASSED, summary['tickets_passed'])}/{summary.get('tickets_selected', summary['tickets_attempted'])}** "
        f"selected tickets ({_pct(summary.get('pass_rate_of_selected'))}).",
        "",
        "| Status | Tickets |",
        "|---|---:|",
    ]
    for status in STATUSES:
        lines.append(f"| {status} | {counts.get(status, 0)} |")
    if summary.get("remaining_ticket_ids"):
        lines += ["", "Not executed: " + ", ".join(f"`{tid}`" for tid in summary["remaining_ticket_ids"])]
    lines += [
        "",
        "Only `passed` counts as a pass. Infrastructure errors and unexecuted tickets are not scored and "
        "are not excluded from the headline rate. These rates describe this fixed set only.",
        "",
        "## Behaviors (scored tickets only)",
        "",
        "| Behavior | Result |",
        "|---|---:|",
        f"| Final outcome | {_stat(summary['outcome'])} |",
        f"| Tool selection | {_stat(summary['tool_selection'])} |",
        f"| Citation correctness (grounded tickets) | {_stat(summary['answer_grounding'])} |",
    ]
    if "evidence_gate" in summary:
        lines.append(f"| Evidence gate decision (diagnostic, retrieval ran) | {_stat(summary['evidence_gate'])} |")
    for expected, label in ((GROUNDED_ANSWER, "Grounded answer when expected"),
                            (CLARIFICATION_NEEDED, "Clarification when expected"),
                            (FALLBACK, "Fallback when expected")):
        stats = (summary.get("outcome_by_expected") or {}).get(expected)
        if stats:
            lines.append(f"| {label} | {_stat(stats)} |")
    lines += [
        "",
        "Citation correctness means every cited document was in the accepted evidence and at least one "
        "is an expected supporting document. It does not measure whether each recommendation is faithful "
        "to the passage; passing the evidence gate only means the passages are close to the ticket.",
        "",
        "## Outcomes (rows: expected, columns: returned)",
        "",
        "| Expected | " + " | ".join(OUTCOMES) + " |",
        "|---|" + "---:|" * len(OUTCOMES),
    ]
    matrix = summary["outcome_matrix"]
    for expected in OUTCOMES:
        lines.append(f"| {expected} | " + " | ".join(str(matrix[expected][o]) for o in OUTCOMES) + " |")

    if summary.get("known_regressions"):
        lines += ["", "## Known regressions", ""]
        for item in summary["known_regressions"]:
            lines.append(f"- `{item['ticket_id']}`: {item['status']}"
                         + (f" (expected {item['expected_outcome']}, returned {item['observed_outcome'] or 'nothing'})"
                            if item.get("expected_outcome") else ""))

    latency = summary["latency_ms"]
    calls = summary.get("calls") or {}
    usage = summary.get("usage") or {}
    lines += [
        "",
        "## Cost and operations",
        "",
        f"- Infrastructure errors: {summary['api_errors']} of {summary['tickets_attempted']} executed "
        f"({_pct(summary.get('infrastructure_error_rate'))})",
        f"- Latency ms: avg {_num(latency['avg'])}, p50 {_num(latency.get('p50'))}, "
        f"p95 {_num(latency.get('p95'))}, max {_num(latency['max'])} (n={latency['count']})",
    ]
    if calls:
        lines.append(
            f"- Avg per ticket: Gemini calls incl. retries {_num(calls['gemini_calls']['avg'], '.2f')}, "
            f"retrieval executions {_num(calls['tool_executions']['avg'], '.2f')}, "
            f"embedding requests {_num(calls['embed_api_calls']['avg'], '.2f')}"
        )
    if usage.get("available"):
        lines.append(
            f"- Tokens (Gemini usage_metadata, {usage['responses_with_usage']}/{usage['gemini_responses']} responses): "
            f"prompt {usage['prompt_tokens']}, output {usage['output_tokens']}, thinking {usage['thinking_tokens']}, "
            f"total {usage['total_tokens']} (avg {usage['avg_total_tokens_per_ticket']:.0f}/ticket)"
        )
    else:
        lines.append(f"- Tokens: not available. {usage.get('reason', '')}".rstrip())
    cost = usage.get("cost_usd")
    lines.append(f"- Estimated cost: ${cost:.4f}. {usage['cost_note']}" if cost is not None
                 else f"- Estimated cost: {usage.get('cost_note', 'not estimated.')}")

    lines += ["", "## Not passed", ""]
    if not summary["failures"]:
        lines.append("None.")
    for failure in summary["failures"]:
        reason = "; ".join(f"{name}: {detail}" for name, detail in failure["details"].items())
        if failure.get("error"):
            reason = (reason + "; " if reason else "") + f"error: {str(failure['error'])[:160]}"
        lines.append(f"- `{failure['ticket_id']}` ({failure['group']}) **{failure.get('status', 'failed')}**: {reason}")

    comparison = report.get("comparison")
    if comparison:
        base, curr = comparison["baseline_summary"], comparison["current_summary"]
        lines += [
            "",
            "## Comparison with baseline",
            "",
            f"Baseline `{comparison['baseline']}` ({comparison['baseline_model']}, "
            f"{str(comparison['baseline_generated_at'])[:19]}) vs this run ({comparison['current_model']}), "
            f"on {len(comparison['shared_ticket_ids'])} tickets both runs executed with identical text. "
            "Both sides are scored with the current scorer.",
        ]
        if not comparison["same_model"]:
            lines.append("**Models differ; the comparison is not like for like.**")
        if comparison["excluded_ticket_ids"]:
            lines.append("Excluded: " + ", ".join(f"`{tid}`" for tid in comparison["excluded_ticket_ids"]))
        lines += ["", "| Metric | Baseline | Multi-agent |", "|---|---:|---:|"]
        for status in (PASSED, FAILED, INFRASTRUCTURE_ERROR):
            lines.append(f"| {status} | {base['status_counts'][status]} | {curr['status_counts'][status]} |")
        for key, label in (("outcome", "Final outcome"), ("tool_selection", "Tool selection"),
                           ("answer_grounding", "Citation correctness"), ("evidence_gate", "Evidence gate")):
            lines.append(f"| {label} | {_stat(base[key])} | {_stat(curr[key])} |")
        lines.append(f"| Avg Gemini calls | {_num(base['avg_gemini_calls'], '.2f')} | {_num(curr['avg_gemini_calls'], '.2f')} |")
        lines.append(f"| Avg latency ms | {_num(base['avg_latency_ms'])} | {_num(curr['avg_latency_ms'])} |")
        lines.append(f"| p95 latency ms | {_num(base['p95_latency_ms'])} | {_num(curr['p95_latency_ms'])} |")
        if comparison["changed_tickets"]:
            lines += ["", "Changed tickets:"]
            for item in comparison["changed_tickets"]:
                lines.append(f"- `{item['ticket_id']}`: {item['baseline']} ({item['baseline_outcome']}) -> "
                             f"{item['current']} ({item['current_outcome']})")

    lines += [
        "",
        "## Per-ticket results",
        "",
        "| Ticket | Group | Status | Tool | Citations | Gate | Outcome (expected -> returned) | Calls | ms |",
        "|---|---|---|:---:|:---:|:---:|---|---:|---:|",
    ]

    def mark(check: dict[str, Any] | None) -> str:
        if not check or check.get("errored"):
            return "n/a"
        return "skip" if check.get("skipped") else ("pass" if check["passed"] else "fail")

    for row in report["results"]:
        score, trace = row["score"], row["trace"]
        checks = score["checks"]
        lines.append(
            f"| {score['ticket_id']} | {score['group']} | {_status(row)} | {mark(checks['tool_selection'])} | "
            f"{mark(checks['answer_grounding'])} | {mark((score.get('diagnostics') or {}).get('evidence_gate'))} | "
            f"{score.get('expected_outcome')} -> {score.get('observed_outcome') or 'none'} | "
            f"{trace.get('gemini_calls', 'n/a')} | {_num(score.get('latency_ms'))} |"
        )
    lines.append("")
    return "\n".join(lines)


# --- Run ------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the technical support agent")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ticket-id", action="append", default=None)
    parser.add_argument("--force-reingest", action="store_true")
    parser.add_argument("--skip-ingest-check", action="store_true")
    parser.add_argument("--rescore", action="store_true",
                        help="Re-score the traces saved in results/latest.json without calling Gemini")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="A saved report to compare against on the same tickets")
    parser.add_argument("--input-usd-per-mtok", type=float, default=None,
                        help="Price per million prompt tokens; cost is estimated only when both prices are given")
    parser.add_argument("--output-usd-per-mtok", type=float, default=None,
                        help="Price per million output (incl. thinking) tokens")
    return parser.parse_args(argv)


def output_paths(dataset_path: Path) -> tuple[Path, Path, Path]:
    """(latest json, latest markdown, full-suite snapshot) for a dataset."""
    if dataset_path.resolve() == DATASET_PATH.resolve():
        return RESULTS_DIR / "latest.json", RESULTS_DIR / "latest.md", RESULTS_DIR / "RESULTS.md"
    stem = dataset_path.stem
    return (RESULTS_DIR / f"latest_{stem}.json", RESULTS_DIR / f"latest_{stem}.md",
            RESULTS_DIR / f"{stem.upper()}_RESULTS.md")


def write_report(report: dict[str, Any], *, dataset_path: Path, full_suite: bool, quiet: bool = False) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path, md_path, snapshot_path = output_paths(dataset_path)
    markdown = render_markdown(report)
    # Write to a temp file first so an interrupted write never truncates earlier results.
    tmp = json_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    tmp.replace(json_path)
    md_path.write_text(markdown, encoding="utf-8")
    if full_suite:
        snapshot_path.write_text(markdown, encoding="utf-8")
    if not quiet:
        print(markdown)
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")


def _git_info() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    status = git("status", "--porcelain")
    return {"git_commit": git("rev-parse", "--short", "HEAD"), "git_dirty": bool(status) if status is not None else None}


def _run_config() -> dict[str, Any]:
    """Non-secret settings that affect results."""
    return {
        "model_call_timeout_seconds": settings.model_call_timeout_seconds,
        "tool_execution_timeout_seconds": settings.tool_execution_timeout_seconds,
        "retrieval_top_k": settings.retrieval_top_k,
        "relevance_candidate_pool": settings.relevance_candidate_pool,
        "min_relevant_chunks": settings.min_relevant_chunks,
        "embedding_dimensionality": settings.embedding_dimensionality,
        "chunk_size_chars": settings.chunk_size_chars,
        "chunk_overlap_chars": settings.chunk_overlap_chars,
    }


def _prices(args: argparse.Namespace) -> dict[str, float] | None:
    if args.input_usd_per_mtok is None or args.output_usd_per_mtok is None:
        return None
    return {"input_usd_per_mtok": args.input_usd_per_mtok, "output_usd_per_mtok": args.output_usd_per_mtok}


def _is_calibration(path: Path, dataset: dict[str, Any]) -> bool:
    return path.resolve() == CALIBRATION_PATH.resolve() or "calibration" in str(dataset.get("purpose", "")).lower()


def _relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def rescore(dataset_path: Path, *, baseline: Path | None = None, prices: dict[str, float] | None = None) -> int:
    """Score saved traces against the current dataset and scorer; no API calls."""
    report = json.loads(output_paths(dataset_path)[0].read_text(encoding="utf-8"))
    specs = {ticket["ticket_id"]: ticket for ticket in load_dataset(dataset_path)["tickets"]}
    rows = [
        {"ticket": specs[row["ticket"]["ticket_id"]], "trace": row["trace"],
         "score": score_ticket(specs[row["ticket"]["ticket_id"]], row["trace"])}
        for row in report["results"]
    ]
    remaining = list((report.get("summary") or {}).get("remaining_ticket_ids") or [])
    report["results"] = rows
    report["summary"] = summarize(rows, remaining, prices)
    report["rescored_at"] = datetime.now(timezone.utc).isoformat()
    if baseline is not None:
        report["comparison"] = compare_with_baseline(
            rows, load_dataset(baseline), specs, baseline_path=_relpath(baseline), model=report.get("model", ""))
    full = report["summary"]["complete"] and len(rows) == len(specs)
    write_report(report, dataset_path=dataset_path, full_suite=full)
    return 0 if full and report["summary"]["status_counts"][PASSED] == len(rows) else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    prices = _prices(args)
    if args.rescore:
        return rescore(args.dataset, baseline=args.baseline, prices=prices)

    dataset = load_dataset(args.dataset)
    if _is_calibration(args.dataset, dataset):
        print("The calibration set tunes the evidence gate and must not be used as the evaluation set.",
              file=sys.stderr)
        return 2
    if not settings.google_api_key:
        print("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key "
              "before running the live evaluation.", file=sys.stderr)
        return 2

    tickets = list(dataset.get("tickets") or [])
    if args.ticket_id:
        wanted = set(args.ticket_id)
        tickets = [ticket for ticket in tickets if ticket["ticket_id"] in wanted]
    if args.limit is not None:
        tickets = tickets[: args.limit]
    if not tickets:
        print("No tickets selected.", file=sys.stderr)
        return 2
    filtered = bool(args.limit is not None or args.ticket_id)

    if args.skip_ingest_check:
        index_info: dict[str, Any] = {"skipped": True}
    else:
        logger.info("Checking Chroma index...")
        index_info = ensure_index(force_reingest=args.force_reingest)
        logger.info("Index status: %s", index_info)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "dataset": _relpath(args.dataset),
        "dataset_version": dataset.get("version"),
        "dataset_ticket_count": len(dataset.get("tickets") or []),
        "selection": {"limit": args.limit, "ticket_ids": args.ticket_id},
        "model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "architecture": ARCHITECTURE,
        "config": _run_config(),
        "run": {**_git_info(), "python": platform.python_version()},
        "relevance_rule": {
            "max_distance": settings.relevance_max_distance,
            "min_contrast": settings.relevance_min_contrast,
            "contrast_rank": settings.relevance_contrast_rank,
            # The multi-agent gate scores the Researcher's search results; calibration used ticket text.
            "scored_on": f"researcher query results (top_k={settings.retrieval_top_k})",
        },
        "index": index_info,
        "stopped_for_quota": False,
        "stop_reason": None,
        "summary": {},
        "results": [],
    }
    specs = {ticket["ticket_id"]: ticket for ticket in dataset.get("tickets") or []}
    baseline_report = load_dataset(args.baseline) if args.baseline else None

    def save(rows: list[dict[str, Any]], remaining: list[str], *, final: bool) -> None:
        report["results"] = rows
        report["summary"] = summarize(rows, remaining, prices)
        if baseline_report is not None and final:
            report["comparison"] = compare_with_baseline(
                rows, baseline_report, specs, baseline_path=_relpath(args.baseline), model=settings.llm_model)
        if final:
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_report(report, dataset_path=args.dataset,
                     full_suite=final and not filtered and not remaining, quiet=not final)

    rows: list[dict[str, Any]] = []
    pending = [ticket["ticket_id"] for ticket in tickets]
    try:
        for ticket in tickets:
            logger.info("Evaluating %s", ticket["ticket_id"])
            row = run_one(ticket)
            rows.append(row)
            pending.remove(ticket["ticket_id"])
            if _is_quota_error(row["trace"].get("error")):
                report["stopped_for_quota"] = True
                report["stop_reason"] = "daily Gemini quota exhausted"
                logger.error("Stopping evaluation: daily Gemini quota reached; %d tickets not run", len(pending))
                break
            save(rows, pending, final=False)
    except KeyboardInterrupt:
        report["stop_reason"] = "interrupted"
        logger.error("Interrupted; %d tickets not run", len(pending))
    save(rows, pending, final=True)

    summary = report["summary"]
    return 0 if summary["complete"] and summary["status_counts"][PASSED] == len(tickets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
