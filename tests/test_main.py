"""API tests for POST /tickets/analyze (agent mocked; no live Gemini)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from app.main import app
from app.orchestration.coordinator import CoordinatorFailure
from app.orchestration.workflow import CLIENT_TIMEOUT
from app.schemas import AgentOutcome, AgentResponse, DocumentSource, SupportTicket, TicketCategory

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_analyze_ticket_validation_rejects_blank_description() -> None:
    response = client.post(
        "/tickets/analyze",
        json={"ticket_id": "T-1", "description": "   "},
    )
    assert response.status_code == 422


def test_analyze_ticket_returns_agent_response() -> None:
    payload = AgentResponse(
        ticket_id="T-DB-001",
        category=TicketCategory.DATABASE,
        outcome=AgentOutcome.GROUNDED_ANSWER,
        recommendations=["Verify DB credentials."],
        sources=[],
        sufficient_evidence=True,
        fallback_triggered=False,
        summary="Grounded in database docs.",
    )
    with patch("app.main.handle_support_ticket", return_value=payload):
        response = client.post(
            "/tickets/analyze",
            json={
                "ticket_id": "T-DB-001",
                "description": "Database connection keeps timing out.",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == "T-DB-001"
    assert body["category"] == "database"
    assert body["sufficient_evidence"] is True
    assert body["fallback_triggered"] is False
    assert body["recommendations"] == ["Verify DB credentials."]


def test_analyze_ticket_maps_missing_api_key_to_400() -> None:
    with patch(
        "app.main.handle_support_ticket",
        side_effect=ValueError("GOOGLE_API_KEY is not set."),
    ):
        response = client.post(
            "/tickets/analyze",
            json={"ticket_id": "T-1", "description": "DNS failure"},
        )
    assert response.status_code == 400
    assert "GOOGLE_API_KEY" in response.json()["detail"]


def test_analyze_ticket_maps_rate_limit_to_429() -> None:
    rate_limit_error = genai_errors.ClientError(
        429,
        {"error": {"message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}},
    )
    with patch("app.main.handle_support_ticket", side_effect=rate_limit_error):
        response = client.post(
            "/tickets/analyze",
            json={"ticket_id": "T-1", "description": "504 gateway timeout"},
        )
    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"].lower()


# --- Coordinator integration (Coordinator mocked; no live Gemini or ChromaDB) ---

TICKET = {"ticket_id": "T-API-1", "description": "Database connection keeps timing out."}
RESPONSE_FIELDS = set(AgentResponse.model_fields)


def _response(outcome: AgentOutcome, ticket_id: str = "T-API-1") -> AgentResponse:
    if outcome is AgentOutcome.GROUNDED_ANSWER:
        return AgentResponse(
            ticket_id=ticket_id,
            category=TicketCategory.DATABASE,
            outcome=outcome,
            recommendations=["Raise the pool timeout."],
            sources=[DocumentSource(source="database.md", doc_id="db", chunk_index=0)],
            sufficient_evidence=True,
            fallback_triggered=False,
            summary="Grounded in database docs.",
        )
    if outcome is AgentOutcome.CLARIFICATION_NEEDED:
        return AgentResponse(
            ticket_id=ticket_id,
            category=TicketCategory.OTHER,
            outcome=outcome,
            clarifying_questions=["Which service fails?"],
            recommendations=["Please answer the questions above."],
            sources=[],
            sufficient_evidence=False,
            fallback_triggered=False,
        )
    return AgentResponse(
        ticket_id=ticket_id,
        category=TicketCategory.DATABASE,
        outcome=outcome,
        recommendations=["Escalate to a human engineer."],
        sources=[],
        sufficient_evidence=False,
        fallback_triggered=True,
    )


def _assert_safe(body: dict, *forbidden: str) -> None:
    text = str(body)
    assert "Traceback" not in text
    for value in forbidden:
        assert value not in text


@pytest.mark.parametrize("outcome", list(AgentOutcome))
def test_analyze_ticket_returns_each_coordinator_outcome_unchanged(outcome: AgentOutcome) -> None:
    expected = _response(outcome)
    with patch("app.main.handle_support_ticket", return_value=expected) as coordinator:
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == RESPONSE_FIELDS
    assert body == expected.model_dump(mode="json")
    coordinator.assert_called_once()
    (passed,), _ = coordinator.call_args
    assert isinstance(passed, SupportTicket)
    assert passed.ticket_id == TICKET["ticket_id"]


def test_analyze_ticket_maps_coordinator_timeout_to_503() -> None:
    with patch("app.main.handle_support_ticket", side_effect=CoordinatorFailure(CLIENT_TIMEOUT)):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 503
    assert response.json() == {"detail": CLIENT_TIMEOUT}


def test_infrastructure_failure_inside_coordinator_is_not_a_success() -> None:
    # Real handle_support_ticket wrapper; the workflow itself fails.
    with patch(
        "app.orchestration.workflow.coordinate_ticket",
        side_effect=OSError("chroma at /srv/chroma unreachable GOOGLE_API_KEY=secret"),
    ):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 503
    assert response.json() == {"detail": "The analysis could not be completed."}
    _assert_safe(response.json(), "secret", "/srv/chroma")


def test_unknown_runtime_error_detail_is_not_exposed() -> None:
    with patch("app.main.handle_support_ticket", side_effect=RuntimeError("prompt: SYSTEM secret")):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 503
    _assert_safe(response.json(), "secret", "SYSTEM")


def test_value_error_detail_is_not_exposed() -> None:
    with patch("app.main.handle_support_ticket", side_effect=ValueError("internal doc_id=hr-salaries")):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 400
    _assert_safe(response.json(), "hr-salaries")


def test_missing_api_key_detail_never_echoes_a_key_value() -> None:
    with patch("app.main.handle_support_ticket", side_effect=ValueError("GOOGLE_API_KEY=secret is invalid")):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 400
    assert "GOOGLE_API_KEY" in response.json()["detail"]
    _assert_safe(response.json(), "secret")


def test_gemini_client_error_hides_upstream_message() -> None:
    error = genai_errors.ClientError(
        400, {"error": {"message": "Invalid value at 'system_instruction' (secret prompt)"}}
    )
    with patch("app.main.handle_support_ticket", side_effect=error):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 400
    _assert_safe(response.json(), "system_instruction", "secret prompt")


def test_rate_limit_keeps_only_the_retry_delay() -> None:
    error = genai_errors.ClientError(
        429, {"error": {"message": "Quota for project secret-proj exceeded. Please retry in 41.2s."}}
    )
    with patch("app.main.handle_support_ticket", side_effect=error):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert "rate limit" in detail.lower()
    assert "retry in 41.2s" in detail
    _assert_safe(response.json(), "secret-proj")


@pytest.mark.parametrize(
    ("message", "expected_status"),
    [("The model is overloaded due to high demand.", 503), ("Internal error at host-7", 502)],
)
def test_gemini_server_error_keeps_status_and_hides_message(message: str, expected_status: int) -> None:
    error = genai_errors.ServerError(500, {"error": {"message": message}})
    with patch("app.main.handle_support_ticket", side_effect=error):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == expected_status
    _assert_safe(response.json(), message)


def test_unexpected_error_returns_generic_500() -> None:
    with patch("app.main.handle_support_ticket", side_effect=KeyError("secret")):
        response = client.post("/tickets/analyze", json=TICKET)

    assert response.status_code == 500
    _assert_safe(response.json(), "secret")


def test_concurrent_requests_do_not_share_state() -> None:
    barrier = threading.Barrier(4, timeout=10)

    def fake_coordinator(ticket: SupportTicket) -> AgentResponse:
        barrier.wait()  # all four requests are in flight together
        return _response(AgentOutcome.GROUNDED_ANSWER, ticket_id=ticket.ticket_id)

    def post(ticket_id: str):
        return client.post(
            "/tickets/analyze",
            json={"ticket_id": ticket_id, "description": "DNS lookups fail."},
        )

    ids = [f"T-CONC-{n}" for n in range(4)]
    with patch("app.main.handle_support_ticket", side_effect=fake_coordinator):
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(post, ids))

    assert [r.status_code for r in responses] == [200] * 4
    assert [r.json()["ticket_id"] for r in responses] == ids
