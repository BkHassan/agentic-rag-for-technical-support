"""Deterministic evidence validation and safety checks.

Chroma collections in this project are created with `hnsw:space` cosine.
In that space a smaller distance is a closer match. The separate `score`
field (`1 - distance`) is not read by this gate.

Sufficiency rule (both checks must pass, no LLM involved):
1. Closeness: the best usable chunk has cosine distance <= `relevance_max_distance`
   (calibrated 0.3687).
2. Contrast: among distinct documents, sort each document's best distance
   ascending and subtract the closest from the `relevance_contrast_rank`-th
   (calibrated rank 5). That gap must be >= `relevance_min_contrast`
   (calibrated 0.0442). List position is not used.

Fewer than five distinct usable documents leaves contrast unknown, and the
gate rejects the set when a positive contrast threshold is configured.
Chunks with a missing, boolean, non-numeric, or non-finite distance, and
chunks with no passage text, are ignored. They cannot open the gate and are
not accepted passages.

Passing this gate means only that the retrieved distances met the rule.
It does not judge whether a later generated answer is faithful.

Thresholds come from the 53-ticket calibration (`python -m evaluation.calibrate_relevance`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from numbers import Real
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


def _passage_text(item: dict[str, Any]) -> str:
    for key in ("text", "document"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _finite_distance(item: Any) -> float | None:
    """Cosine distance, or None when the score cannot be used.

    Booleans are rejected because `bool` is a subclass of `int` (`False` would
    otherwise become distance 0). Strings are not coerced. NaN and infinities
    are rejected because comparisons with them do not implement an ordering.
    """
    if not isinstance(item, dict):
        return None
    value = item.get("distance")
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _document_key(item: dict[str, Any]) -> str:
    return str(item.get("doc_id") or item.get("source") or item.get("id") or "")


def select_relevant_chunks(
    results: list[dict[str, Any]],
    *,
    max_distance: float,
) -> list[dict[str, Any]]:
    """Return usable chunks whose cosine distance is at or below the threshold."""
    relevant: list[dict[str, Any]] = []
    for item in results:
        distance = _finite_distance(item)
        if distance is None or not _passage_text(item):
            continue
        if distance <= max_distance:
            relevant.append(item)
    # Prefer stronger matches first (lower cosine distance).
    return sorted(relevant, key=lambda item: _finite_distance(item) or 0.0)


def relevance_features(
    results: list[dict[str, Any]] | None,
    *,
    contrast_rank: int,
) -> RelevanceFeatures:
    """
    Compute closeness and contrast over distinct documents.

    Each document contributes its closest usable chunk. Distances are sorted
    ascending, so the order Chroma returned does not matter. Contrast is the
    rank-th distance minus the closest. It is None when fewer than
    `contrast_rank` distinct documents were usable, because the background
    cannot be measured.
    """
    best_per_document: dict[str, float] = {}
    for item in results or []:
        distance = _finite_distance(item)
        if distance is None or not isinstance(item, dict) or not _passage_text(item):
            continue
        key = _document_key(item)
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
    best = features.best_distance
    if best is None or not math.isfinite(best) or best > max_distance:
        return False
    if min_contrast <= 0:
        return True
    contrast = features.contrast
    return contrast is not None and math.isfinite(contrast) and contrast >= min_contrast


def evidence_reason_code(validation: EvidenceValidationResult) -> str:
    """
    Stable code for a gate decision, read from the result's structured fields in the
    same order validate_retrieval_evidence applies its checks. For logs and metrics.
    """
    if validation.sufficient_evidence:
        return "accepted"
    if not validation.retrieval_performed:
        return "no_retrieval"
    if validation.retrieved_count == 0:
        return "empty_retrieval"
    if validation.relevant_count < validation.min_relevant_chunks:
        return "distance_above_threshold"
    if validation.contrast is None:
        return "too_few_documents_for_contrast"
    return "low_contrast"


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
    try:
        chunks = list(results or [])
    except TypeError:
        chunks = []
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


def accepted_passages(validation: EvidenceValidationResult) -> list[dict[str, Any]]:
    """Passages the gate accepted. Empty when the set fails or none remain eligible.

    Callers that generate an answer must use this list. A true sufficiency flag
    is not permission to forward the raw retrieval payload.
    """
    if not validation.sufficient_evidence:
        return []
    accepted = select_relevant_chunks(
        validation.relevant_chunks,
        max_distance=validation.max_distance_threshold,
    )
    if len(accepted) < validation.min_relevant_chunks:
        return []
    return accepted


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
