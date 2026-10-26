"""
Reproducible evaluation of tool selection, answer grounding, and fallback.

Usage (from project root, with GOOGLE_API_KEY and ingested Chroma docs):

    python -m evaluation.run_evaluation
    python -m evaluation.run_evaluation --limit 5
    python -m evaluation.run_evaluation --ticket-id E-REL-001

Outputs:
    evaluation/results/latest.json
    evaluation/results/latest.md
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent import analyze_ticket_with_trace
from app.config import settings
from app.retrieval.ingestion import get_chroma_collection, ingest_documents
from app.schemas import SupportTicket
from evaluation.scoring import OUTCOMES, score_ticket

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = Path(__file__).resolve().parent / "support_tickets.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


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


def run_one(ticket_spec: dict[str, Any]) -> dict[str, Any]:
    ticket = SupportTicket(
        ticket_id=ticket_spec["ticket_id"],
        description=ticket_spec["description"],
    )
    try:
        trace = analyze_ticket_with_trace(ticket)
        trace_row = {
            "response": trace.response.model_dump(mode="json"),
            "classification_category": trace.classification_category,
            "gemini_requested_retrieval": trace.gemini_requested_retrieval,
            "retrieval_source": trace.retrieval_source,
            "retrieval_performed": trace.retrieval_performed,
            "evidence": trace.evidence,
            "validation_sufficient": trace.validation_sufficient,
            "validation_reason": trace.validation_reason,
            "tool_calls": [
                {
                    "name": call.get("name"),
                    "args": call.get("args"),
                    "result_count": call.get("result_count"),
                    "relevant_count": call.get("relevant_count"),
                    "error": call.get("error"),
                }
                for call in trace.tool_calls
            ],
            "latency_ms": trace.latency_ms,
            "error": trace.error,
            "clarification_missing": trace.clarification_missing,
            "completeness": trace.completeness,
            "clarification_overridden": trace.clarification_overridden,
            "gemini_calls": trace.gemini_calls,
            "embed_api_calls": trace.embed_api_calls,
            "embed_cache_hits": trace.embed_cache_hits,
            "stage_ms": trace.stage_ms,
        }
    except Exception as exc:  # noqa: BLE001 - keep one API failure from aborting the report
        if _is_quota_error(str(exc)):
            logger.error(
                "Ticket %s not run: daily Gemini quota exhausted for %s. %s",
                ticket.ticket_id,
                settings.llm_model,
                _retry_hint(str(exc)),
            )
        else:
            logger.exception("Ticket %s failed before a scoreable result", ticket.ticket_id)
        trace_row = {
            "response": {},
            "classification_category": None,
            "gemini_requested_retrieval": False,
            "retrieval_source": "error",
            "retrieval_performed": False,
            "evidence": [],
            "validation_sufficient": False,
            "validation_reason": str(exc),
            "tool_calls": [],
            "latency_ms": None,
            "error": str(exc),
        }
    scored = score_ticket(ticket_spec, trace_row)
    return {"ticket": ticket_spec, "trace": trace_row, "score": scored}


def _retry_hint(message: str) -> str:
    match = re.search(r"retry in ([0-9hms.]+)", message, re.IGNORECASE)
    return f"Gemini says retry in {match.group(1)}." if match else "Retry after the daily reset."


def _is_quota_error(message: str | None) -> bool:
    """Stop the suite only for the daily cap. Per-minute limits are retried inside the agent."""
    from app.agent import is_daily_quota_error

    return is_daily_quota_error(message)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored_rows = [row for row in rows if not row["score"].get("errored")]
    total = len(scored_rows)
    passed = sum(1 for row in scored_rows if row["score"]["passed"])

    def metric_stats(name: str) -> dict[str, Any]:
        items = [row["score"]["checks"][name] for row in scored_rows]
        applicable = [item for item in items if not item.get("skipped")]
        wins = sum(1 for item in applicable if item["passed"])
        return {
            "passed": wins,
            "total": len(applicable),
            "rate": (wins / len(applicable)) if applicable else None,
            "skipped": len(items) - len(applicable),
        }

    failures = [
        {
            "ticket_id": row["score"]["ticket_id"],
            "group": row["score"]["group"],
            "failed_checks": [
                name
                for name, check in row["score"]["checks"].items()
                if not check["passed"]
            ],
            "details": {
                name: check["detail"]
                for name, check in row["score"]["checks"].items()
                if not check["passed"]
            },
            "error": row["score"].get("error"),
        }
        for row in scored_rows
        if not row["score"]["passed"]
    ]

    latencies = [row["trace"]["latency_ms"] for row in rows if row["trace"].get("latency_ms")]
    api_errors = sum(1 for row in rows if row["trace"].get("error"))

    return {
        "tickets_attempted": len(rows),
        "tickets_evaluated": total,
        "tickets_passed": passed,
        "ticket_pass_rate": (passed / total) if total else 0.0,
        "tool_selection": metric_stats("tool_selection"),
        "answer_grounding": metric_stats("answer_grounding"),
        "outcome": metric_stats("outcome"),
        "outcome_matrix": {
            expected: {
                observed: sum(
                    1
                    for row in scored_rows
                    if row["score"]["expected_outcome"] == expected
                    and row["score"]["observed_outcome"] == observed
                )
                for observed in OUTCOMES
            }
            for expected in OUTCOMES
        },
        "latency_ms": {
            "count": len(latencies),
            "avg": (sum(latencies) / len(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "api_errors": api_errors,
        "failures": failures,
    }


def render_markdown(report: dict[str, Any]) -> str:
def _cost_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Averages over rows that recorded call counts. Older traces omit them."""

    def values(key: str) -> list[float]:
        return [float(row["trace"][key]) for row in rows if row["trace"].get(key) is not None]

    def average(key: str) -> float | None:
        items = values(key)
        return (sum(items) / len(items)) if items else None

    return {
        "tickets": len(values("gemini_calls")),
        "avg_gemini_calls": average("gemini_calls"),
        "avg_embed_api_calls": average("embed_api_calls"),
        "avg_embed_cache_hits": average("embed_cache_hits"),
    }


    summary = report["summary"]
    lines = [
        "# Evaluation results",
        "",
        f"- Generated at: `{report['generated_at']}`"
        + (f" (re-scored from saved traces at `{report['rescored_at']}`)" if report.get("rescored_at") else ""),
        f"- Dataset: `{report['dataset']}`",
        f"- Model: `{report['model']}`",
        f"- Sufficiency rule: best distance <= {report['relevance_rule']['max_distance']} and "
        f"contrast@{report['relevance_rule']['contrast_rank']} >= "
        f"{report['relevance_rule']['min_contrast']} (calibrated in `evaluation/results/CALIBRATION.md`)",
        f"- Tickets scored: **{summary['tickets_passed']}/{summary['tickets_evaluated']}** "
        f"({summary['ticket_pass_rate']:.0%})",
        f"- API errors excluded from the scores: {summary['api_errors']}",
        f"- Stopped early for daily quota: {report.get('stopped_for_quota', False)}",
        "",
        "These rates describe this fixed set only. They are not a production reliability claim.",
        "",
        "## Core behaviors",
        "",
        "| Behavior | Passed | Total | Rate |",
        "|---|---:|---:|---:|",
    ]
    for key, label in (
        ("tool_selection", "Tool selection"),
        ("answer_grounding", "Answer grounding"),
        ("outcome", "Fallback / clarification outcome"),
    ):
        stats = summary[key]
        rate = "n/a" if stats["rate"] is None else f"{stats['rate']:.0%}"
        lines.append(
            f"| {label} | {stats['passed']} | {stats['total']} | {rate} |"
        )

    matrix = summary["outcome_matrix"]
    lines.extend(
        [
            "",
            "## Outcomes (rows: expected, columns: returned)",
            "",
            "| Expected | " + " | ".join(OUTCOMES) + " |",
            "|---|" + "---:|" * len(OUTCOMES),
        ]
    )
    for expected in OUTCOMES:
        lines.append(
            f"| {expected} | " + " | ".join(str(matrix[expected][observed]) for observed in OUTCOMES) + " |"
        )

    latency = summary["latency_ms"]
    lines.extend(
        [
            "",
            "## Operational notes",
            "",
            f"- API errors: {summary['api_errors']}",
            f"- Avg latency: {latency['avg']:.0f} ms" if latency["avg"] is not None else "- Avg latency: n/a",
            f"- Max latency: {latency['max']:.0f} ms" if latency["max"] is not None else "- Max latency: n/a",
            "",
        ]
    )
    cost = summary.get("cost") or {}
    if cost.get("avg_gemini_calls") is not None:
        lines.append(
            "- Avg Gemini calls: "
            f"{cost['avg_gemini_calls']:.2f}; avg embedding requests: "
            f"{cost['avg_embed_api_calls']:.2f}; avg embedding cache hits: "
            f"{cost['avg_embed_cache_hits']:.2f}"
        )
    lines.extend(
        [
            "## Failures",
            "",
        ]
    )
    if not summary["failures"]:
        lines.append("None.")
    else:
        for failure in summary["failures"]:
            lines.append(
                f"- `{failure['ticket_id']}` ({failure['group']}): "
                f"{', '.join(failure['failed_checks'])}"
            )
            for name, detail in failure["details"].items():
                lines.append(f"  - {name}: {detail}")
            if failure.get("error"):
                lines.append(f"  - error: {failure['error']}")

    lines.extend(
        [
            "",
            "## Per-ticket results",
            "",
            "| Ticket | Group | Pass | Tool | Grounding | Outcome (expected -> returned) | Latency ms |",
            "|---|---|:---:|:---:|:---:|---|---:|",
        ]
    )
    for row in report["results"]:
        score = row["score"]
        checks = score["checks"]
        if score.get("errored"):
            lines.append(
                f"| {score['ticket_id']} | {score['group']} | error | n/a | n/a | "
                f"not scored (API error) | n/a |"
            )
            continue
        grounding = "skip" if checks["answer_grounding"].get("skipped") else (
            "pass" if checks["answer_grounding"]["passed"] else "fail"
        )
        lines.append(
            "| {tid} | {group} | {ok} | {tool} | {ground} | {fb} | {lat:.0f} |".format(
                tid=score["ticket_id"],
                group=score["group"],
                ok="pass" if score["passed"] else "fail",
                tool="pass" if checks["tool_selection"]["passed"] else "fail",
                ground=grounding,
                fb="{mark} {expected} -> {observed}".format(
                    mark="pass" if checks["outcome"]["passed"] else "fail",
                    expected=score.get("expected_outcome"),
                    observed=score.get("observed_outcome") or "error",
                ),
                lat=score.get("latency_ms") or 0.0,
            )
        )
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the technical support agent")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ticket-id", action="append", default=None)
    parser.add_argument("--force-reingest", action="store_true")
    parser.add_argument("--skip-ingest-check", action="store_true")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Re-score the traces saved in results/latest.json without calling Gemini",
    )
    return parser.parse_args(argv)


def output_paths(dataset_path: Path) -> tuple[Path, Path, Path]:
    """(latest json, latest markdown, full-suite snapshot) for a dataset."""
    if dataset_path.resolve() == DATASET_PATH.resolve():
        return RESULTS_DIR / "latest.json", RESULTS_DIR / "latest.md", RESULTS_DIR / "RESULTS.md"
    stem = dataset_path.stem
    return (
        RESULTS_DIR / f"latest_{stem}.json",
        RESULTS_DIR / f"latest_{stem}.md",
        RESULTS_DIR / f"{stem.upper()}_RESULTS.md",
    )


def write_report(report: dict[str, Any], *, dataset_path: Path, full_suite: bool) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path, md_path, snapshot_path = output_paths(dataset_path)
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    # Stable copy for the repo's documented results snapshot when full suite runs.
    if full_suite:
        snapshot_path.write_text(render_markdown(report), encoding="utf-8")

    print(render_markdown(report))
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


def rescore(dataset_path: Path) -> int:
    """Score saved traces against the current dataset and scorer; no API calls."""
    report = json.loads(output_paths(dataset_path)[0].read_text(encoding="utf-8"))
    specs = {ticket["ticket_id"]: ticket for ticket in load_dataset(dataset_path)["tickets"]}
    rows = []
    for row in report["results"]:
        spec = specs[row["ticket"]["ticket_id"]]
        rows.append({"ticket": spec, "trace": row["trace"], "score": score_ticket(spec, row["trace"])})
    report["results"] = rows
    report["summary"] = summarize(rows)
    report["rescored_at"] = datetime.now(timezone.utc).isoformat()
    write_report(report, dataset_path=dataset_path, full_suite=len(rows) == len(specs))
    return 0 if report["summary"]["ticket_pass_rate"] == 1.0 else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    if args.rescore:
        return rescore(args.dataset)

    if not settings.google_api_key:
        print(
            "GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key "
            "before running the live evaluation.",
            file=sys.stderr,
        )
        return 2

    dataset = load_dataset(args.dataset)
    tickets = list(dataset.get("tickets") or [])
    if args.ticket_id:
        wanted = set(args.ticket_id)
        tickets = [ticket for ticket in tickets if ticket["ticket_id"] in wanted]
    if args.limit is not None:
        tickets = tickets[: args.limit]

    if not tickets:
        print("No tickets selected.", file=sys.stderr)
        return 2

    index_info: dict[str, Any]
    if args.skip_ingest_check:
        index_info = {"skipped": True}
    else:
        logger.info("Checking Chroma index...")
        index_info = ensure_index(force_reingest=args.force_reingest)
        logger.info("Index status: %s", index_info)

    rows: list[dict[str, Any]] = []
    stopped_for_quota = False
    for ticket in tickets:
        logger.info("Evaluating %s", ticket["ticket_id"])
        row = run_one(ticket)
        rows.append(row)
        if _is_quota_error(row["score"].get("error")):
            stopped_for_quota = True
            logger.error("Stopping evaluation: Gemini quota or rate limit reached")
            break

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "relevance_rule": {
            "max_distance": settings.relevance_max_distance,
            "min_contrast": settings.relevance_min_contrast,
            "contrast_rank": settings.relevance_contrast_rank,
            "scored_on": "original ticket text",
        },
        "index": index_info,
        "stopped_for_quota": stopped_for_quota,
        "summary": summarize(rows),
        "results": rows,
    }

    write_report(
        report,
        dataset_path=args.dataset,
        full_suite=args.limit is None and not args.ticket_id and not stopped_for_quota,
    )
    return 0 if report["summary"]["ticket_pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
