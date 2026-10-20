"""
Reproducible evaluation of tool selection, answer grounding, and fallback.

Usage (from project root, with GOOGLE_API_KEY and ingested Chroma docs):

    python -m evals.run_evaluation
    python -m evals.run_evaluation --limit 5
    python -m evals.run_evaluation --ticket-id E-REL-001

Outputs:
    evals/results/latest.json
    evals/results/latest.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent import analyze_ticket_with_trace
from app.config import settings
from app.rag.ingestion import get_chroma_collection, ingest_documents
from app.schemas import SupportTicket
from evals.scoring import score_ticket

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
    trace = analyze_ticket_with_trace(ticket)
    trace_row = {
        "response": trace.response.model_dump(),
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
    }
    scored = score_ticket(ticket_spec, trace_row)
    return {"ticket": ticket_spec, "trace": trace_row, "score": scored}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for row in rows if row["score"]["passed"])

    def metric_stats(name: str) -> dict[str, Any]:
        items = [row["score"]["checks"][name] for row in rows]
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
        for row in rows
        if not row["score"]["passed"]
    ]

    latencies = [row["trace"]["latency_ms"] for row in rows if row["trace"].get("latency_ms")]
    api_errors = sum(1 for row in rows if row["trace"].get("error"))

    return {
        "tickets_evaluated": total,
        "tickets_passed": passed,
        "ticket_pass_rate": (passed / total) if total else 0.0,
        "tool_selection": metric_stats("tool_selection"),
        "answer_grounding": metric_stats("answer_grounding"),
        "fallback": metric_stats("fallback"),
        "latency_ms": {
            "count": len(latencies),
            "avg": (sum(latencies) / len(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "api_errors": api_errors,
        "failures": failures,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Evaluation results",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Dataset: `{report['dataset']}`",
        f"- Model: `{report['model']}`",
        f"- Tickets passed: **{summary['tickets_passed']}/{summary['tickets_evaluated']}** "
        f"({summary['ticket_pass_rate']:.0%})",
        "",
        "## Core behaviors",
        "",
        "| Behavior | Passed | Total | Rate |",
        "|---|---:|---:|---:|",
    ]
    for key, label in (
        ("tool_selection", "Tool selection"),
        ("answer_grounding", "Answer grounding"),
        ("fallback", "Fallback behavior"),
    ):
        stats = summary[key]
        rate = "n/a" if stats["rate"] is None else f"{stats['rate']:.0%}"
        lines.append(
            f"| {label} | {stats['passed']} | {stats['total']} | {rate} |"
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
            "| Ticket | Group | Pass | Tool | Grounding | Fallback | Latency ms |",
            "|---|---|:---:|:---:|:---:|:---:|---:|",
        ]
    )
    for row in report["results"]:
        score = row["score"]
        checks = score["checks"]
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
                fb="pass" if checks["fallback"]["passed"] else "fail",
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

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
    for ticket in tickets:
        logger.info("Evaluating %s", ticket["ticket_id"])
        rows.append(run_one(ticket))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "relevance_max_distance": settings.relevance_max_distance,
        "index": index_info,
        "summary": summarize(rows),
        "results": rows,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / "latest.json"
    md_path = RESULTS_DIR / "latest.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    # Stable copy for the repo's documented results snapshot when full suite runs.
    if args.limit is None and not args.ticket_id:
        (RESULTS_DIR / "RESULTS.md").write_text(render_markdown(report), encoding="utf-8")

    print(render_markdown(report))
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0 if report["summary"]["ticket_pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
