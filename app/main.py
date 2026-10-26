"""FastAPI entry point for the technical support agent."""

from __future__ import annotations

import logging
import re

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from google.genai import errors as genai_errors

from app import observability as obs
from app.orchestration import handle_support_ticket
from app.orchestration.coordinator import CoordinatorFailure
from app.schemas import AgentResponse, SupportTicket

logger = logging.getLogger(__name__)

# Client-facing details are fixed sentences. Exception text, upstream Gemini messages,
# and configuration values stay in the server log.
DETAIL_MISSING_API_KEY = "GOOGLE_API_KEY is not configured on the server."
DETAIL_BAD_REQUEST = "The ticket could not be processed."
DETAIL_RATE_LIMIT = "Gemini API rate limit exceeded. Please wait and retry."
DETAIL_CLIENT_ERROR = "Gemini API client error."
DETAIL_SERVER_ERROR = "Upstream Gemini service error. Please try again shortly."
DETAIL_UNAVAILABLE = "The analysis could not be completed."

# Same shape the frontend parses: "retry in 41.2s", "retry in 7h9m23.6s".
_RETRY_IN_RE = re.compile(r"retry in ((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)", re.IGNORECASE)

app = FastAPI(
    title="Agentic Technical Support",
    description=(
        "Classify support tickets, retrieve technical documentation, and return "
        "evidence-grounded troubleshooting recommendations, clarifying questions when "
        "the ticket is too vague, or a safe fallback when documentation is insufficient."
    ),
    version="0.1.0",
)


@app.middleware("http")
async def bind_request_id(request: Request, call_next):
    """One ID per request for server logs; echoed so a client can quote it to support."""
    with obs.request_context(request.headers.get("x-request-id")) as request_id:
        response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/tickets/analyze",
    response_model=AgentResponse,
    tags=["tickets"],
    summary="Analyze a support ticket",
    response_description=(
        "Ticket category, outcome (grounded_answer, clarification_needed, or fallback), "
        "recommendations or clarifying questions, source references, and evidence status flags."
    ),
    responses={
        400: {"description": "Invalid request or missing configuration (e.g. API key)"},
        429: {"description": "Gemini API rate limit exceeded"},
        502: {"description": "Upstream Gemini client/server error"},
        503: {"description": "Agent runtime unavailable"},
        422: {"description": "Request validation error"},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "database_timeout": {
                            "summary": "Database connection timeout",
                            "value": {
                                "ticket_id": "T-DB-001",
                                "description": (
                                    "Users cannot connect to the application because "
                                    "the database connection keeps timing out."
                                ),
                            },
                        },
                        "vague_ticket": {
                            "summary": "Too vague: asks for clarification",
                            "value": {
                                "ticket_id": "T-VAGUE-001",
                                "description": "It stopped working after yesterday.",
                            },
                        },
                    }
                }
            }
        }
    },
)
def analyze_ticket(ticket: SupportTicket) -> AgentResponse:
    """
    Accept a support ticket and run the agent pipeline.

    Returns classification, `outcome`, recommendations or clarifying questions,
    cited sources, and `sufficient_evidence` / `fallback_triggered` flags.
    """
    try:
        return handle_support_ticket(ticket)
    except ValueError as exc:
        logger.warning("Bad request for ticket_id=%s: %s", ticket.ticket_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_bad_request_detail(exc),
        ) from exc
    except genai_errors.ClientError as exc:
        http_status = _client_error_http_status(exc)
        logger.error(
            "Gemini client error ticket_id=%s status=%s detail=%s",
            ticket.ticket_id,
            http_status,
            getattr(exc, "message", str(exc)),
        )
        raise HTTPException(
            status_code=http_status,
            detail=_client_error_detail(exc),
        ) from exc
    except genai_errors.ServerError as exc:
        upstream = getattr(exc, "message", None) or str(exc)
        logger.error(
            "Gemini server error ticket_id=%s: %s",
            ticket.ticket_id,
            upstream,
        )
        # High demand / temporary Gemini outages are unavailable, not a bad gateway.
        lowered = upstream.lower()
        http_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if "high demand" in lowered or "try again" in lowered
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=http_status, detail=DETAIL_SERVER_ERROR) from exc
    except CoordinatorFailure as exc:
        # The coordinator's message is already a fixed public sentence (e.g. a timeout).
        logger.error("Coordinator failure ticket_id=%s: %s", ticket.ticket_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc) or DETAIL_UNAVAILABLE,
        ) from exc
    except RuntimeError as exc:
        logger.exception("Agent runtime error ticket_id=%s", ticket.ticket_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=DETAIL_UNAVAILABLE,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - API boundary
        logger.exception("Unexpected failure analyzing ticket_id=%s", ticket.ticket_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while analyzing the ticket.",
        ) from exc


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a clean JSON body for unexpected errors outside route handlers."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error."},
    )


def _client_error_http_status(exc: genai_errors.ClientError) -> int:
    code = getattr(exc, "code", None)
    try:
        code_int = int(code) if code is not None else status.HTTP_400_BAD_REQUEST
    except (TypeError, ValueError):
        code_int = status.HTTP_400_BAD_REQUEST

    if code_int == 429:
        return status.HTTP_429_TOO_MANY_REQUESTS
    if code_int == 404:
        return status.HTTP_502_BAD_GATEWAY
    if code_int in {401, 403}:
        return status.HTTP_502_BAD_GATEWAY
    if 400 <= code_int < 500:
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_502_BAD_GATEWAY


def _client_error_detail(exc: genai_errors.ClientError) -> str:
    if _client_error_http_status(exc) != status.HTTP_429_TOO_MANY_REQUESTS:
        return DETAIL_CLIENT_ERROR
    # Pass on only the retry delay, which the console uses for its countdown.
    match = _RETRY_IN_RE.search(getattr(exc, "message", None) or str(exc))
    if match is None:
        return DETAIL_RATE_LIMIT
    return f"Gemini API rate limit exceeded. Please retry in {match.group(1)}."


def _bad_request_detail(exc: ValueError) -> str:
    if "GOOGLE_API_KEY" in str(exc):
        return DETAIL_MISSING_API_KEY
    return DETAIL_BAD_REQUEST
