"""Tests for the calibrated retrieval sufficiency rule (no live API required)."""

from __future__ import annotations

from unittest.mock import patch

from app.agent import collect_evidence_for_ticket
from app.config import Settings
from app.policies.evidence import relevance_features, validate_retrieval_evidence
from app.schemas import SupportTicket
from evaluation.calibrate_relevance import decide, fit, fit_family
from tests.retrieval_fixtures import background_docs

CFG = Settings(
    google_api_key="test",
    relevance_max_distance=0.37,
    relevance_min_contrast=0.044,
    relevance_contrast_rank=5,
)


def _hit(doc: str, distance: float, chunk: int = 0) -> dict:
    return {"source": f"{doc}.md", "doc_id": doc, "chunk_index": chunk, "distance": distance, "text": doc}


def test_features_use_best_chunk_per_document() -> None:
    hits = [
        _hit("dns", 0.25),
        _hit("dns", 0.27, chunk=1),
        _hit("vpn", 0.30),
        _hit("ip", 0.33),
    ]
    features = relevance_features(hits, contrast_rank=3)
    assert features.best_distance == 0.25
    assert features.distinct_documents == 3
    assert round(features.contrast, 4) == 0.08


def test_contrast_is_unknown_with_too_few_documents() -> None:
    features = relevance_features([_hit("dns", 0.25), _hit("vpn", 0.30)], contrast_rank=5)
    assert features.contrast is None


def test_specific_ticket_that_stands_out_is_sufficient() -> None:
    hits = [_hit("database_connection_failures", 0.29)] + background_docs(0.38)
    result = validate_retrieval_evidence(hits, retrieval_performed=True, cfg=CFG)
    assert result.sufficient_evidence is True
    assert [chunk["doc_id"] for chunk in result.relevant_chunks] == ["database_connection_failures"]


def test_vague_ticket_close_to_everything_falls_back() -> None:
    """Shape of 'The application is broken for some users': close, but no document stands out."""
    hits = [
        _hit("authentication_failures", 0.300),
        _hit("session_tokens_and_sso", 0.306),
        _hit("vpn_and_remote_access", 0.321),
        _hit("file_upload_failures", 0.339),
        _hit("http_502_bad_gateway", 0.340),
    ]
    result = validate_retrieval_evidence(hits, retrieval_performed=True, cfg=CFG)
    assert result.sufficient_evidence is False
    assert "does not stand out" in result.reason


def test_out_of_corpus_ticket_falls_back_on_distance() -> None:
    hits = [_hit("http_502_bad_gateway", 0.393)] + background_docs(0.45)
    result = validate_retrieval_evidence(hits, retrieval_performed=True, cfg=CFG)
    assert result.sufficient_evidence is False
    assert result.relevant_count == 0


def test_gate_scores_the_original_ticket_text() -> None:
    ticket = SupportTicket(ticket_id="T-1", description="Something is wrong with login.")
    tool_outcome = {"used_knowledge_base": True, "evidence": [_hit("authentication_failures", 0.2)]}
    with patch("app.orchestration.coordinator.retrieve", return_value=[]) as retrieve:
        evidence, performed, source = collect_evidence_for_ticket(ticket, tool_outcome, cfg=CFG)
    retrieve.assert_called_once_with(
        "Something is wrong with login.", top_k=CFG.relevance_candidate_pool, cfg=CFG
    )
    assert evidence == []
    assert performed is True
    assert source == "gemini_tool"


def test_gate_reuses_hits_already_scored_for_the_ticket_text() -> None:
    ticket = SupportTicket(ticket_id="T-1", description="Something is wrong with login.")
    tool_outcome = {"used_knowledge_base": True}
    prior = [_hit("authentication_failures", 0.2)]
    with patch("app.orchestration.coordinator.retrieve") as retrieve:
        evidence, performed, source = collect_evidence_for_ticket(
            ticket, tool_outcome, cfg=CFG, prior_ticket_hits=prior
        )
    retrieve.assert_not_called()
    assert evidence == prior
    assert performed is True
    assert source == "gemini_tool"


def _row(ticket_id: str, label: str, distance: float, contrast: float) -> dict:
    return {
        "ticket_id": ticket_id,
        "label": label,
        "best_distance": distance,
        "contrast": {str(rank): contrast for rank in (2, 3, 4, 5)},
    }


def test_calibration_needs_both_checks_when_vague_tickets_are_close() -> None:
    rows = (
        [_row(f"A{i}", "answer", 0.25 + i * 0.01, 0.10 + i * 0.005) for i in range(8)]
        + [_row(f"O{i}", "fallback", 0.42 + i * 0.01, 0.06 + i * 0.003) for i in range(6)]
        + [_row(f"V{i}", "fallback", 0.28 + i * 0.01, 0.01 + i * 0.003) for i in range(6)]
    )
    distance_only = fit_family(rows, "distance", None)
    assert distance_only.unsafe + distance_only.missed > 0

    rule, loo, _ = fit(rows)
    assert rule.family == "distance+contrast"
    assert rule.unsafe == 0 and rule.missed == 0
    assert not loo["unsafe_answers"]
    assert all(decide(rule, row) == (row["label"] == "answer") for row in rows)
