"""
Check the clarification decision on the labeled calibration tickets.

Usage (from project root, with GOOGLE_API_KEY):

    python -m evaluation.check_clarification

Runs only the classification step (one Gemini call per ticket, no retrieval) and
applies `decide_clarification`. Expected: the vague group asks for clarification;
answerable and out-of-corpus tickets name a system and symptom, so they do not
(out-of-corpus tickets are a documentation gap, handled by the fallback).

Outputs:
    evals/results/clarification_check.json
    evals/results/CLARIFICATION_CHECK.md
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.orchestration.coordinator import classify_ticket
from app.orchestration.model import is_daily_quota_error
from app.policies.clarification import decide_clarification
from app.config import settings

logger = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVALS_DIR / "calibration_tickets.json"
RESULTS_DIR = EVALS_DIR / "results"
JSON_PATH = RESULTS_DIR / "clarification_check.json"
REPORT_PATH = RESULTS_DIR / "CLARIFICATION_CHECK.md"

CLARIFY_GROUPS = {"vague"}


def check_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticket_id": ticket["ticket_id"],
        "group": ticket["group"],
        "description": ticket["description"],
        "expected_clarification": ticket["group"] in CLARIFY_GROUPS,
    }
    try:
        classification = classify_ticket(ticket["description"], ticket_id=ticket["ticket_id"])
    except Exception as exc:  # noqa: BLE001 - recorded and excluded from the counts
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    completeness = classification.completeness
    decision = decide_clarification(ticket["description"], completeness)
    row.update(
        {
            "error": None,
            "completeness": completeness.model_dump() if completeness else None,
            "clarification": decision.needed,
            "missing": decision.missing,
            "passed": decision.needed == row["expected_clarification"],
        }
    )
    return row


def render_markdown(rows: list[dict[str, Any]], generated_at: str) -> str:
    scored = [row for row in rows if not row.get("error")]
    passed = sum(1 for row in scored if row["passed"])
    lines = [
        "# Clarification check (calibration tickets)",
        "",
        f"Generated: {generated_at} · Model: `{settings.llm_model}`",
        "",
        f"**{passed}/{len(scored)}** tickets got the expected decision"
        f" ({len(rows) - len(scored)} API errors excluded).",
        "",
        "| Group | Tickets | Asked for clarification |",
        "|---|---:|---:|",
    ]
    for group in ("answerable", "out_of_corpus", "vague"):
        in_group = [row for row in scored if row["group"] == group]
        asked = sum(1 for row in in_group if row["clarification"])
        lines.append(f"| {group} | {len(in_group)} | {asked} |")

    mismatches = [row for row in scored if not row["passed"]]
    if mismatches:
        lines.extend(["", "## Mismatches", ""])
        for row in mismatches:
            facts = row["completeness"] or {}
            lines.append(
                f"- `{row['ticket_id']}` ({row['group']}): {row['description']!r} — "
                f"system={facts.get('affected_system')!r}, "
                f"symptom={facts.get('observable_symptom')!r}, missing={row['missing']}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    tickets = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["tickets"]

    rows: list[dict[str, Any]] = []
    for ticket in tickets:
        row = check_ticket(ticket)
        rows.append(row)
        status = "error" if row.get("error") else ("ok" if row["passed"] else "MISMATCH")
        print(f"{row['ticket_id']}: {status}", flush=True)
        if row.get("error") and is_daily_quota_error(row["error"]):
            print("Stopped: daily Gemini quota reached.", file=sys.stderr)
            break

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(
        json.dumps({"generated_at": generated_at, "model": settings.llm_model, "rows": rows}, indent=2),
        encoding="utf-8",
    )
    REPORT_PATH.write_text(render_markdown(rows, generated_at), encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")

    scored = [row for row in rows if not row.get("error")]
    return 0 if scored and len(scored) == len(tickets) and all(row["passed"] for row in scored) else 1


if __name__ == "__main__":
    raise SystemExit(main())
