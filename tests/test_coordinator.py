"""Coordinator tests. Agents, retrieval, and the evidence gate are injected."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from google.genai import errors as genai_errors
from pydantic import ValidationError

from app.agents.researcher import ResearchRun
from app.agents.state import (
    ClarificationRecord,
    RetrievalResult,
    Stage,
    StageStatus,
    WorkflowStatus,
    draft_stage,
    research_stage,
)
from app.agents.triage import TriageAssessment, TriageOutputError
from app.agents.writer import WriterFailure, WriterRun
from app.config import Settings
from app.orchestration.coordinator import analyze_ticket_with_trace, handle_support_ticket
from app.orchestration.workflow import (
    CLIENT_CLASSIFICATION_FAILED,
    CLIENT_MODEL_UNAVAILABLE,
    CLIENT_TIMEOUT,
    CLIENT_WORKFLOW,
    coordinate_ticket,
)
from app.policies.clarification import CLARIFICATION_MESSAGE, decide_clarification
from app.policies.evidence import FALLBACK_MESSAGE, EvidenceValidationResult
from app.schemas import (
    AgentOutcome,
    DocumentSource,
    SupportTicket,
    TicketCategory,
    TicketCompleteness,
    TicketTriage,
)

CLEAR = "Users cannot connect to the application because the database connection keeps timing out."
VAGUE = "It stopped working after yesterday."
THANKS = "Thanks, that fixed it."
SOURCE = "database_connection_failures.md"
PASSAGE = "Check the connection pool size against active connections."
REJECTED_SOURCE = "vpn_and_remote_access.md"


def _cfg(**overrides: object) -> Settings:
    payload = {"google_api_key": "test-key"}
    payload.update(overrides)
    return Settings(**payload)


def _ticket(description: str = CLEAR, ticket_id: str = "T-1") -> SupportTicket:
    return SupportTicket(ticket_id=ticket_id, description=description)


def _chunk(
    source: str = SOURCE,
    distance: float = 0.18,
    text: str = PASSAGE,
    doc_id: str = "database_connection_failures",
) -> dict:
    return {
        "source": source,
        "doc_id": doc_id,
        "chunk_index": 0,
        "category": "database",
        "distance": distance,
        "text": text,
        "excerpt": text,
    }


def _passage(chunk: dict | None = None) -> DocumentSource:
    item = chunk or _chunk()
    return DocumentSource(
        source=item["source"],
        doc_id=item.get("doc_id"),
        chunk_index=item.get("chunk_index"),
        category=item.get("category"),
        distance=item.get("distance"),
        excerpt=item.get("excerpt") or item.get("text"),
    )


def _assessment(
    description: str = CLEAR,
    *,
    troubleshooting: bool = True,
    system: str | None = "database connection",
    symptom: str | None = "keeps timing out",
    category: TicketCategory = TicketCategory.DATABASE,
) -> TriageAssessment:
    classification = TicketTriage(
        category=category if troubleshooting else TicketCategory.OTHER,
        rationale="Classified from the ticket." if troubleshooting else "Not a failure.",
        completeness=TicketCompleteness(
            is_troubleshooting_request=troubleshooting,
            affected_system=system if troubleshooting else None,
            observable_symptom=symptom if troubleshooting else None,
        ),
    )
    decision = decide_clarification(description, classification.completeness)
    return TriageAssessment(
        classification=classification,
        clarification=ClarificationRecord(
            needed=decision.needed,
            questions=list(decision.questions),
            reason=decision.reason,
        ),
        missing=list(decision.missing),
    )


def _research_ok(passages: list[DocumentSource] | None = None, query: str = "database timeout") -> ResearchRun:
    docs = passages if passages is not None else [_passage()]
    return ResearchRun(
        research=research_stage({"requested_search": True, "query": query}),
        retrieval=Stage(
            status=StageStatus.PRODUCED,
            value=RetrievalResult(query=query, passages=docs),
        ),
        model_calls=1,
        tool_executions=1,
    )


def _research_declined() -> ResearchRun:
    return ResearchRun(
        research=research_stage({"requested_search": False}),
        model_calls=1,
        tool_executions=0,
    )


def _writer_ok(source: str = SOURCE, text: str = PASSAGE) -> WriterRun:
    return WriterRun(
        draft=draft_stage(
            {
                "recommendations": [text],
                "cited_sources": [source],
                "summary": "The passage documents a pool check. The cause is not confirmed.",
            }
        ),
        model_calls=1,
    )


def _validation(*, sufficient: bool, chunks: list[dict] | None = None) -> EvidenceValidationResult:
    items = list(chunks or [])
    relevant = items if sufficient else []
    return EvidenceValidationResult(
        sufficient_evidence=sufficient,
        retrieval_performed=True,
        distance_metric="cosine",
        max_distance_threshold=0.3687,
        min_relevant_chunks=1,
        retrieved_count=max(len(items), 1),
        relevant_count=len(relevant),
        relevant_chunks=relevant,
        retrieved_chunks=items,
        reason="accepted in test" if sufficient else "rejected in test",
    )


def _run(
    ticket: SupportTicket | None = None,
    *,
    triage: TriageAssessment | None = None,
    research: ResearchRun | None = None,
    writer: WriterRun | None = None,
    evidence: EvidenceValidationResult | None = None,
    retrieve: MagicMock | None = None,
    cfg: Settings | None = None,
    research_agent: object | None = None,
    writer_agent: object | None = None,
    evidence_gate: object | None = None,
):
    ticket = ticket or _ticket()

    def _mock(value, default):
        if value is None:
            return MagicMock(return_value=default)
        if isinstance(value, MagicMock):
            return value
        return MagicMock(side_effect=value)

    triage_fn = MagicMock(return_value=triage or _assessment(ticket.description))
    research_fn = _mock(research_agent, research or _research_ok())
    writer_fn = _mock(writer_agent, writer or _writer_ok())
    gate_fn = _mock(evidence_gate, evidence or _validation(sufficient=True, chunks=[_chunk()]))
    retrieve_fn = retrieve if retrieve is not None else MagicMock(return_value=[_chunk()])
    state = coordinate_ticket(
        ticket,
        cfg=cfg or _cfg(),
        triage_agent=triage_fn,
        research_agent=research_fn,
        writer_agent=writer_fn,
        evidence_gate=gate_fn,
        retrieve_documents=retrieve_fn,
    )
    return state, triage_fn, research_fn, writer_fn, gate_fn, retrieve_fn


def test_clarification_needed_does_not_retrieve_or_write() -> None:
    ticket = _ticket(VAGUE, "T-VAGUE")
    state, _triage, research, writer, gate, retrieve = _run(
        ticket,
        triage=_assessment(VAGUE, system=None, symptom="stopped working", category=TicketCategory.OTHER),
    )

    research.assert_not_called()
    writer.assert_not_called()
    gate.assert_not_called()
    retrieve.assert_not_called()
    response = state.final.value
    assert state.status is WorkflowStatus.COMPLETED
    assert response is not None
    assert response.outcome is AgentOutcome.CLARIFICATION_NEEDED
    assert response.summary == CLARIFICATION_MESSAGE
    assert response.recommendations == []
    assert response.fallback_triggered is False
    assert response.sufficient_evidence is False
    assert state.research.status is StageStatus.MISSING
    assert state.draft.status is StageStatus.MISSING


def test_non_troubleshooting_request_returns_fallback_without_research() -> None:
    ticket = _ticket(THANKS, "T-NRT")
    state, _triage, research, writer, gate, retrieve = _run(
        ticket,
        triage=_assessment(THANKS, troubleshooting=False, category=TicketCategory.OTHER),
    )

    research.assert_not_called()
    writer.assert_not_called()
    gate.assert_not_called()
    retrieve.assert_not_called()
    response = state.final.value
    assert response is not None
    assert response.outcome is AgentOutcome.FALLBACK
    assert response.recommendations == [FALLBACK_MESSAGE]
    assert response.fallback_triggered is True
    assert response.sufficient_evidence is False


def test_grounded_answer_uses_writer_draft_and_accepted_passages() -> None:
    captured: list[list] = []

    def write(_ticket, _triage, passages, **_kwargs):
        captured.append(list(passages))
        return _writer_ok()

    accepted = _chunk()
    rejected = _chunk(source=REJECTED_SOURCE, distance=0.42, text="Rotate VPN certificates.")
    state, _triage, research, writer, gate, _retrieve = _run(
        writer_agent=write,
        research=_research_ok(passages=[_passage(accepted), _passage(rejected)]),
        evidence=_validation(sufficient=True, chunks=[accepted]),
    )

    research.assert_called_once()
    gate.assert_called_once()
    response = state.final.value
    assert response is not None
    assert response.outcome is AgentOutcome.GROUNDED_ANSWER
    assert response.recommendations == [PASSAGE]
    assert {item.source for item in response.sources} == {SOURCE}
    assert REJECTED_SOURCE not in {item.source for item in response.sources}
    assert [item.source for item in captured[0]] == [SOURCE]
    assert state.draft.status is StageStatus.PRODUCED
    assert state.evidence.status is StageStatus.PRODUCED


def test_writer_is_not_called_when_evidence_is_rejected() -> None:
    writer = MagicMock(side_effect=AssertionError("writer must not run"))
    state, _triage, research, _writer, gate, _retrieve = _run(
        writer_agent=writer,
        evidence=_validation(sufficient=False, chunks=[_chunk(distance=0.90)]),
    )

    research.assert_called_once()
    writer.assert_not_called()
    gate.assert_called_once()
    response = state.final.value
    assert response is not None
    assert response.outcome is AgentOutcome.FALLBACK
    assert response.sufficient_evidence is False
    assert state.draft.status is StageStatus.MISSING
    assert state.evidence.status is StageStatus.REJECTED


@pytest.mark.parametrize("cited", [REJECTED_SOURCE, "never_retrieved.md"])
def test_draft_citing_a_document_outside_the_accepted_evidence_is_not_grounded(cited: str) -> None:
    """The coordinator re-checks citations even if a Writer lets a bad one through."""
    rejected = _chunk(source=REJECTED_SOURCE, distance=0.42, text="Rotate VPN certificates.")
    state, *_ = _run(
        research=_research_ok(passages=[_passage(), _passage(rejected)]),
        evidence=_validation(sufficient=True, chunks=[_chunk()]),
        writer=_writer_ok(source=cited),
    )

    response = state.final.value
    assert response is not None
    assert response.outcome is AgentOutcome.FALLBACK
    assert response.sufficient_evidence is False
    assert response.recommendations != [PASSAGE]
    assert response.sources == []
    assert state.draft.status is StageStatus.MISSING


def test_declined_search_is_fallback_without_writer() -> None:
    writer = MagicMock(side_effect=AssertionError("writer must not run"))
    state, _triage, research, _writer, _gate, _retrieve = _run(
        research=_research_declined(),
        writer_agent=writer,
    )

    research.assert_called_once()
    writer.assert_not_called()
    assert state.final.value is not None
    assert state.final.value.outcome is AgentOutcome.FALLBACK
    assert state.final.value.sufficient_evidence is False


def test_writer_insufficient_draft_is_fallback_not_grounded() -> None:
    writer = WriterRun(
        draft=Stage(),
        failure=WriterFailure.INSUFFICIENT_EVIDENCE,
        reason="accepted passages do not support a recommendation",
        model_calls=1,
    )
    state, _triage, _research, writer_fn, _gate, _retrieve = _run(writer=writer)

    writer_fn.assert_called_once()
    response = state.final.value
    assert response is not None
    assert response.outcome is AgentOutcome.FALLBACK
    assert response.sufficient_evidence is False
    assert state.draft.status is not StageStatus.PRODUCED


def test_invalid_triage_output_is_not_a_grounded_answer() -> None:
    def bad(*_args, **_kwargs):
        raise TriageOutputError("category: missing")

    writer = MagicMock(side_effect=AssertionError("writer must not run"))
    state = coordinate_ticket(
        _ticket(),
        cfg=_cfg(),
        triage_agent=bad,
        research_agent=MagicMock(side_effect=AssertionError("research must not run")),
        writer_agent=writer,
        evidence_gate=MagicMock(side_effect=AssertionError("gate must not run")),
    )

    writer.assert_not_called()
    assert state.status is WorkflowStatus.FAILED
    assert state.error == CLIENT_CLASSIFICATION_FAILED
    assert state.final.status is StageStatus.MISSING


def test_invalid_researcher_output_is_fallback() -> None:
    writer = MagicMock(side_effect=AssertionError("writer must not run"))
    state, _triage, research, _writer, gate, _retrieve = _run(
        research_agent=lambda *_args, **_kwargs: {"not": "a research run"},
        writer_agent=writer,
    )

    research.assert_called_once()
    writer.assert_not_called()
    gate.assert_not_called()
    assert state.status is WorkflowStatus.COMPLETED
    assert state.final.value is not None
    assert state.final.value.outcome is AgentOutcome.FALLBACK


def test_malformed_writer_output_is_fallback_not_grounded() -> None:
    state, _triage, _research, writer, _gate, _retrieve = _run(
        writer_agent=lambda *_args, **_kwargs: {"outcome": "grounded_answer"}
    )

    writer.assert_called_once()
    assert state.final.value is not None
    assert state.final.value.outcome is AgentOutcome.FALLBACK
    assert state.final.value.sufficient_evidence is False


def test_writer_model_outage_is_not_converted_into_a_grounded_answer() -> None:
    writer = WriterRun(
        draft=Stage(status=StageStatus.INVALID, error="model rate limit"),
        failure=WriterFailure.MODEL_ERROR,
        reason="model rate limit",
        model_calls=1,
    )
    state, _triage, _research, _writer, _gate, _retrieve = _run(writer=writer)

    assert state.status is WorkflowStatus.FAILED
    assert state.error == CLIENT_MODEL_UNAVAILABLE
    assert state.final.status is StageStatus.MISSING


def test_timeout_fails_closed_without_retry() -> None:
    calls = {"n": 0}

    def hang(*_args, **_kwargs):
        calls["n"] += 1
        time.sleep(2)
        raise AssertionError("timed-out call must not continue")

    writer = MagicMock(side_effect=AssertionError("writer must not run"))
    state = coordinate_ticket(
        _ticket(),
        cfg=_cfg(model_call_timeout_seconds=0.05, tool_execution_timeout_seconds=0.05),
        triage_agent=hang,
        research_agent=MagicMock(side_effect=AssertionError("research must not run")),
        writer_agent=writer,
        evidence_gate=MagicMock(),
    )

    assert calls["n"] == 1
    writer.assert_not_called()
    assert state.status is WorkflowStatus.FAILED
    assert state.error == CLIENT_TIMEOUT
    assert state.final.status is StageStatus.MISSING


def test_tool_timeout_fails_closed() -> None:
    def research(_ticket, _triage, retrieve_documents=None, **_kwargs):
        retrieve_documents("database timeout")
        raise AssertionError("research continued after the tool timeout")

    def hang(_query):
        time.sleep(2)
        return [_chunk()]

    state = coordinate_ticket(
        _ticket(),
        cfg=_cfg(model_call_timeout_seconds=0.05, tool_execution_timeout_seconds=0.05),
        triage_agent=lambda *_args, **_kwargs: _assessment(),
        research_agent=research,
        writer_agent=MagicMock(side_effect=AssertionError("writer must not run")),
        evidence_gate=MagicMock(),
        retrieve_documents=hang,
    )

    assert state.status is WorkflowStatus.FAILED
    assert state.error == CLIENT_TIMEOUT


def test_exception_is_logged_without_leaking_secrets_to_the_client(caplog: pytest.LogCaptureFixture) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("chroma unavailable GOOGLE_API_KEY=secret")

    with caplog.at_level("ERROR"):
        state = coordinate_ticket(
            _ticket(),
            cfg=_cfg(),
            triage_agent=boom,
            research_agent=MagicMock(),
            writer_agent=MagicMock(),
        )

    assert state.status is WorkflowStatus.FAILED
    assert state.error == CLIENT_WORKFLOW
    assert "GOOGLE_API_KEY" not in (state.error or "")
    assert "secret" not in (state.error or "")
    # The diagnosis stays in the server log; the secret value itself is redacted there too.
    assert "chroma unavailable" in caplog.text
    assert "GOOGLE_API_KEY=[REDACTED]" in caplog.text
    assert "secret" not in caplog.text


def test_missing_api_key_still_surfaces_as_value_error() -> None:
    def missing(*_args, **_kwargs):
        raise ValueError("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key.")

    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        coordinate_ticket(
            _ticket(),
            cfg=_cfg(),
            triage_agent=missing,
            research_agent=MagicMock(),
            writer_agent=MagicMock(),
        )


def test_gemini_client_error_is_not_turned_into_success() -> None:
    def boom(*_args, **_kwargs):
        raise genai_errors.ClientError(
            429,
            {"error": {"message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}},
        )

    with pytest.raises(genai_errors.ClientError):
        coordinate_ticket(
            _ticket(),
            cfg=_cfg(),
            triage_agent=boom,
            research_agent=MagicMock(),
            writer_agent=MagicMock(),
        )


def test_invalid_ticket_is_rejected_before_agents_run() -> None:
    writer = MagicMock()
    with pytest.raises(ValidationError):
        coordinate_ticket(
            {"ticket_id": "T-1", "description": "   "},
            cfg=_cfg(),
            triage_agent=MagicMock(side_effect=AssertionError("triage must not run")),
            writer_agent=writer,
        )
    writer.assert_not_called()


def test_each_agent_runs_at_most_once_on_the_grounded_path() -> None:
    state, triage, research, writer, gate, _retrieve = _run()

    assert triage.call_count == 1
    assert research.call_count == 1
    assert writer.call_count == 1
    assert gate.call_count == 1
    assert state.status is WorkflowStatus.COMPLETED
    assert state.final.value is not None
    assert state.final.value.outcome is AgentOutcome.GROUNDED_ANSWER


def test_handle_support_ticket_raises_safe_error_on_failure() -> None:
    with pytest.raises(RuntimeError, match=CLIENT_CLASSIFICATION_FAILED):
        handle_support_ticket(
            _ticket(),
            cfg=_cfg(),
            triage_agent=lambda *_args, **_kwargs: (_ for _ in ()).throw(TriageOutputError("category: missing")),
            research_agent=MagicMock(),
            writer_agent=MagicMock(),
        )


def test_analyze_trace_records_clarification_without_retrieval() -> None:
    ticket = _ticket(VAGUE, "T-VAGUE")
    research = MagicMock(side_effect=AssertionError("research must not run"))
    trace = analyze_ticket_with_trace(
        ticket,
        cfg=_cfg(),
        triage_agent=lambda *_args, **_kwargs: _assessment(
            VAGUE, system=None, symptom="stopped working", category=TicketCategory.OTHER
        ),
        research_agent=research,
        writer_agent=MagicMock(),
    )

    research.assert_not_called()
    assert trace.response.outcome is AgentOutcome.CLARIFICATION_NEEDED
    assert trace.gemini_requested_retrieval is False
    assert trace.retrieval_performed is False
    assert trace.retrieval_source == "clarification"
    assert trace.clarification_overridden is False
    assert trace.error is None
    assert "affected_system" in (trace.clarification_missing or [])
