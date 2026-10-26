"""Tests for the clarification decision (no live API required)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agent import analyze_ticket_with_trace
from app.agents.state import ClarificationRecord
from app.agents.triage import TriageAssessment
from app.policies.clarification import (
    CLARIFICATION_MESSAGE,
    QUESTION_AFFECTED_SYSTEM,
    QUESTION_OBSERVABLE_SYMPTOM,
    decide_clarification,
)
from app.schemas import (
    AgentOutcome,
    SupportTicket,
    TicketCategory,
    TicketCompleteness,
    TicketTriage,
)

E_REL_004 = "Employees cannot resolve internal hostnames after the DNS change. IP access still works."


def _facts(system: str | None, symptom: str | None, troubleshooting: bool = True) -> TicketCompleteness:
    return TicketCompleteness(
        is_troubleshooting_request=troubleshooting,
        affected_system=system,
        observable_symptom=symptom,
    )


def test_specific_ticket_does_not_need_clarification() -> None:
    description = "Customers are receiving HTTP 504 gateway timeout errors on the checkout page."
    decision = decide_clarification(
        description, _facts("checkout page", "receiving HTTP 504 gateway timeout errors")
    )
    assert decision.needed is False


def test_out_of_corpus_but_specific_ticket_is_not_a_clarification_case() -> None:
    """Asking the reporter more cannot fill a documentation gap."""
    description = "Apache Kafka consumers lag behind and throw OffsetOutOfRangeException."
    decision = decide_clarification(
        description, _facts("Apache Kafka consumers", "throw OffsetOutOfRangeException")
    )
    assert decision.needed is False


def test_specific_symptom_without_named_system_does_not_need_clarification() -> None:
    description = "Our service returns 503 Service Unavailable during traffic spikes."
    decision = decide_clarification(
        description, _facts("service", "returns 503 Service Unavailable")
    )
    assert decision.needed is False


def test_contractions_do_not_count_as_specific_words() -> None:
    decision = decide_clarification(
        "I can't do my work, nothing is working.", _facts(None, "I can't do my work")
    )
    assert decision.needed is True


def test_generic_system_and_symptom_need_clarification() -> None:
    decision = decide_clarification(
        "The application is broken for some users.", _facts("The application", "broken")
    )
    assert decision.needed is True
    assert decision.missing == ["affected_system", "observable_symptom"]
    assert decision.questions[:2] == [QUESTION_AFFECTED_SYSTEM, QUESTION_OBSERVABLE_SYMPTOM]


def test_symptom_that_only_repeats_the_system_is_missing() -> None:
    description = "Something is wrong with login or maybe the network. Please fix ASAP."
    decision = decide_clarification(
        description, _facts("login or maybe the network", "Something is wrong with login")
    )
    assert decision.needed is True
    assert decision.missing == ["observable_symptom"]


def test_vague_behavior_words_are_not_a_symptom() -> None:
    decision = decide_clarification("The VPN is acting weird.", _facts("VPN", "acting weird"))
    assert decision.needed is True
    assert decision.missing == ["observable_symptom"]


@pytest.mark.parametrize(
    ("description", "system", "symptom"),
    [
        (  # E-REL-006
            "The application reports too many connections when opening new database sessions under peak load.",
            "database sessions",
            "reports too many connections",
        ),
        (  # C-ANS-03
            "The load balancer returns 504 when the reports endpoint runs a long export.",
            "load balancer",
            "returns 504",
        ),
    ],
)
def test_specific_symptoms_next_to_load_words_still_skip_clarification(
    description: str, system: str, symptom: str
) -> None:
    assert decide_clarification(description, _facts(system, symptom)).needed is False


def test_phrases_not_found_in_the_ticket_are_not_trusted() -> None:
    decision = decide_clarification(
        "It stopped working after yesterday.",
        _facts("payment service", "returns HTTP 500"),
    )
    assert decision.needed is True
    assert decision.missing == ["affected_system", "observable_symptom"]


def test_non_troubleshooting_requests_are_not_clarified() -> None:
    decision = decide_clarification(
        "Thanks for resolving ticket INC-4421 yesterday.", _facts(None, None, troubleshooting=False)
    )
    assert decision.needed is False


def test_unassessed_completeness_does_not_trigger_clarification() -> None:
    assert decide_clarification("Database connection keeps timing out.", None).needed is False


def test_unexplained_detail_lists_words_outside_the_system_name() -> None:
    specific = decide_clarification(E_REL_004, _facts("internal hostnames", None))
    assert {"resolve", "dns", "ip"} <= set(specific.unexplained_detail)
    vague = decide_clarification("We're seeing errors since this morning.", _facts(None, None))
    assert vague.unexplained_detail == []


def _assessment(ticket: SupportTicket, completeness: TicketCompleteness, category: TicketCategory) -> TriageAssessment:
    classification = TicketTriage(
        category=category,
        rationale="Triage fixture.",
        completeness=completeness,
    )
    decision = decide_clarification(ticket.description, completeness)
    return TriageAssessment(
        classification=classification,
        clarification=ClarificationRecord(
            needed=decision.needed,
            questions=list(decision.questions),
            reason=decision.reason,
        ),
        missing=list(decision.missing),
    )


def _blocked_agent(name: str) -> MagicMock:
    return MagicMock(side_effect=AssertionError(f"{name} must not run"))


def test_clarification_returns_questions_without_offering_search() -> None:
    ticket = SupportTicket(ticket_id="T-VAGUE", description="It stopped working after yesterday.")
    research = _blocked_agent("research")
    writer = _blocked_agent("writer")
    retrieve = _blocked_agent("retrieve")
    trace = analyze_ticket_with_trace(
        ticket,
        triage_agent=lambda *args, **kwargs: _assessment(ticket, _facts(None, "stopped working"), TicketCategory.OTHER),
        research_agent=research,
        writer_agent=writer,
        retrieve_documents=retrieve,
    )

    research.assert_not_called()
    writer.assert_not_called()
    retrieve.assert_not_called()
    response = trace.response
    assert response.outcome == AgentOutcome.CLARIFICATION_NEEDED
    assert response.summary == CLARIFICATION_MESSAGE
    assert response.recommendations == []
    assert QUESTION_AFFECTED_SYSTEM in response.clarifying_questions
    assert response.fallback_triggered is False
    assert trace.gemini_requested_retrieval is False
    assert trace.clarification_missing == ["affected_system", "observable_symptom"]
    assert trace.completeness["observable_symptom"] == "stopped working"


def test_vague_ticket_clarifies_without_retrieving_documents() -> None:
    ticket = SupportTicket(ticket_id="T-VAGUE-2", description="Nothing loads on the portal.")
    research = _blocked_agent("research")
    retrieve = _blocked_agent("retrieve")
    trace = analyze_ticket_with_trace(
        ticket,
        triage_agent=lambda *args, **kwargs: _assessment(ticket, _facts(None, None), TicketCategory.OTHER),
        research_agent=research,
        writer_agent=_blocked_agent("writer"),
        retrieve_documents=retrieve,
    )

    retrieve.assert_not_called()
    research.assert_not_called()
    assert trace.response.outcome == AgentOutcome.CLARIFICATION_NEEDED
    assert trace.clarification_overridden is False


def test_missing_symptom_clarifies_without_retrieval() -> None:
    """Essential details are missing: the coordinator asks, and does not search."""
    ticket = SupportTicket(ticket_id="E-REL-004", description=E_REL_004)
    research = _blocked_agent("research")
    retrieve = _blocked_agent("retrieve")
    trace = analyze_ticket_with_trace(
        ticket,
        triage_agent=lambda *args, **kwargs: _assessment(
            ticket, _facts("internal hostnames", None), TicketCategory.NETWORK
        ),
        research_agent=research,
        writer_agent=_blocked_agent("writer"),
        retrieve_documents=retrieve,
    )

    research.assert_not_called()
    retrieve.assert_not_called()
    assert trace.clarification_overridden is False
    assert trace.response.outcome == AgentOutcome.CLARIFICATION_NEEDED


def test_ticket_that_only_names_a_system_asks_for_the_symptom() -> None:
    """R-AMB-003: 'VPN' names a system, but no symptom is described."""
    ticket = SupportTicket(ticket_id="R-AMB-003", description="The VPN is acting weird.")
    research = _blocked_agent("research")
    retrieve = _blocked_agent("retrieve")
    trace = analyze_ticket_with_trace(
        ticket,
        triage_agent=lambda *args, **kwargs: _assessment(ticket, _facts("VPN", None), TicketCategory.NETWORK),
        research_agent=research,
        writer_agent=_blocked_agent("writer"),
        retrieve_documents=retrieve,
    )

    retrieve.assert_not_called()
    research.assert_not_called()
    assert trace.response.outcome == AgentOutcome.CLARIFICATION_NEEDED
    assert trace.clarification_missing == ["observable_symptom"]


def test_non_troubleshooting_ticket_skips_research_and_writer() -> None:
    ticket = SupportTicket(ticket_id="E-NRT", description="Thanks, that fixed it.")
    research = _blocked_agent("research")
    writer = _blocked_agent("writer")
    retrieve = _blocked_agent("retrieve")
    trace = analyze_ticket_with_trace(
        ticket,
        triage_agent=lambda *args, **kwargs: _assessment(
            ticket, TicketCompleteness(is_troubleshooting_request=False), TicketCategory.OTHER
        ),
        research_agent=research,
        writer_agent=writer,
        retrieve_documents=retrieve,
    )

    research.assert_not_called()
    writer.assert_not_called()
    retrieve.assert_not_called()
    assert trace.response.outcome == AgentOutcome.FALLBACK
    assert trace.gemini_requested_retrieval is False
    assert trace.stage_ms["triage"] >= 0
