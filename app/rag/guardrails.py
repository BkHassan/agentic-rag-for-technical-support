"""Deterministic evidence validation and safety checks.

Chroma collections in this project use cosine distance:
- lower distance => closer / more relevant match
- distance is NOT a similarity score where larger is better

The host application must validate retrieval results before asking Gemini
to produce final troubleshooting recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, settings

# Fixed application-owned fallback. Returned directly when evidence checks fail —
# do not ask Gemini whether it has enough evidence.
FALLBACK_MESSAGE = (
    "Insufficient supporting documentation was found to provide "
    "reliable troubleshooting recommendations. Please review "
    "the ticket manually."
)


@dataclass(frozen=True)
class EvidenceValidationResult:
    """Outcome of deterministic evidence checks (no LLM involved)."""

    sufficient_evidence: bool
    retrieval_performed: bool
    distance_metric: str
    max_distance_threshold: float
    min_relevant_chunks: int
    retrieved_count: int
    relevant_count: int
    relevant_chunks: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sufficient_evidence": self.sufficient_evidence,
            "retrieval_performed": self.retrieval_performed,
            "distance_metric": self.distance_metric,
            "max_distance_threshold": self.max_distance_threshold,
            "min_relevant_chunks": self.min_relevant_chunks,
            "retrieved_count": self.retrieved_count,
            "relevant_count": self.relevant_count,
            "reason": self.reason,
            "relevant": self.relevant_chunks,
            "all": self.retrieved_chunks,
        }


def _has_finite_distance(item: dict[str, Any]) -> bool:
    value = item.get("distance")
    return isinstance(value, (int, float))


def select_relevant_chunks(
    results: list[dict[str, Any]],
    *,
    max_distance: float,
) -> list[dict[str, Any]]:
    """Return chunks whose cosine distance is at or below the threshold."""
    relevant: list[dict[str, Any]] = []
    for item in results:
        if not _has_finite_distance(item):
            continue
        if float(item["distance"]) <= max_distance:
            relevant.append(item)
    # Prefer stronger matches first (lower cosine distance).
    return sorted(relevant, key=lambda item: float(item["distance"]))


def is_relevant(
    results: list[dict[str, Any]] | None,
    *,
    max_distance: float | None = None,
    min_relevant_chunks: int | None = None,
    cfg: Settings | None = None,
) -> bool:
    """
    Relevance check for MVP.

    Chroma may return nearest neighbors that are still irrelevant. For cosine
    space, a chunk is relevant when distance <= configured threshold (lower is
    better). Calibrate `relevance_max_distance` on your eval set.
    """
    cfg = cfg or settings
    threshold = cfg.relevance_max_distance if max_distance is None else max_distance
    min_needed = (
        cfg.min_relevant_chunks if min_relevant_chunks is None else min_relevant_chunks
    )
    chunks = list(results or [])
    if not chunks:
        return False
    relevant = select_relevant_chunks(chunks, max_distance=threshold)
    return len(relevant) >= min_needed


def should_return_fallback(
    results: list[dict[str, Any]] | None,
    *,
    retrieval_performed: bool,
    cfg: Settings | None = None,
) -> bool:
    """
    Application safety gate:

        if not results or not is_relevant(results):
            return FALLBACK_MESSAGE

    Also fails closed when retrieval never ran.
    """
    if not retrieval_performed:
        return True
    chunks = list(results or [])
    if not chunks:
        return True
    return not is_relevant(chunks, cfg=cfg)


def validate_retrieval_evidence(
    results: list[dict[str, Any]] | None,
    *,
    retrieval_performed: bool,
    max_distance: float | None = None,
    min_relevant_chunks: int | None = None,
    cfg: Settings | None = None,
) -> EvidenceValidationResult:
    """
    Deterministically decide whether retrieval evidence is good enough.

    Rules (MVP):
    1. Retrieval must have been performed. If Gemini never requested a search and
       the application also did not retrieve, evidence is insufficient.
       Absence of a tool call is never treated as sufficient evidence.
    2. At least `min_relevant_chunks` results must have cosine distance
       <= `max_distance` (lower is better for cosine space).
    3. Empty or unscored result sets are insufficient.
    """
    cfg = cfg or settings
    threshold = cfg.relevance_max_distance if max_distance is None else max_distance
    min_needed = (
        cfg.min_relevant_chunks if min_relevant_chunks is None else min_relevant_chunks
    )
    chunks = list(results or [])

    if should_return_fallback(
        chunks,
        retrieval_performed=retrieval_performed,
        cfg=cfg,
    ):
        if not retrieval_performed:
            reason = (
                "No retrieval was performed. The absence of a Gemini tool call "
                "is not proof that sufficient evidence exists."
            )
            relevant: list[dict[str, Any]] = []
        elif not chunks:
            reason = "Retrieval ran but returned no document chunks."
            relevant = []
        else:
            relevant = select_relevant_chunks(chunks, max_distance=threshold)
            best = None
            scored = [item for item in chunks if _has_finite_distance(item)]
            if scored:
                best = min(float(item["distance"]) for item in scored)
            reason = (
                f"Insufficient relevant evidence: {len(relevant)} chunk(s) met "
                f"cosine distance <= {threshold} (required {min_needed})"
                + (f"; best distance was {best:.4f}." if best is not None else ".")
            )
        return EvidenceValidationResult(
            sufficient_evidence=False,
            retrieval_performed=retrieval_performed,
            distance_metric="cosine",
            max_distance_threshold=threshold,
            min_relevant_chunks=min_needed,
            retrieved_count=len(chunks),
            relevant_count=len(relevant),
            relevant_chunks=relevant,
            retrieved_chunks=chunks,
            reason=reason,
        )

    relevant = select_relevant_chunks(chunks, max_distance=threshold)
    return EvidenceValidationResult(
        sufficient_evidence=True,
        retrieval_performed=True,
        distance_metric="cosine",
        max_distance_threshold=threshold,
        min_relevant_chunks=min_needed,
        retrieved_count=len(chunks),
        relevant_count=len(relevant),
        relevant_chunks=relevant,
        retrieved_chunks=chunks,
        reason=(
            f"Accepted {len(relevant)} relevant chunk(s) with cosine distance "
            f"<= {threshold}."
        ),
    )


# Backward-compatible alias used by older call sites / tests.
def assess_evidence(
    evidence: list[dict[str, Any]],
    *,
    retrieval_performed: bool = True,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    """Wrapper returning a plain dict for convenience."""
    return validate_retrieval_evidence(
        evidence,
        retrieval_performed=retrieval_performed,
        cfg=cfg,
    ).to_dict()
