"""Typed state for one ticket analysis. Nothing here is stored after the request."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.schemas import (
    AgentOutcome,
    AgentResponse,
    DocumentSource,
    SupportTicket,
    TicketTriage,
)

ValueT = TypeVar("ValueT", bound=BaseModel)

_ERROR_MAX_LENGTH = 300
_SECRET_MARKERS = ("GOOGLE_API_KEY", "AIza", "api_key=")


class StageStatus(str, Enum):
    """What the coordinator is allowed to do with a stage slot."""

    MISSING = "missing"
    INVALID = "invalid"
    REJECTED = "rejected"
    PRODUCED = "produced"


class WorkflowStatus(str, Enum):
    RECEIVED = "received"
    TRIAGED = "triaged"
    CLARIFYING = "clarifying"
    RESEARCHING = "researching"
    EVIDENCE_CHECKED = "evidence_checked"
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"


class TimedStage(str, Enum):
    TRIAGE = "triage"
    RESEARCH = "research"
    RETRIEVAL = "retrieval"
    WRITER = "writer"


def _bounded_error(text: str) -> str:
    compact = " ".join(text.split())
    if any(marker in compact for marker in _SECRET_MARKERS):
        raise ValueError("error text must not contain credentials")
    return compact[:_ERROR_MAX_LENGTH]


class Stage(BaseModel, Generic[ValueT]):
    """One agent or gate result. Invalid output is not stored as a value."""

    model_config = ConfigDict(extra="forbid")

    status: StageStatus = StageStatus.MISSING
    value: ValueT | None = None
    error: str | None = Field(default=None, max_length=_ERROR_MAX_LENGTH)

    @field_validator("error")
    @classmethod
    def error_is_bounded(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_error(value)

    @model_validator(mode="after")
    def status_matches_payload(self) -> Stage[ValueT]:
        if self.status is StageStatus.MISSING:
            if self.value is not None or self.error is not None:
                raise ValueError("a missing stage has no value and no error")
        elif self.status is StageStatus.INVALID:
            if self.value is not None:
                raise ValueError("an invalid stage does not keep the rejected payload")
            if not self.error:
                raise ValueError("an invalid stage requires an error")
        else:
            if self.value is None:
                raise ValueError(f"a {self.status.value} stage requires a value")
            if self.error is not None:
                raise ValueError(f"a {self.status.value} stage does not store an error")
        return self


class ClarificationRecord(BaseModel):
    """Python clarification decision. Questions are stored only when the reporter is asked."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    needed: bool
    questions: list[str] = Field(default_factory=list)
    reason: str = Field(..., min_length=1)

    @field_validator("questions")
    @classmethod
    def questions_are_not_blank(cls, value: list[str]) -> list[str]:
        return _non_blank_items(value)

    @model_validator(mode="after")
    def questions_follow_the_decision(self) -> ClarificationRecord:
        if self.needed and not self.questions:
            raise ValueError("clarification requires at least one question")
        if not self.needed and self.questions:
            raise ValueError("a ticket that does not need clarification has no questions")
        return self


class ResearchRequest(BaseModel):
    """Researcher output. The query is kept only when search was requested."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requested_search: bool
    query: str | None = None

    @model_validator(mode="after")
    def query_follows_the_request(self) -> ResearchRequest:
        if self.requested_search and not self.query:
            raise ValueError("a search request requires a query")
        if not self.requested_search and self.query:
            raise ValueError("a declined search has no query")
        return self


class RetrievalResult(BaseModel):
    """The one Chroma lookup for this ticket, scored on the original ticket text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(..., min_length=1)
    passages: list[DocumentSource] = Field(default_factory=list)


class EvidenceDecision(BaseModel):
    """Python evidence gate. Accepted passages are empty when the gate rejects the ticket."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sufficient: bool
    reason: str = Field(..., min_length=1)
    accepted: list[DocumentSource] = Field(default_factory=list)
    # Machine-readable form of `reason` for diagnostics (see evidence_reason_code).
    reason_code: str | None = Field(default=None, max_length=64)

    @field_validator("accepted")
    @classmethod
    def accepted_passages_have_text(cls, value: list[DocumentSource]) -> list[DocumentSource]:
        for passage in value:
            if passage.distance is None:
                raise ValueError("an accepted passage requires a distance")
            if not passage.excerpt or not passage.excerpt.strip():
                raise ValueError("an accepted passage requires an excerpt")
        return value

    @model_validator(mode="after")
    def acceptance_matches_the_gate(self) -> EvidenceDecision:
        if self.sufficient and not self.accepted:
            raise ValueError("sufficient evidence requires at least one accepted passage")
        if not self.sufficient and self.accepted:
            raise ValueError("rejected evidence has no accepted passages")
        return self


class DraftAnswer(BaseModel):
    """Writer output the coordinator may turn into a grounded answer."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    recommendations: list[str] = Field(..., min_length=1)
    cited_sources: list[str] = Field(default_factory=list)
    summary: str = Field(..., min_length=1)

    @field_validator("recommendations", "cited_sources")
    @classmethod
    def items_are_not_blank(cls, value: list[str]) -> list[str]:
        return _non_blank_items(value)


class StageTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: TimedStage
    elapsed_ms: float = Field(..., ge=0)


class TicketRunState(BaseModel):
    """Coordinator state for a single analyze request."""

    model_config = ConfigDict(extra="forbid")

    status: WorkflowStatus = WorkflowStatus.RECEIVED
    ticket: SupportTicket
    received_at: datetime
    triage: Stage[TicketTriage] = Field(default_factory=lambda: Stage[TicketTriage]())
    clarification: Stage[ClarificationRecord] = Field(default_factory=lambda: Stage[ClarificationRecord]())
    research: Stage[ResearchRequest] = Field(default_factory=lambda: Stage[ResearchRequest]())
    retrieval: Stage[RetrievalResult] = Field(default_factory=lambda: Stage[RetrievalResult]())
    evidence: Stage[EvidenceDecision] = Field(default_factory=lambda: Stage[EvidenceDecision]())
    draft: Stage[DraftAnswer] = Field(default_factory=lambda: Stage[DraftAnswer]())
    final: Stage[AgentResponse] = Field(default_factory=lambda: Stage[AgentResponse]())
    timings: list[StageTiming] = Field(default_factory=list)
    gemini_calls: int = Field(default=0, ge=0)
    embed_api_calls: int = Field(default=0, ge=0)
    embed_cache_hits: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=_ERROR_MAX_LENGTH)
    # Server-side category for `error`; never part of the client response.
    error_category: str | None = Field(default=None, max_length=64)

    @field_validator("error")
    @classmethod
    def workflow_error_is_bounded(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_error(value)

    @model_validator(mode="after")
    def stages_agree_with_their_values(self) -> TicketRunState:
        _agree(self.clarification, "needed", True, "clarification")
        _agree(self.research, "requested_search", True, "research")
        _agree(self.evidence, "sufficient", True, "evidence")
        if self.status is WorkflowStatus.COMPLETED:
            if self.final.status is not StageStatus.PRODUCED or self.final.value is None:
                raise ValueError("a completed run requires a produced final response")
            if self.error is not None:
                raise ValueError("a completed run has no workflow error")
            _completed_path(self)
        elif self.status is WorkflowStatus.FAILED:
            if not self.error:
                raise ValueError("a failed run requires an error")
            if self.final.status is StageStatus.PRODUCED:
                raise ValueError("a failed run has no final response")
        else:
            if self.error is not None:
                raise ValueError("a workflow error is set only when the run has failed")
            if self.final.status is not StageStatus.MISSING:
                raise ValueError("the final response is set only when the run is completed")
        return self


def research_stage(payload: Any) -> Stage[ResearchRequest]:
    """Validate Researcher output. A declined search is rejected, not passed to retrieval."""
    try:
        request = ResearchRequest.model_validate(payload)
    except ValidationError as exc:
        return Stage(status=StageStatus.INVALID, error=_error_from(exc))
    status = StageStatus.PRODUCED if request.requested_search else StageStatus.REJECTED
    return Stage(status=status, value=request)


def clarification_stage(payload: Any) -> Stage[ClarificationRecord]:
    """Validate the clarification decision. Asking the reporter is a produced result."""
    try:
        record = ClarificationRecord.model_validate(payload)
    except ValidationError as exc:
        return Stage(status=StageStatus.INVALID, error=_error_from(exc))
    status = StageStatus.PRODUCED if record.needed else StageStatus.REJECTED
    return Stage(status=status, value=record)


def evidence_stage(payload: Any) -> Stage[EvidenceDecision]:
    """Validate the gate. Rejected evidence is kept, but not as passages for the Writer."""
    try:
        decision = EvidenceDecision.model_validate(payload)
    except ValidationError as exc:
        return Stage(status=StageStatus.INVALID, error=_error_from(exc))
    status = StageStatus.PRODUCED if decision.sufficient else StageStatus.REJECTED
    return Stage(status=status, value=decision)


def draft_stage(payload: Any) -> Stage[DraftAnswer]:
    """Validate Writer output. Empty or blank steps are invalid and are not stored."""
    try:
        draft = DraftAnswer.model_validate(payload)
    except ValidationError as exc:
        return Stage(status=StageStatus.INVALID, error=_error_from(exc))
    return Stage(status=StageStatus.PRODUCED, value=draft)


def _non_blank_items(value: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in value:
        text = item.strip()
        if not text:
            raise ValueError("must not be blank")
        cleaned.append(text)
    return cleaned


def _error_from(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "value"
    return _bounded_error(f"{location}: {first['type']}")


def _agree(stage: Stage[Any], flag: str, produced_when: bool, name: str) -> None:
    if stage.status not in {StageStatus.PRODUCED, StageStatus.REJECTED} or stage.value is None:
        return
    is_set = bool(getattr(stage.value, flag))
    if stage.status is StageStatus.PRODUCED and is_set is not produced_when:
        raise ValueError(f"produced {name} does not match its decision")
    if stage.status is StageStatus.REJECTED and is_set is produced_when:
        raise ValueError(f"rejected {name} does not match its decision")


def _completed_path(state: TicketRunState) -> None:
    response = state.final.value
    if response is None:
        raise ValueError("a completed run requires a produced final response")
    if response.outcome is AgentOutcome.GROUNDED_ANSWER:
        if state.evidence.status is not StageStatus.PRODUCED or state.draft.status is not StageStatus.PRODUCED:
            raise ValueError("a grounded answer requires accepted evidence and a draft")
    elif response.outcome is AgentOutcome.CLARIFICATION_NEEDED:
        if state.clarification.status is not StageStatus.PRODUCED:
            raise ValueError("clarification requires a produced clarification decision")
        if state.research.status is not StageStatus.MISSING or state.draft.status is not StageStatus.MISSING:
            raise ValueError("clarification does not keep research or a draft")
    elif state.draft.status is StageStatus.PRODUCED:
        raise ValueError("fallback does not keep a writer draft")
