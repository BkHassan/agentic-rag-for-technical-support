"""Coordinator: the only owner of workflow control for one ticket.

Specialist agents do not call one another. This module runs Triage, Researcher,
and Writer at most once each, validates every boundary, and builds the public
response. It does not add a graph library.
"""

from __future__ import annotations

import contextvars
from contextvars import ContextVar
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from google.genai import errors as genai_errors
from pydantic import ValidationError

from app.agents.researcher import ResearchRun, research_ticket
from app.agents.state import (
    DraftAnswer,
    EvidenceDecision,
    Stage,
    StageStatus,
    StageTiming,
    TicketRunState,
    TimedStage,
    WorkflowStatus,
    clarification_stage,
    draft_stage,
    evidence_stage,
)
from app.agents.triage import TriageAssessment, TriageOutputError, triage_ticket
from app.agents.writer import WriterFailure, WriterRun, write_answer
from app.config import Settings, settings
from app import observability as obs
from app.orchestration.model import MODEL_REQUEST_REJECTED, call_deadline
from app.policies.clarification import CLARIFICATION_MESSAGE
from app.policies.evidence import (
    FALLBACK_MESSAGE,
    accepted_passages,
    evidence_reason_code,
    validate_retrieval_evidence,
)
from app.retrieval.retrieval import retrieve
from app.usage import current_usage
from app.schemas import (
    AgentOutcome,
    AgentResponse,
    DocumentSource,
    SupportTicket,
    TicketCategory,
    TicketTriage,
)

logger = logging.getLogger(__name__)

MAX_TRANSITIONS = 8
CLIENT_CLASSIFICATION_FAILED = "Ticket classification failed."
CLIENT_TIMEOUT = "The analysis timed out."
CLIENT_MODEL_UNAVAILABLE = "The language model is temporarily unavailable."
CLIENT_WORKFLOW = "The analysis could not be completed."
CLIENT_DUPLICATE = "A workflow stage ran more than once."

TriageFn = Callable[..., TriageAssessment]
ResearchFn = Callable[..., ResearchRun]
WriterFn = Callable[..., WriterRun]
EvidenceFn = Callable[..., Any]
RetrieveFn = Callable[[str], list[dict[str, Any]]]


class CoordinatorTimeout(TimeoutError):
    """An external model or tool call exceeded its bound. The coordinator does not retry."""


@dataclass(frozen=True)
class CoordinatorAgents:
    """Injected specialists. Tests replace these; production uses the three agents."""

    triage: TriageFn
    research: ResearchFn
    write: WriterFn
    evidence_gate: EvidenceFn
    retrieve_documents: RetrieveFn | None = None


def coordinate_ticket(
    ticket: SupportTicket | dict[str, Any] | str,
    *,
    ticket_id: str = "adhoc",
    client: Any | None = None,
    cfg: Settings | None = None,
    triage_agent: TriageFn | None = None,
    research_agent: ResearchFn | None = None,
    writer_agent: WriterFn | None = None,
    evidence_gate: EvidenceFn | None = None,
    retrieve_documents: RetrieveFn | None = None,
) -> TicketRunState:
    """Run the ticket through explicit workflow statuses. Agents are not invoked from each other."""
    cfg = cfg or settings
    ticket_model = _validate_ticket(ticket, ticket_id)
    agents = CoordinatorAgents(
        triage=triage_agent or triage_ticket,
        research=research_agent or research_ticket,
        write=writer_agent or write_answer,
        evidence_gate=evidence_gate or validate_retrieval_evidence,
        retrieve_documents=retrieve_documents,
    )
    state = TicketRunState(
        status=WorkflowStatus.RECEIVED,
        ticket=ticket_model,
        received_at=datetime.now(timezone.utc),
    )
    transitions = {
        WorkflowStatus.RECEIVED: _run_triage,
        WorkflowStatus.TRIAGED: _branch_after_triage,
        WorkflowStatus.CLARIFYING: _complete_clarification,
        WorkflowStatus.RESEARCHING: _run_research,
        WorkflowStatus.EVIDENCE_CHECKED: _run_evidence_gate,
        WorkflowStatus.WRITING: _run_writer,
    }
    steps = 0
    while state.status not in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED} and steps < MAX_TRANSITIONS:
        steps += 1
        current = state.status
        handler = transitions.get(current)
        if handler is None:
            return _fail(state, CLIENT_WORKFLOW)
        before = state
        usage_before = _usage_snapshot()
        _stage_notes.set({})
        started = time.perf_counter()
        try:
            state = handler(state, agents, client, cfg)
        except (CoordinatorTimeout, FuturesTimeoutError) as exc:
            logger.exception("Coordinator timeout ticket_id=%s status=%s", ticket_model.ticket_id, current.value)
            state = _fail(state, CLIENT_TIMEOUT, cause=exc, category=obs.TIMEOUT)
            _log_stage(current, before, state, started, usage_before)
            return state
        except (genai_errors.ClientError, genai_errors.ServerError, ValueError) as exc:
            _log_stage(current, before, None, started, usage_before, error=exc)
            raise
        except Exception as exc:  # noqa: BLE001 - the original stays in the log, not the client body
            logger.exception("Coordinator failed ticket_id=%s status=%s", ticket_model.ticket_id, current.value)
            state = _fail(state, CLIENT_WORKFLOW, cause=exc, category=obs.INTERNAL_ERROR)
            _log_stage(current, before, state, started, usage_before)
            return state
        _log_stage(current, before, state, started, usage_before)
        if state.status is current:
            logger.error("Workflow did not advance ticket_id=%s status=%s", ticket_model.ticket_id, current.value)
            return _fail(state, CLIENT_WORKFLOW)
    if state.status not in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
        logger.error("Workflow exceeded its step bound ticket_id=%s", ticket_model.ticket_id)
        return _fail(state, CLIENT_WORKFLOW)
    return state


def _validate_ticket(ticket: SupportTicket | dict[str, Any] | str, ticket_id: str) -> SupportTicket:
    if isinstance(ticket, SupportTicket):
        return SupportTicket.model_validate(ticket.model_dump())
    if isinstance(ticket, str):
        return SupportTicket(ticket_id=ticket_id, description=ticket)
    return SupportTicket.model_validate(ticket)


def _run_triage(
    state: TicketRunState,
    agents: CoordinatorAgents,
    client: Any,
    cfg: Settings,
) -> TicketRunState:
    if state.triage.status is not StageStatus.MISSING:
        return _fail(state, CLIENT_DUPLICATE)
    started = time.perf_counter()
    try:
        raw = _call_bounded(
            agents.triage,
            cfg.model_call_timeout_seconds,
            state.ticket,
            client=client,
            cfg=cfg,
        )
    except CoordinatorTimeout:
        raise
    except TriageOutputError as exc:
        logger.exception("Invalid triage output ticket_id=%s", state.ticket.ticket_id)
        return _fail(state, CLIENT_CLASSIFICATION_FAILED, cause=exc)
    except (genai_errors.ClientError, genai_errors.ServerError, ValueError):
        raise
    elapsed = _elapsed_ms(started)
    try:
        assessment = TriageAssessment.model_validate(
            raw.model_dump() if hasattr(raw, "model_dump") else raw
        )
        classification = TicketTriage.model_validate(assessment.classification.model_dump())
        clarification = clarification_stage(assessment.clarification)
    except (ValidationError, TypeError, ValueError) as exc:
        logger.exception("Triage boundary failed ticket_id=%s", state.ticket.ticket_id)
        return _fail(state, CLIENT_CLASSIFICATION_FAILED, cause=exc)
    if clarification.status is StageStatus.INVALID:
        logger.error("Clarification record invalid ticket_id=%s", state.ticket.ticket_id)
        return _fail(state, CLIENT_CLASSIFICATION_FAILED)
    return _update(
        state,
        status=WorkflowStatus.TRIAGED,
        triage=Stage(status=StageStatus.PRODUCED, value=classification),
        clarification=clarification,
        timings=_timing(state, TimedStage.TRIAGE, elapsed),
    )


def _branch_after_triage(
    state: TicketRunState,
    agents: CoordinatorAgents,
    client: Any,
    cfg: Settings,
) -> TicketRunState:
    del agents, client, cfg
    if state.triage.status is not StageStatus.PRODUCED or state.triage.value is None:
        return _fail(state, CLIENT_CLASSIFICATION_FAILED)
    if state.clarification.status is StageStatus.PRODUCED:
        return _update(state, status=WorkflowStatus.CLARIFYING)
    completeness = state.triage.value.completeness
    if completeness is None or not completeness.is_troubleshooting_request:
        return _complete(
            state,
            _fallback_response(state, reason="Not a troubleshooting request."),
        )
    return _update(state, status=WorkflowStatus.RESEARCHING)


def _complete_clarification(
    state: TicketRunState,
    agents: CoordinatorAgents,
    client: Any,
    cfg: Settings,
) -> TicketRunState:
    del agents, client, cfg
    if state.research.status is not StageStatus.MISSING or state.draft.status is not StageStatus.MISSING:
        return _fail(state, CLIENT_DUPLICATE)
    record = state.clarification.value
    classification = state.triage.value
    if record is None or classification is None or not record.needed:
        return _fail(state, CLIENT_WORKFLOW)
    response = AgentResponse(
        ticket_id=state.ticket.ticket_id,
        category=classification.category,
        outcome=AgentOutcome.CLARIFICATION_NEEDED,
        clarifying_questions=list(record.questions),
        recommendations=[],
        sources=[],
        sufficient_evidence=False,
        fallback_triggered=False,
        summary=CLARIFICATION_MESSAGE,
    )
    return _complete(state, response)


def _run_research(
    state: TicketRunState,
    agents: CoordinatorAgents,
    client: Any,
    cfg: Settings,
) -> TicketRunState:
    if state.research.status is not StageStatus.MISSING or state.draft.status is not StageStatus.MISSING:
        return _fail(state, CLIENT_DUPLICATE)
    if state.triage.value is None:
        return _fail(state, CLIENT_CLASSIFICATION_FAILED)
    retriever = _timed_retriever(agents.retrieve_documents, cfg)
    timeout = cfg.model_call_timeout_seconds + cfg.tool_execution_timeout_seconds
    started = time.perf_counter()
    raw = _call_bounded(
        agents.research,
        timeout,
        state.ticket,
        state.triage.value,
        client=client,
        cfg=cfg,
        retrieve_documents=retriever,
    )
    elapsed = _elapsed_ms(started)
    try:
        run = ResearchRun.model_validate(raw.model_dump() if hasattr(raw, "model_dump") else raw)
    except (ValidationError, TypeError, ValueError) as exc:
        logger.exception("Invalid researcher output ticket_id=%s", state.ticket.ticket_id)
        return _complete(
            _stamp_research_timing(state, elapsed),
            _fallback_response(state, reason="Research output was not valid."),
        )
    if _is_model_request_rejected(run.error):
        logger.error("Researcher model request rejected ticket_id=%s error=%s", state.ticket.ticket_id, run.error)
        return _fail(_stamp_research_timing(state, elapsed), CLIENT_WORKFLOW, category=obs.MODEL_REQUEST_REJECTED)
    if _is_model_outage(run.error):
        logger.error("Researcher model outage ticket_id=%s error=%s", state.ticket.ticket_id, run.error)
        return _fail(
            _stamp_research_timing(state, elapsed), CLIENT_MODEL_UNAVAILABLE, category=_outage_category(run.error)
        )
    if _is_timeout_error(run.error):
        logger.error("Researcher tool timeout ticket_id=%s", state.ticket.ticket_id)
        return _fail(_stamp_research_timing(state, elapsed), CLIENT_TIMEOUT, category=obs.TIMEOUT)
    if run.research.status is StageStatus.INVALID:
        logger.warning("Researcher rejected ticket_id=%s error=%s", state.ticket.ticket_id, run.error)
        updated = _stamp_research_timing(
            _update(state, research=run.research, retrieval=run.retrieval),
            elapsed,
        )
        return _complete(updated, _fallback_response(updated, reason=run.error or "Research request was not valid."))
    updated = _stamp_research_timing(
        _update(
            state,
            status=WorkflowStatus.EVIDENCE_CHECKED,
            research=run.research,
            retrieval=run.retrieval,
            gemini_calls=state.gemini_calls + run.model_calls,
        ),
        elapsed,
    )
    return updated


def _run_evidence_gate(
    state: TicketRunState,
    agents: CoordinatorAgents,
    client: Any,
    cfg: Settings,
) -> TicketRunState:
    del client
    if state.evidence.status is not StageStatus.MISSING or state.draft.status is not StageStatus.MISSING:
        return _fail(state, CLIENT_DUPLICATE)
    retrieval_performed = state.retrieval.status is StageStatus.PRODUCED
    if state.retrieval.status is StageStatus.INVALID:
        decision = evidence_stage(
            EvidenceDecision(
                sufficient=False,
                reason=state.retrieval.error or "Retrieval failed.",
                accepted=[],
                reason_code="retrieval_failed",
            )
        )
        updated = _update(state, evidence=decision)
        return _complete(updated, _fallback_response(updated, reason=decision.value.reason if decision.value else FALLBACK_MESSAGE))

    chunks = _chunks_from_retrieval(state)
    try:
        validation = agents.evidence_gate(
            chunks,
            retrieval_performed=retrieval_performed,
            cfg=cfg,
        )
    except Exception:  # noqa: BLE001 - a gate failure must not become a grounded answer
        logger.exception("Evidence gate failed ticket_id=%s", state.ticket.ticket_id)
        return _complete(state, _fallback_response(state, reason="Evidence could not be validated."))
    accepted = accepted_passages(validation) if retrieval_performed else []
    sufficient = bool(getattr(validation, "sufficient_evidence", False) and accepted)
    reason = str(getattr(validation, "reason", "") or "Evidence was not accepted.")
    try:
        decision = evidence_stage(
            EvidenceDecision(
                sufficient=sufficient,
                reason=reason,
                accepted=_as_sources(accepted) if sufficient else [],
                reason_code=_gate_reason_code(validation, sufficient),
            )
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.exception("Evidence boundary failed ticket_id=%s", state.ticket.ticket_id)
        return _complete(state, _fallback_response(state, reason="Evidence could not be validated."))
    updated = _update(state, evidence=decision)
    if decision.status is not StageStatus.PRODUCED or decision.value is None or not decision.value.sufficient:
        return _complete(updated, _fallback_response(updated, reason=reason, sources=_fallback_sources(updated, cfg)))
    return _update(updated, status=WorkflowStatus.WRITING)


def _run_writer(
    state: TicketRunState,
    agents: CoordinatorAgents,
    client: Any,
    cfg: Settings,
) -> TicketRunState:
    if state.draft.status is not StageStatus.MISSING:
        return _fail(state, CLIENT_DUPLICATE)
    if state.evidence.status is not StageStatus.PRODUCED or state.evidence.value is None:
        return _complete(state, _fallback_response(state, reason="Writer requires accepted evidence."))
    accepted = list(state.evidence.value.accepted)
    if not accepted:
        return _complete(state, _fallback_response(state, reason="Writer requires accepted evidence."))
    started = time.perf_counter()
    raw = _call_bounded(
        agents.write,
        cfg.model_call_timeout_seconds,
        state.ticket,
        state.triage.value,
        accepted,
        client=client,
        cfg=cfg,
    )
    elapsed = _elapsed_ms(started)
    timed = _update(state, timings=_timing(state, TimedStage.WRITER, elapsed))
    try:
        run = WriterRun.model_validate(raw.model_dump() if hasattr(raw, "model_dump") else raw)
    except (ValidationError, TypeError, ValueError) as exc:
        logger.exception("Invalid writer output ticket_id=%s", state.ticket.ticket_id)
        return _complete(timed, _fallback_response(timed, reason="Writer output was not valid."))
    if _is_model_request_rejected(run.reason):
        logger.error("Writer model request rejected ticket_id=%s error=%s", state.ticket.ticket_id, run.reason)
        return _fail(timed, CLIENT_WORKFLOW, category=obs.MODEL_REQUEST_REJECTED)
    if run.failure is WriterFailure.MODEL_ERROR or _is_model_outage(run.reason):
        logger.error("Writer model outage ticket_id=%s error=%s", state.ticket.ticket_id, run.reason)
        return _fail(timed, CLIENT_MODEL_UNAVAILABLE, category=_outage_category(run.reason))
    if run.failure is not None or run.draft.status is not StageStatus.PRODUCED or run.draft.value is None:
        logger.info("Writer did not produce a draft ticket_id=%s failure=%s", state.ticket.ticket_id, run.failure)
        _note_stage(writer_failure=run.failure.value if run.failure is not None else "no_draft")
        draft = run.draft if run.draft.status is StageStatus.INVALID else Stage()
        return _complete(
            _update(timed, draft=draft),
            _fallback_response(timed, reason=run.reason or FALLBACK_MESSAGE, sources=_fallback_sources(timed, cfg)),
        )
    checked = draft_stage(run.draft.value.model_dump())
    if checked.status is not StageStatus.PRODUCED or checked.value is None:
        return _complete(timed, _fallback_response(timed, reason="Writer draft was not valid."))
    if not _citations_are_supplied(checked.value, accepted):
        logger.warning("Writer cited a document that was not supplied ticket_id=%s", state.ticket.ticket_id)
        return _complete(timed, _fallback_response(timed, reason="Writer cited a document that was not supplied."))
    response = _grounded_response(timed, checked.value, accepted)
    return _complete(_update(timed, draft=checked), response)


def _grounded_response(
    state: TicketRunState,
    draft: DraftAnswer,
    accepted: list[DocumentSource],
) -> AgentResponse:
    category = state.triage.value.category if state.triage.value is not None else TicketCategory.OTHER
    return AgentResponse(
        ticket_id=state.ticket.ticket_id,
        category=category,
        outcome=AgentOutcome.GROUNDED_ANSWER,
        recommendations=list(draft.recommendations),
        sources=_sources_from(accepted, draft.cited_sources),
        sufficient_evidence=True,
        fallback_triggered=False,
        summary=draft.summary,
    )


def _fallback_response(
    state: TicketRunState,
    *,
    reason: str,
    sources: list[DocumentSource] | None = None,
) -> AgentResponse:
    del reason
    category = state.triage.value.category if state.triage.value is not None else TicketCategory.OTHER
    return AgentResponse(
        ticket_id=state.ticket.ticket_id,
        category=category,
        outcome=AgentOutcome.FALLBACK,
        recommendations=[FALLBACK_MESSAGE],
        sources=list(sources or []),
        sufficient_evidence=False,
        fallback_triggered=True,
        summary=FALLBACK_MESSAGE,
    )


def _fallback_sources(state: TicketRunState, cfg: Settings) -> list[DocumentSource]:
    if state.retrieval.status is not StageStatus.PRODUCED or state.retrieval.value is None:
        return []
    return list(state.retrieval.value.passages[: cfg.retrieval_top_k])


def _complete(state: TicketRunState, response: AgentResponse) -> TicketRunState:
    return _update(
        state,
        status=WorkflowStatus.COMPLETED,
        final=Stage(status=StageStatus.PRODUCED, value=response),
        error=None,
    )


_DEFAULT_CATEGORY = {
    CLIENT_TIMEOUT: obs.TIMEOUT,
    CLIENT_MODEL_UNAVAILABLE: obs.MODEL_UNAVAILABLE,
    CLIENT_CLASSIFICATION_FAILED: obs.INVALID_AGENT_OUTPUT,
    CLIENT_WORKFLOW: obs.WORKFLOW_ERROR,
    CLIENT_DUPLICATE: obs.WORKFLOW_ERROR,
}


def _fail(
    state: TicketRunState,
    message: str,
    *,
    cause: BaseException | None = None,
    category: str | None = None,
) -> TicketRunState:
    del cause
    return _update(
        state,
        status=WorkflowStatus.FAILED,
        final=Stage(),
        error=message,
        error_category=category or _DEFAULT_CATEGORY.get(message, obs.WORKFLOW_ERROR),
    )


def _outage_category(message: str | None) -> str:
    return obs.RATE_LIMITED if "rate limit" in (message or "") else obs.MODEL_UNAVAILABLE


def _gate_reason_code(validation: Any, sufficient: bool) -> str:
    try:
        code = evidence_reason_code(validation)
    except (AttributeError, TypeError):
        return "gate_result_unreadable"
    # The gate passed, but no passage was eligible to forward to the Writer.
    return "no_eligible_passages" if code == "accepted" and not sufficient else code


# --- Diagnostics ---------------------------------------------------------------------

_STAGE_NAMES = {
    WorkflowStatus.RECEIVED: "triage",
    WorkflowStatus.TRIAGED: "route",
    WorkflowStatus.CLARIFYING: "clarification",
    WorkflowStatus.RESEARCHING: "research",
    WorkflowStatus.EVIDENCE_CHECKED: "evidence_gate",
    WorkflowStatus.WRITING: "writer",
}
_USAGE_FIELDS = (
    "gemini_calls", "gemini_retries", "embed_api_calls", "gemini_responses",
    "gemini_responses_with_usage", "prompt_tokens", "output_tokens", "thinking_tokens",
)


# Codes a handler knows but the run state does not keep. Per request, so never shared.
_stage_notes: ContextVar[dict[str, Any] | None] = ContextVar("stage_notes", default=None)


def _note_stage(**fields: Any) -> None:
    notes = _stage_notes.get()
    if notes is not None:
        notes.update(fields)


def _usage_snapshot() -> dict[str, int] | None:
    usage = current_usage()
    return None if usage is None else {name: getattr(usage, name) for name in _USAGE_FIELDS}


def _usage_fields(before: dict[str, int] | None) -> dict[str, Any]:
    """Per-stage deltas of the request's counters; empty when usage is not being tracked."""
    after = _usage_snapshot()
    if before is None or after is None:
        return {}
    delta = {name: after[name] - before[name] for name in _USAGE_FIELDS}
    fields: dict[str, Any] = {
        "model_calls": delta["gemini_calls"],
        "retries": delta["gemini_retries"],
        "embed_calls": delta["embed_api_calls"],
    }
    if delta["gemini_responses"] and delta["gemini_responses_with_usage"] == delta["gemini_responses"]:
        fields.update(
            input_tokens=delta["prompt_tokens"],
            output_tokens=delta["output_tokens"],
            thinking_tokens=delta["thinking_tokens"],
        )
    elif delta["gemini_responses"]:
        fields["tokens"] = "not_reported"
    return fields


def _log_stage(
    current: WorkflowStatus,
    before: TicketRunState,
    after: TicketRunState | None,
    started: float,
    usage_before: dict[str, int] | None,
    *,
    error: BaseException | None = None,
) -> None:
    """One event per stage: counts, codes, and durations only, never ticket or passage text."""
    stage = _STAGE_NAMES.get(current, current.value)
    fields: dict[str, Any] = {"stage": stage, "duration_ms": _elapsed_ms(started)}
    if after is None:
        fields.update(status="error", error_category=obs.error_category(error) if error else obs.INTERNAL_ERROR)
    elif after.status is WorkflowStatus.FAILED:
        fields.update(status="failed", error_category=after.error_category)
    else:
        fields.update(status="ok", next=after.status.value)
    if after is not None:
        fields.update(_stage_details(stage, after))
    fields.update(_stage_notes.get() or {})
    _stage_notes.set(None)
    fields.update(_usage_fields(usage_before))
    obs.log_event("stage", level=logging.INFO if fields["status"] == "ok" else logging.WARNING, **fields)


def _stage_details(stage: str, after: TicketRunState) -> dict[str, Any]:
    if stage == "triage" and after.triage.value is not None:
        clarify = after.clarification.value
        return {
            "category": after.triage.value.category.value,
            "clarification_needed": bool(clarify and clarify.needed),
        }
    if stage == "research":
        attempted = after.retrieval.status in {StageStatus.PRODUCED, StageStatus.INVALID}
        produced = after.retrieval.status is StageStatus.PRODUCED and after.retrieval.value is not None
        return {
            "search_requested": bool(after.research.value and after.research.value.requested_search),
            "tool_executions": 1 if attempted else 0,
            "retrieval_status": after.retrieval.status.value,
            "retrieval_count": len(after.retrieval.value.passages) if produced else None,
        }
    if stage == "evidence_gate":
        decision = after.evidence.value
        if decision is None:
            return {"gate_decision": "not_recorded"}
        return {
            "gate_decision": "accepted" if decision.sufficient else "rejected",
            "reason_code": decision.reason_code,
            "accepted_count": len(decision.accepted),
        }
    if stage == "writer":
        draft = after.draft.value
        return {
            "draft_status": after.draft.status.value,
            "steps": len(draft.recommendations) if draft is not None else None,
        }
    if stage == "clarification" and after.clarification.value is not None:
        return {"questions": len(after.clarification.value.questions)}
    return {}


def _update(state: TicketRunState, **changes: Any) -> TicketRunState:
    return state.model_copy(update=changes)


def _timing(state: TicketRunState, stage: TimedStage, elapsed_ms: float) -> list[StageTiming]:
    return [*state.timings, StageTiming(stage=stage, elapsed_ms=elapsed_ms)]


def _stamp_research_timing(state: TicketRunState, elapsed_ms: float) -> TicketRunState:
    return _update(
        state,
        timings=_timing(state, TimedStage.RESEARCH, elapsed_ms),
    )


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (time.perf_counter() - started) * 1000.0), 1)


def _call_bounded(func: Callable[..., Any], timeout_seconds: float, *args: Any, **kwargs: Any) -> Any:
    """Run one external call. A timeout is a failure; the coordinator does not retry it."""
    if timeout_seconds <= 0:
        raise CoordinatorTimeout("timeout bound is not positive")
    deadline = time.monotonic() + timeout_seconds
    outer = call_deadline.get()
    if outer is not None:
        deadline = min(deadline, outer)

    def bounded() -> Any:
        call_deadline.set(deadline)
        return func(*args, **kwargs)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coordinator")
    try:
        # Run in a copy of this context so per-request usage counters and the
        # deadline are visible to the call, without leaking back to the caller.
        future = executor.submit(contextvars.copy_context().run, bounded)
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        raise CoordinatorTimeout(f"{getattr(func, '__name__', 'call')} timed out") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _timed_retriever(retrieve_documents: RetrieveFn | None, cfg: Settings) -> RetrieveFn:
    retriever = retrieve_documents or (
        lambda query: retrieve(query, top_k=cfg.retrieval_top_k, cfg=cfg)
    )

    def bounded(query: str) -> list[dict[str, Any]]:
        return _call_bounded(retriever, cfg.tool_execution_timeout_seconds, query)

    return bounded


def _chunks_from_retrieval(state: TicketRunState) -> list[dict[str, Any]]:
    if state.retrieval.status is not StageStatus.PRODUCED or state.retrieval.value is None:
        return []
    chunks: list[dict[str, Any]] = []
    for passage in state.retrieval.value.passages:
        chunks.append(
            {
                "source": passage.source,
                "doc_id": passage.doc_id,
                "chunk_index": passage.chunk_index,
                "category": passage.category,
                "distance": passage.distance,
                "text": passage.excerpt,
                "excerpt": passage.excerpt,
            }
        )
    return chunks


def _as_sources(chunks: list[dict[str, Any] | DocumentSource]) -> list[DocumentSource]:
    sources: list[DocumentSource] = []
    for item in chunks:
        if isinstance(item, DocumentSource):
            sources.append(item)
            continue
        excerpt = item.get("text") or item.get("excerpt")
        sources.append(
            DocumentSource(
                source=str(item.get("source") or ""),
                doc_id=item.get("doc_id") if isinstance(item.get("doc_id"), str) else None,
                chunk_index=item.get("chunk_index") if isinstance(item.get("chunk_index"), int) else None,
                category=item.get("category") if isinstance(item.get("category"), str) else None,
                distance=float(item["distance"]) if isinstance(item.get("distance"), (int, float)) else None,
                excerpt=excerpt.strip() if isinstance(excerpt, str) else None,
            )
        )
    return sources


def _sources_from(accepted: list[DocumentSource], cited: list[str]) -> list[DocumentSource]:
    allowed = {name.casefold() for name in cited if name and name.strip()}
    sources: list[DocumentSource] = []
    seen: set[str] = set()
    for passage in accepted:
        key = f"{passage.source}::{passage.chunk_index}"
        if key in seen:
            continue
        if allowed and passage.source.casefold() not in allowed:
            continue
        seen.add(key)
        sources.append(passage)
    return sources


def _citations_are_supplied(draft: DraftAnswer, accepted: list[DocumentSource]) -> bool:
    allowed = {passage.source.casefold() for passage in accepted}
    return all(name.casefold() in allowed for name in draft.cited_sources)


def _is_model_outage(message: str | None) -> bool:
    text = (message or "").casefold()
    return "rate limit" in text or "model call failed" in text


def _is_model_request_rejected(message: str | None) -> bool:
    return MODEL_REQUEST_REJECTED in (message or "")


def _is_timeout_error(message: str | None) -> bool:
    text = message or ""
    return "CoordinatorTimeout" in text or "timed out" in text.casefold()
