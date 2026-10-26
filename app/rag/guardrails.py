"""Deterministic evidence validation and safety checks.

Chroma collections in this project use cosine distance:
- lower distance => closer / more relevant match
- distance is NOT a similarity score where larger is better

The host application must validate retrieval results before asking Gemini
to produce final troubleshooting recommendations.

Sufficiency rule (both checks must pass, no LLM involved):
1. Closeness: the best chunk has cosine distance <= `relevance_max_distance`.
2. Contrast: the best document stands out from the background. The distance of
   the `relevance_contrast_rank`-th closest distinct document minus the best
   distance must be >= `relevance_min_contrast`. Vague or out-of-corpus tickets
   sit at a similar distance from many documents, so they have low contrast.

The thresholds are calibrated on labeled examples by `python -m evals.calibrate_relevance`.
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
class RelevanceFeatures:
    """Retrieval-score features used by the sufficiency rule."""

    best_distance: float | None
    contrast: float | None
    distinct_documents: int


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
    min_contrast_threshold: float = 0.0
    contrast_rank: int = 1
    best_distance: float | None = None
    contrast: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sufficient_evidence": self.sufficient_evidence,
            "retrieval_performed": self.retrieval_performed,
            "distance_metric": self.distance_metric,
            "max_distance_threshold": self.max_distance_threshold,
            "min_contrast_threshold": self.min_contrast_threshold,
            "contrast_rank": self.contrast_rank,
            "min_relevant_chunks": self.min_relevant_chunks,
            "retrieved_count": self.retrieved_count,
            "relevant_count": self.relevant_count,
            "best_distance": self.best_distance,
            "contrast": self.contrast,
            "reason": self.reason,
            "relevant": self.relevant_chunks,
            "all": self.retrieved_chunks,
        }


def _has_finite_distance(item: dict[str, Any]) -> bool:
    value = item.get("distance")
    return isinstance(value, (int, float))


def _document_key(item: dict[str, Any]) -> str:
    return str(item.get("doc_id") or item.get("source") or item.get("id") or "")


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


def relevance_features(
    results: list[dict[str, Any]] | None,
    *,
    contrast_rank: int,
) -> RelevanceFeatures:
    """
    Compute closeness and contrast over distinct documents.

    Contrast is None when fewer than `contrast_rank` distinct documents were
    retrieved, because the background cannot be measured.
    """
    best_per_document: dict[str, float] = {}
    for item in results or []:
        if not _has_finite_distance(item):
            continue
        key = _document_key(item)
        distance = float(item["distance"])
        if key not in best_per_document or distance < best_per_document[key]:
            best_per_document[key] = distance

    ranked = sorted(best_per_document.values())
    if not ranked:
        return RelevanceFeatures(best_distance=None, contrast=None, distinct_documents=0)

    rank = max(1, contrast_rank)
    contrast = ranked[rank - 1] - ranked[0] if len(ranked) >= rank else None
    return RelevanceFeatures(
        best_distance=ranked[0],
        contrast=contrast,
        distinct_documents=len(ranked),
    )


def passes_relevance_rule(
    features: RelevanceFeatures,
    *,
    max_distance: float,
    min_contrast: float,
) -> bool:
    """Apply the calibrated closeness + contrast rule to precomputed features."""
    if features.best_distance is None or features.best_distance > max_distance:
        return False
    if min_contrast <= 0:
        return True
    return features.contrast is not None and features.contrast >= min_contrast


def validate_retrieval_evidence(
    results: list[dict[str, Any]] | None,
    *,
    retrieval_performed: bool,
    max_distance: float | None = None,
    min_contrast: float | None = None,
    contrast_rank: int | None = None,
    min_relevant_chunks: int | None = None,
    cfg: Settings | None = None,
) -> EvidenceValidationResult:
    """
    Deterministically decide whether retrieval evidence is good enough.

    Rules:
    1. Retrieval must have been performed. Absence of a Gemini tool call is
       never treated as sufficient evidence.
    2. At least `min_relevant_chunks` chunks have cosine distance <= `max_distance`.
    3. The best document stands out from the background by at least `min_contrast`.
    4. Empty or unscored result sets are insufficient.
    """
    cfg = cfg or settings
    threshold = cfg.relevance_max_distance if max_distance is None else max_distance
    contrast_threshold = cfg.relevance_min_contrast if min_contrast is None else min_contrast
    rank = cfg.relevance_contrast_rank if contrast_rank is None else contrast_rank
    min_needed = (
        cfg.min_relevant_chunks if min_relevant_chunks is None else min_relevant_chunks
    )
    chunks = list(results or [])
    relevant = select_relevant_chunks(chunks, max_distance=threshold) if retrieval_performed else []
    features = relevance_features(chunks, contrast_rank=rank)

    def result(sufficient: bool, reason: str) -> EvidenceValidationResult:
        return EvidenceValidationResult(
            sufficient_evidence=sufficient,
            retrieval_performed=retrieval_performed,
            distance_metric="cosine",
            max_distance_threshold=threshold,
            min_relevant_chunks=min_needed,
            retrieved_count=len(chunks),
            relevant_count=len(relevant),
            relevant_chunks=relevant,
            retrieved_chunks=chunks,
            reason=reason,
            min_contrast_threshold=contrast_threshold,
            contrast_rank=rank,
            best_distance=features.best_distance,
            contrast=features.contrast,
        )

    if not retrieval_performed:
        return result(
            False,
            "No retrieval was performed. The absence of a Gemini tool call "
            "is not proof that sufficient evidence exists.",
        )
    if not chunks:
        return result(False, "Retrieval ran but returned no document chunks.")
    if len(relevant) < min_needed:
        best = (
            f"; best distance was {features.best_distance:.4f}."
            if features.best_distance is not None
            else "."
        )
        return result(
            False,
            f"Insufficient relevant evidence: {len(relevant)} chunk(s) met cosine "
            f"distance <= {threshold} (required {min_needed}){best}",
        )
    if not passes_relevance_rule(
        features, max_distance=threshold, min_contrast=contrast_threshold
    ):
        if features.contrast is None:
            detail = (
                f"only {features.distinct_documents} distinct document(s) were retrieved; "
                f"{rank} are needed to measure contrast."
            )
        else:
            detail = (
                f"contrast {features.contrast:.4f} is below {contrast_threshold} "
                f"(distance gap between the best and the #{rank} document)."
            )
        return result(
            False,
            "Best match does not stand out from unrelated documents: " + detail,
        )

    contrast_note = (
        f" and contrast {features.contrast:.4f} >= {contrast_threshold}"
        if contrast_threshold > 0 and features.contrast is not None
        else ""
    )
    return result(
        True,
        f"Accepted {len(relevant)} relevant chunk(s) with cosine distance "
        f"<= {threshold}{contrast_note}.",
    )


def is_relevant(
    results: list[dict[str, Any]] | None,
    *,
    max_distance: float | None = None,
    min_relevant_chunks: int | None = None,
    cfg: Settings | None = None,
) -> bool:
    """True when retrieved results pass the full sufficiency rule."""
    return validate_retrieval_evidence(
        results,
        retrieval_performed=True,
        max_distance=max_distance,
        min_relevant_chunks=min_relevant_chunks,
        cfg=cfg,
    ).sufficient_evidence


def should_return_fallback(
    results: list[dict[str, Any]] | None,
    *,
    retrieval_performed: bool,
    cfg: Settings | None = None,
) -> bool:
    """Application safety gate. Fails closed when retrieval never ran."""
    return not validate_retrieval_evidence(
        results,
        retrieval_performed=retrieval_performed,
        cfg=cfg,
    ).sufficient_evidence


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
