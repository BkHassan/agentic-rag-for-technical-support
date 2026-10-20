"""API tests for POST /tickets/analyze (agent mocked; no live Gemini)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from app.main import app
from app.schemas import AgentResponse, TicketCategory

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
