"""FastAPI entry point for the technical support agent."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from google.genai import errors as genai_errors

from app.agent import handle_support_ticket
from app.schemas import AgentResponse, SupportTicket

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Agentic Technical Support",
    description=(
        "Classify support tickets, retrieve technical documentation, and return "
        "evidence-grounded troubleshooting recommendations, clarifying questions when "
        "the ticket is too vague, or a safe fallback when documentation is insufficient."
    ),
    version="0.1.0",
)


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
            detail=str(exc),
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
        detail = (
            f"Upstream Gemini service error: {upstream}"
            if upstream
            else "Upstream Gemini service error. Please try again shortly."
        )
        lowered = upstream.lower()
        http_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if "high demand" in lowered or "try again" in lowered
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=http_status, detail=detail) from exc
    except RuntimeError as exc:
        logger.error("Agent runtime error ticket_id=%s: %s", ticket.ticket_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
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
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc)
    try:
        if int(code) == 429:
            return (
                "Gemini API rate limit exceeded. Please wait and retry. "
                f"Upstream message: {message}"
            )
    except (TypeError, ValueError):
        pass
    return f"Gemini API client error: {message}"
