"""Boundary and safety tests for the calibrated evidence gate.

Thresholds stay at the 53-ticket calibration values. These tests do not read
the held-out evaluation set and do not retune the cutoffs.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.config import Settings
from app.orchestration.coordinator import generate_grounded_recommendations
from app.policies.evidence import (
    EvidenceValidationResult,
    accepted_passages,
    relevance_features,
    validate_retrieval_evidence,
)
from app.schemas import GroundedAdvice, SupportTicket, TicketCategory

MAX_DISTANCE = 0.3687
MIN_CONTRAST = 0.0442
CONTRAST_RANK = 5

CFG = Settings(
    google_api_key="test",
    relevance_max_distance=MAX_DISTANCE,
    relevance_min_contrast=MIN_CONTRAST,
    relevance_contrast_rank=CONTRAST_RANK,
    min_relevant_chunks=1,
)


def _hit(
    doc: str,
    distance: object,
    *,
    chunk: int = 0,
    text: str | None = None,
    score: float | None = None,
) -> dict:
    item = {
        "source": f"{doc}.md",
        "doc_id": doc,
        "chunk_index": chunk,
        "distance": distance,
        "text": doc if text is None else text,
    }
    if score is not None:
        item["score"] = score
    return item


def _neighborhood(best: float, fifth: float) -> list[dict]:
    """Five distinct documents. The fifth-closest distance is `fifth`."""
    mids = [best + (fifth - best) * step / 4 for step in (1, 2, 3)]
    return [
        _hit(name, distance, text=f"passage {name}")
        for name, distance in zip(
            ("best", "second", "third", "fourth", "fifth"),
            (best, *mids, fifth),
            strict=True,
        )
    ]


def _validate(hits: list) -> EvidenceValidationResult:
    return validate_retrieval_evidence(hits, retrieval_performed=True, cfg=CFG)


def test_calibrated_thresholds_are_unchanged() -> None:
    fields = Settings.model_fields
    assert fields["relevance_max_distance"].default == 0.3687
    assert fields["relevance_min_contrast"].default == 0.0442
    assert fields["relevance_contrast_rank"].default == 5


def test_distance_at_the_calibrated_boundary_passes() -> None:
    hits = _neighborhood(MAX_DISTANCE, 0.50)
    result = _validate(hits)
    assert result.best_distance == MAX_DISTANCE
    assert result.contrast is not None and result.contrast >= MIN_CONTRAST
    assert result.sufficient_evidence is True
    assert [chunk["doc_id"] for chunk in result.relevant_chunks] == ["best"]


def test_distance_just_past_the_boundary_fails() -> None:
    just_over = math.nextafter(MAX_DISTANCE, math.inf)
    result = _validate(_neighborhood(just_over, just_over + 0.20))
    assert result.best_distance == just_over
    assert result.best_distance > MAX_DISTANCE
    assert result.sufficient_evidence is False
    assert result.relevant_chunks == []


def test_contrast_at_the_calibrated_boundary_passes() -> None:
    hits = _neighborhood(0.30, 0.3442)
    result = _validate(hits)
    assert result.contrast is not None
    assert result.contrast >= MIN_CONTRAST
    assert result.sufficient_evidence is True


def test_contrast_just_under_the_boundary_fails() -> None:
    hits = _neighborhood(0.30, 0.3441)
    result = _validate(hits)
    assert result.contrast is not None
    assert result.contrast < MIN_CONTRAST
    assert result.sufficient_evidence is False
    assert "does not stand out" in result.reason


def test_fewer_than_five_distinct_documents_cannot_pass() -> None:
    hits = [
        _hit("only", 0.10, text="a clear symptom"),
        _hit("second", 0.40, text="background"),
        _hit("third", 0.50, text="background"),
        _hit("fourth", 0.60, text="background"),
    ]
    result = _validate(hits)
    assert result.contrast is None
    assert result.sufficient_evidence is False
    assert "4 distinct" in result.reason


def test_repeated_chunks_of_one_document_do_not_create_contrast() -> None:
    hits = [_hit("same", 0.10 + index * 0.01, chunk=index, text=f"chunk {index}") for index in range(5)]
    features = relevance_features(hits, contrast_rank=CONTRAST_RANK)
    assert features.distinct_documents == 1
    assert features.best_distance == 0.10
    assert features.contrast is None
    assert _validate(hits).sufficient_evidence is False


def test_result_order_does_not_change_the_fifth_document() -> None:
    forward = _neighborhood(0.20, 0.40)
    backward = list(reversed(forward))
    assert relevance_features(forward, contrast_rank=5) == relevance_features(
        backward, contrast_rank=5
    )
    assert _validate(backward).sufficient_evidence is True


def test_flat_neighborhood_rejects_a_misleading_top_hit() -> None:
    """A very close top hit still fails when the fifth document is almost as close."""
    hits = [
        _hit("looks_relevant", 0.20, text="top hit that does not stand out"),
        _hit("near_b", 0.21, text="also close"),
        _hit("near_c", 0.22, text="also close"),
        _hit("near_d", 0.23, text="also close"),
        _hit("near_e", 0.24, text="also close"),
    ]
    result = _validate(hits)
    assert result.best_distance == 0.20
    assert result.contrast == 0.24 - 0.20
    assert result.contrast < MIN_CONTRAST
    assert result.sufficient_evidence is False


def test_a_high_similarity_score_cannot_replace_distance() -> None:
    hits = [
        _hit("best", 0.20, text="passage best"),
        _hit("second", 0.42, text="passage second"),
        _hit("third", 0.46, text="passage third"),
        _hit("fourth", 0.50, text="passage fourth"),
        _hit("fifth", 0.54, text="passage fifth"),
    ]
    decoy = _hit("decoy", 0.90, text="high score but far in cosine distance", score=0.99)
    score_only = {
        "source": "score_only.md",
        "doc_id": "score_only",
        "text": "score without a distance",
        "score": 0.99,
    }
    result = _validate([decoy, score_only, *hits])
    assert result.sufficient_evidence is True
    accepted = {chunk["doc_id"] for chunk in result.relevant_chunks}
    assert accepted == {"best"}
    assert "decoy" not in accepted
    assert "score_only" not in accepted


def test_standout_passage_passes_without_an_answer_quality_verdict() -> None:
    """Retrieval geometry is not a judgment that a later answer is faithful."""
    hits = _neighborhood(0.20, 0.50)
    hits[0]["text"] = "Unrelated VPN certificate rotation steps."
    result = _validate(hits)
    assert result.sufficient_evidence is True
    assert not hasattr(result, "faithful")
    assert "faithful" not in result.reason.lower()


def test_empty_retrieval_and_skipped_retrieval_fail_closed() -> None:
    empty = _validate([])
    skipped = validate_retrieval_evidence(_neighborhood(0.20, 0.50), retrieval_performed=False, cfg=CFG)
    assert empty.sufficient_evidence is False
    assert skipped.sufficient_evidence is False
    assert skipped.relevant_chunks == []


def test_malformed_scores_cannot_open_or_crash_the_gate() -> None:
    background = [
        _hit("b", 0.50, text="background b"),
        _hit("c", 0.55, text="background c"),
        _hit("d", 0.60, text="background d"),
        _hit("e", 0.65, text="background e"),
    ]
    malformed = [
        _hit("nan", float("nan"), text="not a number"),
        _hit("pos_inf", float("inf"), text="positive infinity"),
        _hit("neg_inf", float("-inf"), text="negative infinity"),
        _hit("false_distance", False, text="boolean false would be distance 0"),
        _hit("true_distance", True, text="boolean true"),
        _hit("text_distance", "0.01", text="numeric string"),
        _hit("missing", None, text="explicit null"),
        {"source": "no_distance.md", "doc_id": "no_distance", "text": "distance key absent"},
        "not-a-chunk",
        _hit("blank", 0.01, text="   "),
    ]
    result = _validate([*malformed, *background])
    assert result.sufficient_evidence is False
    assert result.relevant_chunks == []
    assert result.best_distance == 0.50


def test_malformed_chunks_are_dropped_from_an_otherwise_valid_set() -> None:
    hits = [
        _hit("best", 0.20, text="passage best"),
        _hit("second", 0.42, text="passage second"),
        _hit("third", 0.46, text="passage third"),
        _hit("fourth", 0.50, text="passage fourth"),
        _hit("fifth", 0.54, text="passage fifth"),
    ]
    hits.append(_hit("neg_inf", float("-inf"), text="must not be accepted"))
    hits.append(_hit("blank", 0.01, text="  "))
    result = _validate(hits)
    assert result.sufficient_evidence is True
    assert result.best_distance == 0.20
    assert [chunk["doc_id"] for chunk in result.relevant_chunks] == ["best"]


def test_writer_receives_only_passages_the_gate_accepted() -> None:
    accepted_text = "Check pool exhaustion against active connections."
    rejected_text = "Rotate VPN certificates for remote contractors."
    poison_text = "Negative infinity must not reach the writer."
    hits = [
        _hit("database_connection_failures", 0.18, text=accepted_text),
        _hit("vpn_and_remote_access", 0.42, text=rejected_text),
        _hit("http_502_bad_gateway", 0.45, text="gateway background"),
        _hit("file_upload_failures", 0.48, text="upload background"),
        _hit("dns_resolution_failures", 0.52, text="dns background"),
        _hit("poison", float("-inf"), text=poison_text),
        _hit("blank", 0.05, text=" "),
    ]
    advice = GroundedAdvice(
        recommendations=["Check the pool size documented above."],
        cited_sources=["database_connection_failures.md"],
        summary="Documented pool check. Cause is not confirmed.",
    )
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(parsed=advice, text=None)
    ticket = SupportTicket(ticket_id="T-1", description="Database connections time out.")

    result = generate_grounded_recommendations(
        ticket,
        category=TicketCategory.DATABASE,
        evidence=hits,
        retrieval_performed=True,
        client=client,
        cfg=CFG,
    )

    prompt = client.models.generate_content.call_args.kwargs["contents"]
    assert accepted_text in prompt
    assert rejected_text not in prompt
    assert poison_text not in prompt
    assert {source.source for source in result.sources} == {"database_connection_failures.md"}
    assert result.sufficient_evidence is True


def test_writer_is_not_called_when_the_gate_rejects() -> None:
    client = MagicMock()
    ticket = SupportTicket(ticket_id="T-2", description="Something is broken.")
    result = generate_grounded_recommendations(
        ticket,
        category=TicketCategory.OTHER,
        evidence=[_hit("only", 0.10, text="too little neighborhood")],
        retrieval_performed=True,
        client=client,
        cfg=CFG,
    )
    assert result.sufficient_evidence is False
    assert result.fallback_triggered is True
    client.models.generate_content.assert_not_called()


def test_forged_validation_cannot_forward_rejected_passages() -> None:
    far = _hit("far", 0.90, text="this passage was not accepted")
    forged = EvidenceValidationResult(
        sufficient_evidence=True,
        retrieval_performed=True,
        distance_metric="cosine",
        max_distance_threshold=MAX_DISTANCE,
        min_relevant_chunks=1,
        retrieved_count=1,
        relevant_count=1,
        relevant_chunks=[far],
    )
    assert accepted_passages(forged) == []

    client = MagicMock()
    result = generate_grounded_recommendations(
        SupportTicket(ticket_id="T-3", description="Database connections time out."),
        category=TicketCategory.DATABASE,
        evidence=[far],
        retrieval_performed=True,
        client=client,
        cfg=CFG,
        validation=forged,
    )
    assert result.sufficient_evidence is False
    client.models.generate_content.assert_not_called()
