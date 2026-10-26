"""Technical support agent: classify, retrieve via tools, and ground recommendations."""

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

from app.config import Settings, settings
from app.rag.guardrails import (
    FALLBACK_MESSAGE,
    EvidenceValidationResult,
    assess_evidence,
    validate_retrieval_evidence,
)
from app.rag.ingestion import get_genai_client
from app.rag.retrieval import retrieve
from app.clarification import CLARIFICATION_MESSAGE, ClarificationDecision, decide_clarification
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
from app.usage import current_usage, note_gemini_call, reset_usage, start_usage
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


def _is_transient_gemini_error(exc: Exception) -> bool:
    message = (getattr(exc, "message", None) or str(exc)).lower()
    code = getattr(exc, "code", None)
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None
    return code_int in {429, 503, 500} or any(
        needle in message
        for needle in (
            "high demand",
            "try again later",
            "temporarily unavailable",
            "resource exhausted",
            "unavailable",
        )
    )


_RETRY_AFTER_RE = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def _gemini_error_message(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc)


def is_daily_quota_error(message: str | None) -> bool:
    """True when Gemini rejected the call for the daily free-tier cap."""
    text = (message or "").lower()
    return "per day" in text or "daily" in text


def retry_delay_seconds(message: str, attempt: int, base_delay_sec: float) -> float:
    """Use Gemini's retry hint for per-minute limits, capped so a daily cap does not sleep for hours."""
    match = _RETRY_AFTER_RE.search(message or "")
    if match and not is_daily_quota_error(message):
        return min(float(match.group(1)) + 1.0, 90.0)
    return base_delay_sec * attempt


def _generate_content_with_retry(
    client: genai.Client,
    *,
    model: str,
    contents: Any,
    config: types.GenerateContentConfig,
    max_attempts: int = 5,
    base_delay_sec: float = 1.5,
) -> Any:
    """Call Gemini with retries for transient overload and per-minute rate limits."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        note_gemini_call()
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except (genai_errors.ServerError, genai_errors.ClientError) as exc:
            last_exc = exc
            message = _gemini_error_message(exc)
            if is_daily_quota_error(message) or attempt >= max_attempts or not _is_transient_gemini_error(exc):
                raise
            delay = retry_delay_seconds(message, attempt, base_delay_sec)
            logger.warning(
                "Transient Gemini error on attempt %s/%s (%s). Retrying in %.1fs",
                attempt,
                max_attempts,
                message,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


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

    relevant_evidence = validation.relevant_chunks[: cfg.retrieval_top_k]

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


def _stamp_cost(trace: TicketAnalysisTrace, stage_ms: dict[str, float]) -> TicketAnalysisTrace:
    usage = current_usage()
    if usage is not None:
        trace.gemini_calls = usage.gemini_calls
        trace.embed_api_calls = usage.embed_api_calls
        trace.embed_cache_hits = usage.embed_cache_hits
    trace.stage_ms = stage_ms
    logger.info(
        "Cost ticket_id=%s outcome=%s gemini_calls=%s embed_api_calls=%s embed_cache_hits=%s stage_ms=%s",
        trace.response.ticket_id,
        trace.response.outcome.value,
        trace.gemini_calls,
        trace.embed_api_calls,
        trace.embed_cache_hits,
        stage_ms,
    )
    return trace


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
) -> TicketAnalysisTrace:
    """Run the full agent path and return response + evaluation trace."""
    cfg = cfg or settings
    started = time.perf_counter()
    ticket_model = _as_ticket(ticket, ticket_id=ticket_id)
    stage_ms: dict[str, float] = {}
    usage_token = start_usage()
    try:
        client = client or get_genai_client(cfg.google_api_key)
        stage_started = time.perf_counter()
        classification = classify_ticket(ticket_model, client=client, cfg=cfg)
        stage_ms["classify"] = round((time.perf_counter() - stage_started) * 1000.0, 1)
        completeness = (
            classification.completeness.model_dump() if classification.completeness else None
        )
        decision = decide_clarification(ticket_model.description, classification.completeness)
        clarification_overridden = False
        candidates: list[dict[str, Any]] = []
        if decision.needed:
            specificity: EvidenceValidationResult | None = None
            if decision.unexplained_detail:
                stage_started = time.perf_counter()
                candidates, specificity = check_ticket_specificity(ticket_model, cfg=cfg)
                stage_ms["specificity"] = round((time.perf_counter() - stage_started) * 1000.0, 1)
            if specificity is not None and specificity.sufficient_evidence:
                clarification_overridden = True
                logger.info(
                    "Clarification overridden ticket_id=%s missing=%s: ticket text matches "
                    "documentation strongly (%s)",
                    ticket_model.ticket_id,
                    decision.missing,
                    specificity.reason,
                )
            else:
                logger.info(
                    "Clarification needed ticket_id=%s missing=%s",
                    ticket_model.ticket_id,
                    decision.missing,
                )
                clarified = TicketAnalysisTrace(
                    response=build_clarification_response(
                        ticket_model, category=classification.category, decision=decision
                    ),
                    classification_category=classification.category.value,
                    gemini_requested_retrieval=False,
                    retrieval_source="clarification",
                    retrieval_performed=specificity is not None,
                    evidence=candidates,
                    validation_sufficient=False,
                    validation_reason=(
                        f"{decision.reason} {specificity.reason}"
                        if specificity is not None
                        else f"{decision.reason} The ticket has no detail beyond the system name."
                    ),
                    tool_calls=[],
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error=None,
                    clarification_missing=list(decision.missing),
                    completeness=completeness,
                )
                return _stamp_cost(clarified, stage_ms)

        stage_started = time.perf_counter()
        if (
            classification.completeness is not None
            and not classification.completeness.is_troubleshooting_request
        ):
            tool_outcome = {
                "used_knowledge_base": False,
                "tool_calls": [],
                "response_text": "",
            }
            logger.info(
                "Skipped tool call for ticket_id=%s: not a troubleshooting request",
                ticket_model.ticket_id,
            )
        else:
            tool_outcome = run_tool_calling_turn(
                ticket_model,
                client=client,
                cfg=cfg,
                max_tool_rounds=1,
                finish_after_tool=True,
            )
        stage_ms["tool"] = round((time.perf_counter() - stage_started) * 1000.0, 1)
        stage_started = time.perf_counter()
        evidence, retrieval_performed, retrieval_source = collect_evidence_for_ticket(
            ticket_model,
            tool_outcome,
            cfg=cfg,
            prior_ticket_hits=candidates if candidates else None,
        )
        stage_ms["evidence"] = round((time.perf_counter() - stage_started) * 1000.0, 1)
        validation = validate_retrieval_evidence(
            evidence,
            retrieval_performed=retrieval_performed,
            cfg=cfg,
        )
        stage_started = time.perf_counter()
        response = generate_grounded_recommendations(
            ticket_model,
            category=classification.category,
            evidence=evidence,
            retrieval_performed=retrieval_performed,
            client=client,
            cfg=cfg,
            validation=validation,
        )
        stage_ms["answer"] = round((time.perf_counter() - stage_started) * 1000.0, 1)
        answered = TicketAnalysisTrace(
            response=response,
            classification_category=classification.category.value,
            gemini_requested_retrieval=bool(tool_outcome.get("used_knowledge_base")),
            retrieval_source=retrieval_source,
            retrieval_performed=retrieval_performed,
            evidence=evidence,
            validation_sufficient=validation.sufficient_evidence,
            validation_reason=validation.reason,
            tool_calls=list(tool_outcome.get("tool_calls") or []),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=None,
            completeness=completeness,
            clarification_overridden=clarification_overridden,
        )
        return _stamp_cost(answered, stage_ms)
    except ValueError:
        # Config / input problems (e.g. missing GOOGLE_API_KEY) should surface to the API.
        raise
    except (genai_errors.ClientError, genai_errors.ServerError):
        # Let FastAPI map Gemini client/server failures to the correct HTTP status.
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
            validation_reason=str(exc),
            tool_calls=[],
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=str(exc),
        )
        return _stamp_cost(failed_trace, stage_ms)
    finally:
        reset_usage(usage_token)


def handle_support_ticket(
    ticket: SupportTicket | dict[str, Any] | str,
    *,
    ticket_id: str = "adhoc",
    client: genai.Client | None = None,
    cfg: Settings | None = None,
) -> AgentResponse:
    """
    Full agent path:
    1) classify and extract completeness facts
    2) if no observable symptom was extracted and the ticket text does not match one
       document strongly, ask for clarification (Gemini is not offered search)
    3) let Gemini request documentation search once (app executes the tool).
       Thanks, staffing, and announcements skip this call. No follow-up model
       call is made from the tool result; the host writes the answer.
    4) validate evidence deterministically (before final LLM recommendations),
       reusing a ticket-text search already done for the clarification check
    5) generate grounded recommendations or return the documentation fallback
    """
    trace = analyze_ticket_with_trace(
        ticket,
        ticket_id=ticket_id,
        client=client,
        cfg=cfg,
    )
    if trace.error:
        raise RuntimeError(trace.error)
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
