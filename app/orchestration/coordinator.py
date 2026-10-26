"""Orchestrate one ticket from classification through the evidence gate to a response."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app import observability as obs
from app.config import Settings, settings
from app.policies.evidence import (
    FALLBACK_MESSAGE,
    EvidenceValidationResult,
    accepted_passages,
    assess_evidence,
    validate_retrieval_evidence,
)
from app.retrieval.ingestion import get_genai_client
from app.retrieval.retrieval import retrieve
from app.policies.clarification import CLARIFICATION_MESSAGE, ClarificationDecision
from app.schemas import (
    AgentOutcome,
    AgentResponse,
    DocumentSource,
    GroundedAdvice,
    SupportTicket,
    TicketCategory,
    TicketClassification,
    TicketTriage,
)
from app.orchestration.model import _generate_content_with_retry
from app.usage import current_usage, reset_usage, start_usage
from app.tools.knowledge_base import (
    SEARCH_TECHNICAL_DOCUMENTATION_NAME,
    execute_tool_call,
    get_knowledge_base_tool,
)

# Compatibility aliases for callers/tests.
FALLBACK_RECOMMENDATIONS = [FALLBACK_MESSAGE]

logger = logging.getLogger(__name__)

CATEGORY_GUIDANCE = """
Categories (choose exactly one):
- network: DNS, IP addressing, connectivity, VPN, routing, firewall/path issues
- database: DB connection failures, timeouts, credentials, pools, locks, query failures
- authentication: login failures, password reset, SSO/session tokens, MFA, account lockout
- application: HTTP errors (4xx/5xx), app crashes, uploads, feature bugs, general app behavior
- other: unclear, non-technical, or does not fit the categories above

Rules:
- Classify from the ticket description alone.
- Prefer the root technical domain (e.g. DB timeout → database, even if users mention "the application").
- If multiple domains appear, pick the primary cause described in the ticket.
- Do not invent facts that are not in the ticket.
""".strip()

COMPLETENESS_GUIDANCE = """
Completeness facts (copy phrases exactly from the ticket; do not paraphrase or infer):
- is_troubleshooting_request: true if the reporter says something is failing or not behaving
  as expected, even when the report is vague ("we have an issue with X" is true). False only
  for thanks, staffing, asking to be granted access or permissions, or announcements.
- affected_system: the phrase naming the specific system, feature, or technology that fails.
  Use null if the ticket only says "it", "the system", "the application", "the server", or similar.
- observable_symptom: the clause saying what happens. A concrete behavior counts even without an
  error message, e.g. "cannot reach the printer share", "never receives the email", "still shows
  the old logo", "is missing from the menu", "returns 500". Use null only when the ticket says
  nothing more specific than "broken", "not working", "something is wrong", "errors", "an issue",
  or "slow".
""".strip()

AGENT_SYSTEM_INSTRUCTION = """
You are a technical support agent.

You may call search_technical_documentation to look up internal technical docs.
Gemini does not run that function itself — you only request it; the host application
executes the search and returns structured evidence.

When to call search_technical_documentation:
- Call it for concrete technical troubleshooting tickets (network, DNS, HTTP errors,
  database connectivity, authentication failures, uploads, VPN/IP issues, etc.).
- Do NOT call it for thanks/acknowledgements, staffing/access requests, pure
  feature requests, or other non-troubleshooting messages where documentation
  search would not help.

Prefer results with lower cosine distance (closer matches).
Do not invent troubleshooting procedures. Wait for tool results before asserting remedies.
""".strip()

GROUNDING_INSTRUCTIONS = """
You produce evidence-grounded troubleshooting recommendations for support engineers.

Hard rules:
1. Use ONLY the retrieved documentation evidence provided by the host application.
2. Do NOT present unsupported claims as established facts.
3. Every recommendation must be supported by the evidence text — not merely by mentioning a filename.
4. Refer to supporting source filenames in cited_sources (must appear in the evidence list).
5. Clearly distinguish documented troubleshooting steps from uncertainty in the summary.
6. Prefer concrete diagnostic steps that appear in the evidence (e.g. verify connection string,
   check pool exhaustion, inspect DB logs) over generic advice.
7. The host application decides fallback when evidence is insufficient — do not invent procedures.
""".strip()

def _as_ticket(ticket: SupportTicket | dict[str, Any] | str, ticket_id: str = "adhoc") -> SupportTicket:
    if isinstance(ticket, SupportTicket):
        return ticket
    if isinstance(ticket, str):
        return SupportTicket(ticket_id=ticket_id, description=ticket)
    return SupportTicket.model_validate(ticket)


def classify_ticket(
    ticket: SupportTicket | dict[str, Any] | str,
    *,
    ticket_id: str = "adhoc",
    client: genai.Client | None = None,
    cfg: Settings | None = None,
) -> TicketClassification:
    """
    Send the ticket description to Gemini and return a structured category.

    Intentionally independent of retrieval so classification can be tested alone.
    """
    cfg = cfg or settings
    ticket_model = _as_ticket(ticket, ticket_id=ticket_id)
    client = client or get_genai_client(cfg.google_api_key)

    prompt = (
        "You are a technical support ticket classifier.\n\n"
        f"{CATEGORY_GUIDANCE}\n\n"
        f"{COMPLETENESS_GUIDANCE}\n\n"
        f"Ticket ID: {ticket_model.ticket_id}\n"
        f"Ticket description:\n{ticket_model.description}\n\n"
        "Return the best-fitting category, a short rationale, and the completeness facts."
    )

    logger.info("Classifying ticket_id=%s", ticket_model.ticket_id)
    response = _generate_content_with_retry(
        client,
        model=cfg.llm_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=TicketTriage,
        ),
    )

    classification = _parse_classification(response)
    logger.info(
        "Classified ticket_id=%s category=%s completeness=%s",
        ticket_model.ticket_id,
        classification.category.value,
        classification.completeness.model_dump() if classification.completeness else None,
    )
    return classification


def _parse_classification(response: Any) -> TicketClassification:
    """Normalize Gemini structured output; completeness stays None if Gemini omitted it."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, TicketClassification):
        return parsed
    if isinstance(parsed, dict):
        return TicketClassification.model_validate(parsed)

    text = (getattr(response, "text", None) or "").strip()
    if text:
        return TicketClassification.model_validate_json(text)

    raise RuntimeError("Gemini returned an empty classification response")


def _parse_grounded_advice(response: Any) -> GroundedAdvice:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, GroundedAdvice):
        return parsed
    if isinstance(parsed, dict):
        return GroundedAdvice.model_validate(parsed)

    text = (getattr(response, "text", None) or "").strip()
    if text:
        return GroundedAdvice.model_validate_json(text)

    raise RuntimeError("Gemini returned an empty grounded-advice response")


def _extract_function_calls(response: Any) -> list[Any]:
    """Collect function-call parts from a Gemini response."""
    calls = list(getattr(response, "function_calls", None) or [])
    if calls:
        return calls

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) or []
    return [part.function_call for part in parts if getattr(part, "function_call", None)]


def run_tool_calling_turn(
    ticket: SupportTicket | dict[str, Any] | str,
    *,
    ticket_id: str = "adhoc",
    max_tool_rounds: int = 3,
    finish_after_tool: bool = False,
    client: genai.Client | None = None,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    """
    Agentic workflow with explicit (manual) function calling.

    Gemini proposes tool calls; this application executes them and returns results.
    finish_after_tool stops once the search has run. The host writes the answer
    from its own evidence check, so a follow-up model call would be unused.
    """
    cfg = cfg or settings
    ticket_model = _as_ticket(ticket, ticket_id=ticket_id)
    client = client or get_genai_client(cfg.google_api_key)

    tool = get_knowledge_base_tool()
    config = types.GenerateContentConfig(
        system_instruction=AGENT_SYSTEM_INSTRUCTION,
        tools=[tool],
        temperature=0.2,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    user_prompt = (
        "Review this support ticket.\n"
        "If it is a concrete technical troubleshooting issue, call "
        "search_technical_documentation with a focused query before recommending "
        "actions. If it is not a troubleshooting issue (thanks, staffing, access "
        "requests, etc.), do not call the tool.\n\n"
        f"Ticket ID: {ticket_model.ticket_id}\n"
        f"Description:\n{ticket_model.description}"
    )
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])
    ]

    tool_trace: list[dict[str, Any]] = []
    evidence_hits: list[dict[str, Any]] = []
    final_text = ""

    for round_index in range(1, max_tool_rounds + 1):
        logger.info(
            "Tool-calling round %s for ticket_id=%s",
            round_index,
            ticket_model.ticket_id,
        )
        response = _generate_content_with_retry(
            client,
            model=cfg.llm_model,
            contents=contents,
            config=config,
        )

        function_calls = _extract_function_calls(response)
        if not function_calls:
            final_text = (getattr(response, "text", None) or "").strip()
            break

        model_content = response.candidates[0].content
        contents.append(model_content)

        response_parts: list[types.Part] = []
        for call in function_calls:
            name = call.name or SEARCH_TECHNICAL_DOCUMENTATION_NAME
            args = dict(call.args or {})
            logger.info(
                "Gemini requested tool=%s args=%s (executed by application)",
                name,
                args,
            )
            result = execute_tool_call(name, args, cfg=cfg)
            hits = list(result.get("results") or [])
            evidence_hits.extend(hits)
            tool_trace.append(
                {
                    "round": round_index,
                    "name": name,
                    "args": args,
                    "result_count": result.get("result_count"),
                    "relevant_count": result.get("relevant_count"),
                    "error": result.get("error"),
                    "results": hits,
                }
            )
            # Preserve function_call.id for Gemini 3 tool-context mapping.
            call_id = getattr(call, "id", None)
            response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=call_id,
                        name=name,
                        response={"result": result},
                    )
                )
            )

        contents.append(types.Content(role="user", parts=response_parts))
        if finish_after_tool:
            break
    else:
        response = _generate_content_with_retry(
            client,
            model=cfg.llm_model,
            contents=contents,
            config=config,
        )
        final_text = (getattr(response, "text", None) or "").strip()

    return {
        "ticket_id": ticket_model.ticket_id,
        "description": ticket_model.description,
        "tool_calls": tool_trace,
        "evidence": _dedupe_evidence(evidence_hits),
        "used_knowledge_base": any(
            item["name"] == SEARCH_TECHNICAL_DOCUMENTATION_NAME for item in tool_trace
        ),
        "response_text": final_text,
    }


def _dedupe_evidence(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for hit in hits:
        key = str(hit.get("doc_id") or hit.get("source") or "") + f"::{hit.get('chunk_index')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


def _format_evidence_for_prompt(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "No documentation chunks were retrieved."

    blocks: list[str] = []
    for index, item in enumerate(evidence, start=1):
        text = (item.get("text") or "").strip()
        blocks.append(
            "\n".join(
                [
                    f"[{index}] source={item.get('source')}",
                    f"doc_id={item.get('doc_id')} chunk_index={item.get('chunk_index')}",
                    f"category={item.get('category')} distance={item.get('distance')}",
                    "text:",
                    text,
                ]
            )
        )
    return "\n\n".join(blocks)


def _sources_from_evidence(
    evidence: list[dict[str, Any]],
    cited_filenames: list[str] | None = None,
) -> list[DocumentSource]:
    allowed = {name.lower() for name in (cited_filenames or []) if name}
    sources: list[DocumentSource] = []
    seen: set[str] = set()

    for item in evidence:
        source_name = str(item.get("source") or "")
        if not source_name:
            continue
        if allowed and source_name.lower() not in allowed:
            continue
        key = f"{source_name}::{item.get('chunk_index')}"
        if key in seen:
            continue
        seen.add(key)
        excerpt = (item.get("text") or "").strip().replace("\n", " ")
        if len(excerpt) > 240:
            excerpt = excerpt[:237] + "..."
        sources.append(
            DocumentSource(
                source=source_name,
                doc_id=item.get("doc_id"),
                chunk_index=item.get("chunk_index"),
                category=item.get("category"),
                distance=item.get("distance"),
                excerpt=excerpt or None,
            )
        )
    return sources


def build_fallback_response(
    ticket: SupportTicket,
    *,
    category: TicketCategory,
    validation: EvidenceValidationResult,
) -> AgentResponse:
    """
    Application-owned safe fallback.

    Returned directly when evidence checks fail — Gemini is not asked to decide
    whether it has enough documentation.
    """
    return AgentResponse(
        ticket_id=ticket.ticket_id,
        category=category,
        outcome=AgentOutcome.FALLBACK,
        recommendations=[FALLBACK_MESSAGE],
        sources=_sources_from_evidence(validation.retrieved_chunks[: settings.retrieval_top_k]),
        sufficient_evidence=False,
        fallback_triggered=True,
        summary=FALLBACK_MESSAGE,
    )


def build_clarification_response(
    ticket: SupportTicket,
    *,
    category: TicketCategory,
    decision: ClarificationDecision,
) -> AgentResponse:
    """Application-owned request for the details the ticket is missing. No retrieval, no Gemini."""
    return AgentResponse(
        ticket_id=ticket.ticket_id,
        category=category,
        outcome=AgentOutcome.CLARIFICATION_NEEDED,
        clarifying_questions=list(decision.questions),
        recommendations=[],
        sources=[],
        sufficient_evidence=False,
        fallback_triggered=False,
        summary=CLARIFICATION_MESSAGE,
    )


def generate_grounded_recommendations(
    ticket: SupportTicket,
    *,
    category: TicketCategory,
    evidence: list[dict[str, Any]],
    retrieval_performed: bool,
    client: genai.Client | None = None,
    cfg: Settings | None = None,
    validation: EvidenceValidationResult | None = None,
) -> AgentResponse:
    """
    Generate source-supported recommendations only after deterministic validation.

    Mentions of filenames alone do not count as grounding. If retrieval never ran,
    or cosine-distance evidence is weak, return the safe fallback without asking
    Gemini for procedural recommendations.
    """
    cfg = cfg or settings
    client = client or get_genai_client(cfg.google_api_key)
    validation = validation or validate_retrieval_evidence(
        evidence,
        retrieval_performed=retrieval_performed,
        cfg=cfg,
    )
    logger.info(
        "Evidence validation ticket_id=%s sufficient=%s retrieval_performed=%s reason=%s",
        ticket.ticket_id,
        validation.sufficient_evidence,
        validation.retrieval_performed,
        validation.reason,
    )

    if not validation.sufficient_evidence:
        return build_fallback_response(ticket, category=category, validation=validation)

    relevant_evidence = accepted_passages(validation)[: cfg.retrieval_top_k]
    if not relevant_evidence:
        return build_fallback_response(ticket, category=category, validation=validation)

    prompt = (
        f"{GROUNDING_INSTRUCTIONS}\n\n"
        f"Ticket ID: {ticket.ticket_id}\n"
        f"Classified category: {category.value}\n"
        f"Ticket description:\n{ticket.description}\n\n"
        f"Host evidence validation: sufficient_evidence=true; "
        f"distance_metric=cosine (lower is better); "
        f"threshold<= {validation.max_distance_threshold}; "
        f"relevant_chunks={validation.relevant_count}.\n\n"
        "Retrieved documentation evidence:\n"
        f"{_format_evidence_for_prompt(relevant_evidence)}\n\n"
        "Return grounded recommendations and the source filenames that support them. "
        "In summary, separate documented steps from any remaining uncertainty."
    )

    response = _generate_content_with_retry(
        client,
        model=cfg.llm_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=GroundedAdvice,
        ),
    )
    advice = _parse_grounded_advice(response)

    # Only keep citations that exist in retrieved evidence (no hallucinated sources).
    evidence_names = {str(item.get("source") or "").lower() for item in relevant_evidence}
    cited = [name for name in advice.cited_sources if name.lower() in evidence_names]
    if not cited:
        cited = [
            str(item.get("source"))
            for item in relevant_evidence
            if item.get("source")
        ]

    recommendations = advice.recommendations or list(FALLBACK_RECOMMENDATIONS)
    # If the model returned no actionable steps despite evidence, still avoid invention
    # by falling back to quoting that documentation was found but advice was empty.
    if not advice.recommendations:
        recommendations = [
            "Review the cited documentation excerpts and apply only the diagnostic steps listed there.",
            "Collect confirming logs before making configuration changes.",
        ]

    return AgentResponse(
        ticket_id=ticket.ticket_id,
        category=category,
        outcome=AgentOutcome.GROUNDED_ANSWER,
        recommendations=recommendations,
        sources=_sources_from_evidence(relevant_evidence, cited),
        sufficient_evidence=True,
        fallback_triggered=False,
        summary=advice.summary,
    )


def collect_evidence_for_ticket(
    ticket: SupportTicket,
    tool_outcome: dict[str, Any],
    *,
    prior_ticket_hits: list[dict[str, Any]] | None = None,
    cfg: Settings | None = None,
) -> tuple[list[dict[str, Any]], bool, str]:
    """
    Collect retrieval evidence and record whether retrieval actually ran.

    If Gemini did not request the knowledge-base tool, do not invent evidence and
    do not treat the missing tool call as sufficient. The host may later return
    the safe fallback after validation fails.

    When Gemini did search, the host re-retrieves with the original ticket text and
    judges sufficiency on those scores. Gemini's rewritten query can add domain
    words a vague ticket never contained, and the calibrated thresholds were fitted
    on ticket text. Hits already retrieved for that same text are reused.
    """
    cfg = cfg or settings
    gemini_requested = bool(tool_outcome.get("used_knowledge_base"))

    if gemini_requested:
        if prior_ticket_hits is not None:
            return prior_ticket_hits, True, "gemini_tool"
        evidence = retrieve(ticket.description, top_k=cfg.relevance_candidate_pool, cfg=cfg)
        return evidence, True, "gemini_tool"

    logger.info(
        "Gemini did not request retrieval for ticket_id=%s; "
        "skipping host-side search (absence of a tool call is not evidence)",
        ticket.ticket_id,
    )
    return [], False, "gemini_skipped"


@dataclass
class TicketAnalysisTrace:
    """Full agent outcome plus instrumentation for evaluation."""

    response: AgentResponse
    classification_category: str
    gemini_requested_retrieval: bool
    retrieval_source: str
    retrieval_performed: bool
    evidence: list[dict[str, Any]]
    validation_sufficient: bool
    validation_reason: str
    tool_calls: list[dict[str, Any]]
    latency_ms: float
    error: str | None = None
    clarification_missing: list[str] | None = None
    completeness: dict[str, Any] | None = None
    clarification_overridden: bool = False
    gemini_calls: int = 0
    embed_api_calls: int = 0
    embed_cache_hits: int = 0
    stage_ms: dict[str, float] = field(default_factory=dict)
    # Evaluation-only detail. A retrieval error ends in a fallback response, so the
    # evaluation needs it to tell an outage apart from a correct fallback.
    retrieval_error: str | None = None
    tool_executions: int = 0
    accepted_sources: list[str] = field(default_factory=list)
    gemini_responses: int = 0
    gemini_responses_with_usage: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0


def _stage_ms(state: Any) -> dict[str, float]:
    return {item.stage.value: item.elapsed_ms for item in getattr(state, "timings", [])}


def _trace_from_state(state: Any, started: float) -> TicketAnalysisTrace:
    from app.agents.state import StageStatus, WorkflowStatus
    from app.policies.clarification import decide_clarification
    from app.policies.evidence import FALLBACK_MESSAGE as fallback_text

    classification = state.triage.value
    category = classification.category if classification is not None else TicketCategory.OTHER
    response = state.final.value
    if response is None:
        response = AgentResponse(
            ticket_id=state.ticket.ticket_id,
            category=category,
            outcome=AgentOutcome.FALLBACK,
            recommendations=[fallback_text],
            sources=[],
            sufficient_evidence=False,
            fallback_triggered=True,
            summary=fallback_text,
        )
    research = state.research.value
    requested = bool(research is not None and research.requested_search)
    retrieval_performed = state.retrieval.status is StageStatus.PRODUCED
    if state.clarification.status is StageStatus.PRODUCED:
        retrieval_source = "clarification"
    elif retrieval_performed:
        retrieval_source = "researcher"
    elif state.status is WorkflowStatus.FAILED:
        retrieval_source = "error"
    else:
        retrieval_source = "skipped"
    evidence = []
    if state.retrieval.status is StageStatus.PRODUCED and state.retrieval.value is not None:
        evidence = [item.model_dump() for item in state.retrieval.value.passages]
    evidence_value = state.evidence.value
    tool_calls = []
    if requested and research is not None and research.query:
        tool_calls = [{"name": "search_technical_documentation", "args": {"query": research.query}}]
    completeness = classification.completeness.model_dump() if classification and classification.completeness else None
    missing = None
    if classification is not None:
        decision = decide_clarification(state.ticket.description, classification.completeness)
        if decision.needed:
            missing = list(decision.missing)
    return TicketAnalysisTrace(
        response=response,
        classification_category=category.value,
        gemini_requested_retrieval=requested,
        retrieval_source=retrieval_source,
        retrieval_performed=retrieval_performed,
        evidence=evidence,
        validation_sufficient=bool(evidence_value.sufficient) if evidence_value is not None else False,
        validation_reason=(
            state.error
            or (evidence_value.reason if evidence_value is not None else "")
            or (state.clarification.value.reason if state.clarification.value is not None else "")
        ),
        tool_calls=tool_calls,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        error=state.error,
        clarification_missing=missing,
        completeness=completeness,
        clarification_overridden=False,
        gemini_calls=state.gemini_calls,
        retrieval_error=state.retrieval.error if state.retrieval.status is StageStatus.INVALID else None,
        tool_executions=1 if state.retrieval.status in {StageStatus.PRODUCED, StageStatus.INVALID} else 0,
        accepted_sources=[item.source for item in evidence_value.accepted] if evidence_value is not None else [],
    )


def _stamp_cost(trace: TicketAnalysisTrace, stage_ms: dict[str, float]) -> TicketAnalysisTrace:
    usage = current_usage()
    if usage is not None:
        trace.gemini_calls = usage.gemini_calls
        trace.embed_api_calls = usage.embed_api_calls
        trace.embed_cache_hits = usage.embed_cache_hits
        trace.gemini_responses = usage.gemini_responses
        trace.gemini_responses_with_usage = usage.gemini_responses_with_usage
        trace.prompt_tokens = usage.prompt_tokens
        trace.output_tokens = usage.output_tokens
        trace.thinking_tokens = usage.thinking_tokens
        trace.total_tokens = usage.total_tokens
    trace.stage_ms = stage_ms
    return trace


def _log_request(
    trace: TicketAnalysisTrace | None,
    *,
    status: str,
    error_category: str | None = None,
    started: float | None = None,
) -> None:
    """One summary event per request. Counts come from the request's usage counters."""
    usage = current_usage()
    fields: dict[str, Any] = {"status": status, "error_category": error_category}
    if trace is not None:
        fields.update(
            outcome=None if status != "completed" else trace.response.outcome.value,
            duration_ms=trace.latency_ms,
            tool_executions=trace.tool_executions,
        )
    elif started is not None:
        fields["duration_ms"] = (time.perf_counter() - started) * 1000.0
    if usage is not None:
        fields.update(
            model_calls=usage.gemini_calls,
            retries=usage.gemini_retries,
            embed_calls=usage.embed_api_calls,
            embed_cache_hits=usage.embed_cache_hits,
        )
        if usage.gemini_responses and usage.gemini_responses_with_usage == usage.gemini_responses:
            fields.update(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.output_tokens,
                thinking_tokens=usage.thinking_tokens,
                total_tokens=usage.total_tokens,
            )
        elif usage.gemini_responses:
            fields["tokens"] = "not_reported"
    obs.log_event("request", level=logging.INFO if status == "completed" else logging.WARNING, **fields)


def check_ticket_specificity(
    ticket: SupportTicket,
    *,
    cfg: Settings | None = None,
) -> tuple[list[dict[str, Any]], EvidenceValidationResult]:
    """
    Second opinion before asking for clarification: does the ticket text alone
    match one document strongly enough to pass the calibrated sufficiency rule?

    Completeness extraction is a single, non-deterministic model call, so it can
    miss a symptom the ticket plainly states. Vague tickets match many documents
    weakly and fail this rule, so a pass means the ticket is specific enough.
    Embedding lookup only; Gemini is not offered the search tool.
    """
    cfg = cfg or settings
    candidates = retrieve(ticket.description, top_k=cfg.relevance_candidate_pool, cfg=cfg)
    return candidates, validate_retrieval_evidence(candidates, retrieval_performed=True, cfg=cfg)


def analyze_ticket_with_trace(
    ticket: SupportTicket | dict[str, Any] | str,
    *,
    ticket_id: str = "adhoc",
    client: genai.Client | None = None,
    cfg: Settings | None = None,
    triage_agent: Any | None = None,
    research_agent: Any | None = None,
    writer_agent: Any | None = None,
    evidence_gate: Any | None = None,
    retrieve_documents: Any | None = None,
) -> TicketAnalysisTrace:
    """Run the coordinator and return the public response plus evaluation trace."""
    cfg = cfg or settings
    # Joins the HTTP request's ID when one is bound; otherwise (evaluation, CLI) starts one.
    with obs.request_context():
        return _analyze_in_context(
            ticket,
            ticket_id=ticket_id,
            client=client,
            cfg=cfg,
            triage_agent=triage_agent,
            research_agent=research_agent,
            writer_agent=writer_agent,
            evidence_gate=evidence_gate,
            retrieve_documents=retrieve_documents,
        )


def _analyze_in_context(
    ticket: SupportTicket | dict[str, Any] | str,
    *,
    ticket_id: str,
    client: genai.Client | None,
    cfg: Settings,
    triage_agent: Any | None,
    research_agent: Any | None,
    writer_agent: Any | None,
    evidence_gate: Any | None,
    retrieve_documents: Any | None,
) -> TicketAnalysisTrace:
    from app.orchestration.workflow import coordinate_ticket

    started = time.perf_counter()
    ticket_model = _as_ticket(ticket, ticket_id=ticket_id)
    usage_token = start_usage()
    try:
        state = coordinate_ticket(
            ticket_model,
            ticket_id=ticket_id,
            client=client,
            cfg=cfg,
            triage_agent=triage_agent,
            research_agent=research_agent,
            writer_agent=writer_agent,
            evidence_gate=evidence_gate,
            retrieve_documents=retrieve_documents,
        )
        trace = _stamp_cost(_trace_from_state(state, started), _stage_ms(state))
        _log_request(
            trace,
            status="failed" if state.error else "completed",
            error_category=getattr(state, "error_category", None),
        )
        return trace
    except (ValueError, genai_errors.ClientError, genai_errors.ServerError) as exc:
        _log_request(None, status="error", error_category=obs.error_category(exc), started=started)
        raise
    except Exception as exc:  # noqa: BLE001 - capture for eval reporting
        logger.exception("analyze_ticket_with_trace failed for %s", ticket_model.ticket_id)
        failed = AgentResponse(
            ticket_id=ticket_model.ticket_id,
            category=TicketCategory.OTHER,
            outcome=AgentOutcome.FALLBACK,
            recommendations=[FALLBACK_MESSAGE],
            sources=[],
            sufficient_evidence=False,
            fallback_triggered=True,
            summary=FALLBACK_MESSAGE,
        )
        failed_trace = TicketAnalysisTrace(
            response=failed,
            classification_category=TicketCategory.OTHER.value,
            gemini_requested_retrieval=False,
            retrieval_source="error",
            retrieval_performed=False,
            evidence=[],
            validation_sufficient=False,
            validation_reason="The analysis could not be completed.",
            tool_calls=[],
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error="The analysis could not be completed.",
        )
        failed_trace = _stamp_cost(failed_trace, {})
        _log_request(failed_trace, status="failed", error_category=obs.INTERNAL_ERROR)
        return failed_trace
    finally:
        reset_usage(usage_token)


class CoordinatorFailure(RuntimeError):
    """The coordinator could not finish. The message is a fixed, client-safe sentence."""


def handle_support_ticket(
    ticket: SupportTicket | dict[str, Any] | str,
    *,
    ticket_id: str = "adhoc",
    client: genai.Client | None = None,
    cfg: Settings | None = None,
    triage_agent: Any | None = None,
    research_agent: Any | None = None,
    writer_agent: Any | None = None,
    evidence_gate: Any | None = None,
    retrieve_documents: Any | None = None,
) -> AgentResponse:
    """
    Coordinator path:
    1) validate the ticket
    2) run Triage once
    3) clarification_needed when essential details are missing (no retrieval)
    4) fallback when the request is not troubleshooting
    5) run Researcher and the evidence gate
    6) fallback without Writer when evidence is insufficient
    7) run Writer on accepted passages only, then assemble AgentResponse
    """
    trace = analyze_ticket_with_trace(
        ticket,
        ticket_id=ticket_id,
        client=client,
        cfg=cfg,
        triage_agent=triage_agent,
        research_agent=research_agent,
        writer_agent=writer_agent,
        evidence_gate=evidence_gate,
        retrieve_documents=retrieve_documents,
    )
    if trace.error:
        raise CoordinatorFailure(trace.error)
    return trace.response



def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ticket = SupportTicket(
        ticket_id="T-DB-001",
        description=(
            "Users cannot connect to the application because the database "
            "connection keeps timing out."
        ),
    )
    result = handle_support_ticket(ticket)
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()
