"""
Calibrate the retrieval sufficiency rule on labeled examples.

Usage (from project root, with GOOGLE_API_KEY and an ingested Chroma index):

    python -m evals.calibrate_relevance
    python -m evals.calibrate_relevance --reuse-features   # refit without API calls

Each labeled ticket is retrieved with its original text (as the API's gate does),
then three rule families are fitted on the retrieval scores:

- distance:           accept when best distance <= T
- contrast:           accept when contrast@k >= C
- distance+contrast:  accept when both hold

Within a family, thresholds minimize UNSAFE_ANSWER_COST * unsafe answers +
MISSED_ANSWER_COST * missed answers; ties take the rule with the widest margin to
the nearest example. The family (and contrast rank) is chosen by leave-one-out
cost, so the choice reflects tickets the thresholds were not fitted on. Ties
prefer the wider margin, then the simpler family. Because the family is selected
with leave-one-out, the held-out 20-ticket evaluation is the unbiased check.

Outputs:
    evals/results/calibration.json
    evals/results/CALIBRATION.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings
from app.rag.guardrails import relevance_features
from app.rag.retrieval import retrieve_many

logger = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVALS_DIR / "calibration_tickets.json"
RESULTS_DIR = EVALS_DIR / "results"
FEATURES_PATH = RESULTS_DIR / "calibration.json"
REPORT_PATH = RESULTS_DIR / "CALIBRATION.md"

# A confident answer built from unrelated docs is worse than sending a ticket to manual review.
UNSAFE_ANSWER_COST = 2.0
MISSED_ANSWER_COST = 1.0
CONTRAST_RANKS = (2, 3, 4, 5)
FAMILY_ORDER = {"distance": 0, "contrast": 1, "distance+contrast": 2}


@dataclass(frozen=True)
class Rule:
    family: str
    max_distance: float | None
    min_contrast: float | None
    contrast_rank: int | None
    unsafe: int
    missed: int
    cost: float
    margin: float


def load_dataset(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["tickets"]


def extract_features(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retrieve each ticket's original text and compute the gate features."""
    results = retrieve_many(
        [ticket["description"] for ticket in tickets],
        top_k=settings.relevance_candidate_pool,
    )
    rows: list[dict[str, Any]] = []
    for ticket, hits in zip(tickets, results):
        base = relevance_features(hits, contrast_rank=1)
        ranked_docs: list[str] = []
        for hit in sorted(hits, key=lambda item: float(item["distance"])):
            if hit["source"] not in ranked_docs:
                ranked_docs.append(hit["source"])
        rows.append(
            {
                "ticket_id": ticket["ticket_id"],
                "group": ticket["group"],
                "label": ticket["label"],
                "description": ticket["description"],
                "expected_documents": ticket["expected_documents"],
                "best_distance": base.best_distance,
                "contrast": {
                    str(rank): relevance_features(hits, contrast_rank=rank).contrast
                    for rank in CONTRAST_RANKS
                },
                "top_documents": ranked_docs[:5],
                "top1_matches_expected": bool(ranked_docs)
                and ranked_docs[0] in ticket["expected_documents"],
            }
        )
    return rows


def _candidates(values: np.ndarray) -> np.ndarray:
    """Midpoints between neighboring observed values, plus both extremes."""
    finite = np.unique(values[np.isfinite(values)])
    if finite.size == 0:
        return np.array([0.0])
    mids = (finite[:-1] + finite[1:]) / 2.0
    return np.concatenate(([finite[0] - 1e-6], mids, [finite[-1] + 1e-6]))


def _margin(
    distance: np.ndarray,
    contrast: np.ndarray,
    labels: np.ndarray,
    accept: np.ndarray,
    max_distance: float | None,
    min_contrast: float | None,
    scale_distance: float,
    scale_contrast: float,
) -> float:
    """Smallest normalized gap between a correctly decided example and the rule boundary."""
    gaps: list[float] = []
    for index in range(labels.size):
        if accept[index] != labels[index]:
            continue
        d_gap = (max_distance - distance[index]) / scale_distance if max_distance is not None else None
        c_value = contrast[index]
        c_gap = (
            (c_value - min_contrast) / scale_contrast
            if min_contrast is not None and np.isfinite(c_value)
            else (None if min_contrast is None else -np.inf)
        )
        if accept[index]:
            gaps.append(min(g for g in (d_gap, c_gap) if g is not None))
        else:
            parts = [max(0.0, -g) for g in (d_gap, c_gap) if g is not None]
            gaps.append(float(np.inf) if any(np.isinf(parts)) else float(np.sqrt(sum(p * p for p in parts))))
    return float(min(gaps)) if gaps else 0.0


FAMILIES: list[tuple[str, int | None]] = [("distance", None)] + [
    (family, rank) for rank in CONTRAST_RANKS for family in ("contrast", "distance+contrast")
]


def fit_family(rows: list[dict[str, Any]], family: str, rank: int | None) -> Rule:
    """Best thresholds for one rule family on `rows` (cost first, then margin)."""
    labels = np.array([row["label"] == "answer" for row in rows])
    distance = np.array([row["best_distance"] for row in rows], dtype=float)
    contrast = np.array(
        [
            np.nan if rank is None or row["contrast"][str(rank)] is None else row["contrast"][str(rank)]
            for row in rows
        ],
        dtype=float,
    )
    scale_distance = float(np.std(distance)) or 1.0
    scale_contrast = float(np.nanstd(contrast)) if rank is not None else 1.0
    scale_contrast = scale_contrast or 1.0

    use_distance = family in ("distance", "distance+contrast")
    use_contrast = family in ("contrast", "distance+contrast")
    t_values = _candidates(distance) if use_distance else np.array([np.nan])
    c_values = _candidates(contrast) if use_contrast else np.array([np.nan])

    all_true = np.ones((1, labels.size), dtype=bool)
    accept_d = distance[None, :] <= t_values[:, None] if use_distance else all_true
    with np.errstate(invalid="ignore"):
        accept_c = contrast[None, :] >= c_values[:, None] if use_contrast else all_true
    accept = accept_d[:, None, :] & accept_c[None, :, :]
    unsafe = np.sum(accept & ~labels, axis=2)
    missed = np.sum(~accept & labels, axis=2)
    cost = UNSAFE_ANSWER_COST * unsafe + MISSED_ANSWER_COST * missed
    best_cost = cost.min()

    winners: list[Rule] = []
    for ti, ci in zip(*np.where(cost == best_cost)):
        t = float(t_values[ti]) if use_distance else None
        c = float(c_values[ci]) if use_contrast else None
        winners.append(
            Rule(
                family=family,
                max_distance=t,
                min_contrast=c,
                contrast_rank=rank,
                unsafe=int(unsafe[ti, ci]),
                missed=int(missed[ti, ci]),
                cost=float(best_cost),
                margin=_margin(distance, contrast, labels, accept[ti, ci], t, c,
                               scale_distance, scale_contrast),
            )
        )
    return max(winners, key=lambda rule: rule.margin)


def decide(rule: Rule, row: dict[str, Any]) -> bool:
    if rule.max_distance is not None and row["best_distance"] > rule.max_distance:
        return False
    if rule.min_contrast is not None:
        value = row["contrast"][str(rule.contrast_rank)]
        return value is not None and value >= rule.min_contrast
    return True


def leave_one_out(rows: list[dict[str, Any]], family: str, rank: int | None) -> dict[str, Any]:
    """Refit one family without each ticket and score the held-out decision."""
    unsafe: list[str] = []
    missed: list[str] = []
    for index, held_out in enumerate(rows):
        rule = fit_family(rows[:index] + rows[index + 1 :], family, rank)
        accepted = decide(rule, held_out)
        if accepted and held_out["label"] == "fallback":
            unsafe.append(held_out["ticket_id"])
        elif not accepted and held_out["label"] == "answer":
            missed.append(held_out["ticket_id"])
    return {
        "unsafe_answers": unsafe,
        "missed_answers": missed,
        "cost": UNSAFE_ANSWER_COST * len(unsafe) + MISSED_ANSWER_COST * len(missed),
        "accuracy": 1 - (len(unsafe) + len(missed)) / len(rows),
    }


def fit(rows: list[dict[str, Any]]) -> tuple[Rule, dict[str, Any], list[dict[str, Any]]]:
    """Return the selected rule, its leave-one-out result, and every family's results."""
    candidates: list[dict[str, Any]] = []
    for family, rank in FAMILIES:
        rule = fit_family(rows, family, rank)
        candidates.append({"rule": rule, "loo": leave_one_out(rows, family, rank)})
    best = min(
        candidates,
        key=lambda item: (
            item["loo"]["cost"],
            -item["rule"].margin,
            FAMILY_ORDER[item["rule"].family],
        ),
    )
    return best["rule"], best["loo"], candidates


def decide(rule: Rule, row: dict[str, Any]) -> bool:
    if rule.max_distance is not None and row["best_distance"] > rule.max_distance:
        return False
    if rule.min_contrast is not None:
        value = row["contrast"][str(rule.contrast_rank)]
        return value is not None and value >= rule.min_contrast
    return True


def baseline(rows: list[dict[str, Any]], max_distance: float) -> dict[str, Any]:
    rule = Rule("distance", max_distance, None, None, 0, 0, 0.0, 0.0)
    unsafe = [r["ticket_id"] for r in rows if decide(rule, r) and r["label"] == "fallback"]
    missed = [r["ticket_id"] for r in rows if not decide(rule, r) and r["label"] == "answer"]
    return {"max_distance": max_distance, "unsafe_answers": unsafe, "missed_answers": missed}


def _fmt(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    rule = report["selected_rule"]
    loo = report["leave_one_out"]
    base = report["baseline"]
    rows = report["examples"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(row)

    lines = [
        "# Retrieval sufficiency calibration",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Dataset: `evals/calibration_tickets.json` ({len(rows)} labeled tickets, separate from the 20-ticket evaluation)",
        f"- Embedding model: `{report['embedding_model']}`; collection chunks: {report['chunk_count']}",
        f"- Cost: unsafe answer = {UNSAFE_ANSWER_COST:g}, missed answer = {MISSED_ANSWER_COST:g}",
        "",
        "## Selected rule",
        "",
        f"- Family: **{rule['family']}**",
        f"- `relevance_max_distance` = **{_fmt(rule['max_distance'])}**",
        f"- `relevance_min_contrast` = **{_fmt(rule['min_contrast'])}**"
        + (f" (contrast rank {rule['contrast_rank']})" if rule["contrast_rank"] else ""),
        f"- On the calibration set: {rule['unsafe']} unsafe answer(s), {rule['missed']} missed answer(s)",
        f"- Leave-one-out: {len(loo['unsafe_answers'])} unsafe, {len(loo['missed_answers'])} missed "
        f"(accuracy {loo['accuracy']:.0%})"
        + (
            f"; errors: {', '.join(loo['unsafe_answers'] + loo['missed_answers'])}"
            if loo["unsafe_answers"] or loo["missed_answers"]
            else ""
        ),
        f"- Previous rule (distance <= {base['max_distance']}): "
        f"{len(base['unsafe_answers'])} unsafe, {len(base['missed_answers'])} missed",
        "",
        "## Best rule per family",
        "",
        "In-sample counts use thresholds fitted on all tickets. Leave-one-out counts refit without "
        "each ticket and score it, so they are the fairer comparison between families.",
        "",
        "| Family | Rank | Max distance | Min contrast | In-sample unsafe | In-sample missed "
        "| LOO unsafe | LOO missed | LOO cost | Margin |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["family_results"]:
        item_loo = item["leave_one_out"]
        lines.append(
            f"| {item['family']} | {item['contrast_rank'] or '—'} | {_fmt(item['max_distance'])} | "
            f"{_fmt(item['min_contrast'])} | {item['unsafe']} | {item['missed']} | "
            f"{len(item_loo['unsafe_answers'])} | {len(item_loo['missed_answers'])} | "
            f"{item_loo['cost']:g} | {item['margin']:.2f} |"
        )

    lines += [
        "",
        "## Selected rule by group",
        "",
        "| Group | Label | Tickets | Accepted | Fallback |",
        "|---|---|---:|---:|---:|",
    ]
    for group, items in groups.items():
        accepted = sum(1 for item in items if item["accepted"])
        lines.append(
            f"| {group} | {items[0]['label']} | {len(items)} | {accepted} | {len(items) - accepted} |"
        )

    top1 = [row for row in rows if row["label"] == "answer"]
    top1_hits = sum(1 for row in top1 if row["top1_matches_expected"])
    lines += [
        "",
        f"Top-1 document matched the labeled document for {top1_hits}/{len(top1)} answerable tickets.",
        "",
        "## Limits",
        "",
        f"- {len(rows)} hand-labeled tickets over a {report['chunk_count']}-chunk corpus. "
        "Thresholds carry real sampling error; leave-one-out is the better estimate than the in-sample counts.",
        "- The thresholds depend on the corpus, chunking, and embedding model. Re-run after changing any of them.",
        "- Contrast compares the best document with the background. If several documents legitimately cover "
        "one topic, contrast drops for good tickets and the rank may need re-fitting.",
        "",
        "## Examples",
        "",
        "| Ticket | Label | Best distance | "
        + " | ".join(f"Contrast@{rank}" for rank in CONTRAST_RANKS)
        + " | Decision | Top document |",
        "|---|---|---:|" + "---:|" * len(CONTRAST_RANKS) + "---|---|",
    ]
    for row in rows:
        decision = "answer" if row["accepted"] else "fallback"
        if (decision == "answer") != (row["label"] == "answer"):
            decision = f"**{decision}** (wrong)"
        lines.append(
            f"| {row['ticket_id']} | {row['label']} | {row['best_distance']:.4f} | "
            + " | ".join(_fmt(row["contrast"][str(rank)]) for rank in CONTRAST_RANKS)
            + f" | {decision} | {row['top_documents'][0] if row['top_documents'] else '—'} |"
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--reuse-features",
        action="store_true",
        help=f"Refit from features saved in {FEATURES_PATH.name} instead of calling the embedding API.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    if args.reuse_features:
        saved = json.loads(FEATURES_PATH.read_text(encoding="utf-8"))
        rows = [
            {key: value for key, value in row.items() if key != "accepted"}
            for row in saved["examples"]
        ]
        chunk_count = saved["chunk_count"]
    else:
        if not settings.google_api_key:
            print("GOOGLE_API_KEY is required to embed calibration tickets.", file=sys.stderr)
            return 2
        from app.rag.ingestion import get_chroma_collection

        chunk_count = get_chroma_collection().count()
        rows = extract_features(load_dataset())

    selected, selected_loo, family_results = fit(rows)
    for row in rows:
        row["accepted"] = decide(selected, row)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "embedding_model": settings.embedding_model,
        "chunk_count": chunk_count,
        "cost": {"unsafe_answer": UNSAFE_ANSWER_COST, "missed_answer": MISSED_ANSWER_COST},
        "selected_rule": asdict(selected),
        "family_results": [
            {**asdict(item["rule"]), "leave_one_out": item["loo"]} for item in family_results
        ],
        "leave_one_out": selected_loo,
        "baseline": baseline(rows, 0.45),
        "examples": rows,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FEATURES_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = render_markdown(report)
    REPORT_PATH.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Wrote {FEATURES_PATH}")
    print(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
