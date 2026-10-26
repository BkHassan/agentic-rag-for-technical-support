"""Shared ticket-run state. No Gemini calls."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.agents.state import (
    ClarificationRecord,
    DraftAnswer,
    EvidenceDecision,
    ResearchRequest,
    RetrievalResult,
    Stage,
    StageStatus,
    StageTiming,
    TicketRunState,
    TimedStage,
    WorkflowStatus,
    clarification_stage,
    draft_stage,
    evidence_stage,
    research_stage,
)
from app.schemas import AgentOutcome, AgentResponse, DocumentSource, TicketCategory, TicketCompleteness, TicketTriage


def _when() -> datetime:
    return datetime(2026, 10, 26, tzinfo=timezone.utc)


def _ticket_state(**overrides: object) -> TicketRunState:
    payload = {
        "ticket": {"ticket_id": "T-1", "description": "The database connection times out."},
        "received_at": _when(),
    }
    payload.update(overrides)
    return TicketRunState.model_validate(payload)


def _passage() -> dict[str, object]:
    return {"source": "database_timeouts_and_locks.md", "distance": 0.22, "excerpt": "Check the pool."}


def _triage() -> TicketTriage:
    return TicketTriage(
        category=TicketCategory.DATABASE,
        rationale="The ticket describes a database timeout.",
        completeness=TicketCompleteness(
            is_troubleshooting_request=True,
            affected_system="database",
            observable_symptom="connection times out",
        ),
    )


def _response(outcome: AgentOutcome) -> AgentResponse:
    grounded = outcome is AgentOutcome.GROUNDED_ANSWER
    clarifying = outcome is AgentOutcome.CLARIFICATION_NEEDED
    return AgentResponse(
        ticket_id="T-1",
        category=TicketCategory.DATABASE,
        outcome=outcome,
        clarifying_questions=["Which database?"] if clarifying else [],
        recommendations=["Check the pool."] if grounded else [],
        sources=[DocumentSource.model_validate(_passage())] if grounded else [],
        sufficient_evidence=grounded,
        fallback_triggered=outcome is AgentOutcome.FALLBACK,
        summary="Documented pool guidance." if grounded else None,
    )


def test_received_ticket_has_missing_stages_and_round_trips() -> None:
    state = _ticket_state()

    assert state.status is WorkflowStatus.RECEIVED
    assert state.triage.status is StageStatus.MISSING
    assert state.triage.value is None
    assert state.research.status is StageStatus.MISSING
    assert state.final.status is StageStatus.MISSING
    restored = TicketRunState.model_validate(state.model_dump(mode="json"))
    assert restored.ticket.ticket_id == "T-1"
    assert restored.received_at == _when()


def test_completed_grounded_run_keeps_each_produced_value() -> None:
    state = _ticket_state(
        status=WorkflowStatus.COMPLETED,
        triage=Stage(status=StageStatus.PRODUCED, value=_triage()),
        research=research_stage({"requested_search": True, "query": "database connection timeout"}),
        retrieval=Stage(
            status=StageStatus.PRODUCED,
            value=RetrievalResult(query="The database connection times out.", passages=[DocumentSource.model_validate(_passage())]),
        ),
        evidence=evidence_stage({"sufficient": True, "reason": "One document stands out.", "accepted": [_passage()]}),
        draft=draft_stage(
            {
                "recommendations": ["Check the pool."],
                "cited_sources": ["database_timeouts_and_locks.md"],
                "summary": "The timeout is documented.",
            }
        ),
        final=Stage(status=StageStatus.PRODUCED, value=_response(AgentOutcome.GROUNDED_ANSWER)),
        timings=[{"stage": TimedStage.TRIAGE, "elapsed_ms": 12.5}],
        gemini_calls=2,
        embed_api_calls=1,
    )

    assert state.research.value is not None and state.research.value.query == "database connection timeout"
    assert state.evidence.status is StageStatus.PRODUCED
    assert state.draft.value is not None and state.draft.value.recommendations == ["Check the pool."]
    assert state.final.value is not None and state.final.value.outcome is AgentOutcome.GROUNDED_ANSWER
    assert state.timings == [StageTiming(stage=TimedStage.TRIAGE, elapsed_ms=12.5)]


def test_clarification_does_not_keep_research_or_a_draft() -> None:
    state = _ticket_state(
        status=WorkflowStatus.COMPLETED,
        triage=Stage(status=StageStatus.PRODUCED, value=_triage()),
        clarification=clarification_stage(
            {"needed": True, "questions": ["What fails?"], "reason": "No trusted symptom."}
        ),
        final=Stage(status=StageStatus.PRODUCED, value=_response(AgentOutcome.CLARIFICATION_NEEDED)),
    )

    assert state.clarification.status is StageStatus.PRODUCED
    assert state.research.status is StageStatus.MISSING
    assert state.draft.status is StageStatus.MISSING


def test_declined_search_and_rejected_evidence_are_not_produced() -> None:
    research = research_stage({"requested_search": False})
    evidence = evidence_stage({"sufficient": False, "reason": "No document stands out.", "accepted": []})

    assert research.status is StageStatus.REJECTED
    assert research.value == ResearchRequest(requested_search=False)
    assert evidence.status is StageStatus.REJECTED
    assert evidence.value == EvidenceDecision(sufficient=False, reason="No document stands out.")


def test_missing_required_agent_fields_are_invalid_and_drop_the_payload() -> None:
    research = research_stage({"requested_search": True})
    draft = draft_stage({"recommendations": [], "summary": "Nothing to say."})

    assert research.status is StageStatus.INVALID
    assert research.value is None
    assert research.error == "value: value_error"
    assert draft.status is StageStatus.INVALID
    assert draft.value is None
    assert draft.error == "recommendations: too_short"


def test_missing_triage_category_fails_validation() -> None:
    with pytest.raises(ValidationError) as caught:
        Stage[TicketTriage].model_validate({"status": "produced", "value": {"rationale": "Database."}})

    assert any(error["loc"][-1] == "category" or "category" in error["loc"] for error in caught.value.errors())


def test_blank_writer_steps_are_not_coerced_into_a_draft() -> None:
    with pytest.raises(ValidationError):
        DraftAnswer.model_validate(
            {"recommendations": ["  "], "cited_sources": ["notes.md"], "summary": "Documented."}
        )

    stage = draft_stage({"recommendations": ["  "], "cited_sources": ["notes.md"], "summary": "Documented."})
    assert stage.status is StageStatus.INVALID
    assert stage.value is None


def test_search_query_and_clarification_questions_are_required_when_claimed() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate({"requested_search": True, "query": "   "})
    with pytest.raises(ValidationError):
        ClarificationRecord.model_validate({"needed": True, "questions": [], "reason": "Too vague."})


def test_produced_stage_cannot_omit_its_value() -> None:
    with pytest.raises(ValidationError):
        Stage[ResearchRequest].model_validate({"status": "produced"})


def test_rejected_evidence_cannot_carry_accepted_passages() -> None:
    stage = evidence_stage(
        {"sufficient": False, "reason": "Too far from every document.", "accepted": [_passage()]}
    )

    assert stage.status is StageStatus.INVALID
    assert stage.value is None


def test_grounded_completion_requires_evidence_and_a_draft() -> None:
    with pytest.raises(ValidationError):
        _ticket_state(
            status=WorkflowStatus.COMPLETED,
            final=Stage(status=StageStatus.PRODUCED, value=_response(AgentOutcome.GROUNDED_ANSWER)),
        )


def test_workflow_error_cannot_contain_a_credential_marker() -> None:
    with pytest.raises(ValidationError):
        _ticket_state(status=WorkflowStatus.FAILED, error="GOOGLE_API_KEY was rejected")


def test_failed_run_requires_an_error_and_has_no_final_response() -> None:
    state = _ticket_state(status=WorkflowStatus.FAILED, error="Triage timed out.")

    assert state.final.status is StageStatus.MISSING
    with pytest.raises(ValidationError):
        _ticket_state(status=WorkflowStatus.FAILED)


def test_unknown_agent_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        DraftAnswer.model_validate(
            {
                "recommendations": ["Check the pool."],
                "summary": "Documented.",
                "api_key": "not-stored",
            }
        )
