"""Technical support agent: classify, retrieve via tools, and ground recommendations."""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings, settings
from app.rag.guardrails import (
    FALLBACK_MESSAGE,
    EvidenceValidationResult,
    assess_evidence,
    validate_retrieval_evidence,
)
from app.rag.ingestion import get_genai_client
from app.schemas import (
    AgentResponse,
    DocumentSource,
    GroundedAdvice,
    SupportTicket,
    TicketCategory,
    TicketClassification,
)
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

AGENT_SYSTEM_INSTRUCTION = """
You are a technical support agent.

You may call search_technical_documentation to look up internal technical docs.
Gemini does not run that function itself — you only request it; the host application
executes the search and returns structured evidence.

When documentation is useful, call search_technical_documentation with a focused query.
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
        f"Ticket ID: {ticket_model.ticket_id}\n"
        f"Ticket description:\n{ticket_model.description}\n\n"
        "Return the best-fitting category and a short rationale."
    )

    logger.info("Classifying ticket_id=%s", ticket_model.ticket_id)
    response = client.models.generate_content(
        model=cfg.llm_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=TicketClassification,
        ),
    )

    classification = _parse_classification(response)
    logger.info(
        "Classified ticket_id=%s category=%s",
        ticket_model.ticket_id,
        classification.category.value,
    )
    return classification


def _parse_classification(response: Any) -> TicketClassification:
    """Normalize Gemini structured output into TicketClassification."""
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
    client: genai.Client | None = None,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    """
    Agentic workflow with explicit (manual) function calling.

    Gemini proposes tool calls; this application executes them and returns results.
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
        "Investigate this support ticket. Call search_technical_documentation to "
        "retrieve documented diagnostic steps before recommending actions.\n\n"
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
        response = client.models.generate_content(
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
            response_parts.append(
                types.Part.from_function_response(
                    name=name,
                    response={"result": result},
                )
            )

        contents.append(types.Content(role="user", parts=response_parts))
    else:
        response = client.models.generate_content(
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
        recommendations=[FALLBACK_MESSAGE],
        sources=_sources_from_evidence(validation.retrieved_chunks),
        sufficient_evidence=False,
        fallback_triggered=True,
        summary=FALLBACK_MESSAGE,
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

    relevant_evidence = validation.relevant_chunks

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

    response = client.models.generate_content(
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
    cfg: Settings | None = None,
) -> tuple[list[dict[str, Any]], bool, str]:
    """
    Collect retrieval evidence and record whether retrieval actually ran.

    If Gemini did not request the knowledge-base tool, the application may run a
    deterministic guardrail search. Either way, missing retrieval is never treated
    as sufficient evidence — validation still decides.
    """
    cfg = cfg or settings
    gemini_requested = bool(tool_outcome.get("used_knowledge_base"))
    evidence = list(tool_outcome.get("evidence") or [])

    if gemini_requested:
        return evidence, True, "gemini_tool"

    logger.info(
        "Gemini did not request retrieval for ticket_id=%s; "
        "running application guardrail search (still subject to validation)",
        ticket.ticket_id,
    )
    host_search = execute_tool_call(
        SEARCH_TECHNICAL_DOCUMENTATION_NAME,
        {"query": ticket.description},
        cfg=cfg,
    )
    evidence = list(host_search.get("results") or [])
    # Retrieval was performed by the application, not implied by a missing tool call.
    return evidence, True, "application_guardrail"


def handle_support_ticket(
    ticket: SupportTicket | dict[str, Any] | str,
    *,
    ticket_id: str = "adhoc",
    client: genai.Client | None = None,
    cfg: Settings | None = None,
) -> AgentResponse:
    """
    Full agent path:
    1) classify
    2) let Gemini request documentation search (app executes the tool)
    3) validate evidence deterministically (before final LLM recommendations)
    4) generate grounded recommendations or return the safe fallback
    """
    cfg = cfg or settings
    client = client or get_genai_client(cfg.google_api_key)
    ticket_model = _as_ticket(ticket, ticket_id=ticket_id)

    classification = classify_ticket(ticket_model, client=client, cfg=cfg)
    tool_outcome = run_tool_calling_turn(ticket_model, client=client, cfg=cfg)
    evidence, retrieval_performed, retrieval_source = collect_evidence_for_ticket(
        ticket_model,
        tool_outcome,
        cfg=cfg,
    )

    validation = validate_retrieval_evidence(
        evidence,
        retrieval_performed=retrieval_performed,
        cfg=cfg,
    )
    logger.info(
        "ticket_id=%s retrieval_source=%s validation=%s",
        ticket_model.ticket_id,
        retrieval_source,
        validation.reason,
    )

    return generate_grounded_recommendations(
        ticket_model,
        category=classification.category,
        evidence=evidence,
        retrieval_performed=retrieval_performed,
        client=client,
        cfg=cfg,
        validation=validation,
    )


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
