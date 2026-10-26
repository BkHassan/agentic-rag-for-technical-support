"""Tests for ticket classification (independent of retrieval)."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.agent import (
    _parse_classification,
    classify_ticket,
    is_daily_quota_error,
    retry_delay_seconds,
)
from app.schemas import SupportTicket, TicketCategory, TicketClassification

REQUIRES_API_KEY = pytest.mark.skipif(
    not os.getenv("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY is required for live Gemini classification tests",
)


def test_parse_classification_from_json_text() -> None:
    response = SimpleNamespace(
        parsed=None,
        text='{"category":"database","rationale":"Database connection timeouts."}',
    )
    result = _parse_classification(response)
    assert result.category == TicketCategory.DATABASE
    assert "database" in result.rationale.lower() or "timeout" in result.rationale.lower()


def test_parse_classification_from_parsed_model() -> None:
    parsed = TicketClassification(
        category=TicketCategory.AUTHENTICATION,
        rationale="Password reset login failure.",
    )
    result = _parse_classification(SimpleNamespace(parsed=parsed, text=None))
    assert result.category == TicketCategory.AUTHENTICATION


def test_per_minute_retry_uses_gemini_hint() -> None:
    message = "Quota exceeded. Generate content requests per minute. Please retry in 39.5s."
    assert is_daily_quota_error(message) is False
    assert retry_delay_seconds(message, attempt=1, base_delay_sec=1.5) == 40.5


def test_daily_quota_is_not_treated_as_a_short_retry() -> None:
    message = "You exceeded your current quota. per day limit exceeded. Please retry in 15h."
    assert is_daily_quota_error(message) is True
    assert retry_delay_seconds(message, attempt=2, base_delay_sec=1.5) == 3.0


@REQUIRES_API_KEY
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "Users cannot connect to the application because the database connection keeps timing out.",
            TicketCategory.DATABASE,
        ),
        (
            "Employees cannot resolve internal hostnames after the DNS change.",
            TicketCategory.NETWORK,
        ),
        (
            "A user cannot authenticate after a password reset.",
            TicketCategory.AUTHENTICATION,
        ),
        (
            "Customers are receiving HTTP 504 gateway timeout errors on checkout.",
            TicketCategory.APPLICATION,
        ),
    ],
)
def test_classify_ticket_live(description: str, expected: TicketCategory) -> None:
    ticket = SupportTicket(ticket_id="T-TEST", description=description)
    result = classify_ticket(ticket)
    assert result.category == expected
    assert result.rationale.strip()
