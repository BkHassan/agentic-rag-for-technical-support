"""Tests for the clarification decision (no live API required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.agent import analyze_ticket_with_trace
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
from tests.retrieval_fixtures import background_docs

E_REL_004 = "Employees cannot resolve internal hostnames after the DNS change. IP access still works."


def _facts(system: str | None, symptom: str | None, troubleshooting: bool = True) -> TicketCompleteness:
    return TicketCompleteness(
        is_troubleshooting_request=troubleshooting,
        affected_system=system,
        observable_symptom=symptom,
    )


def _strong_match() -> list[dict]:
    """One document clearly ahead of unrelated background documents."""
    return [
        {
            "text": "Check resolver configuration and recent record changes.",
            "source": "network_connectivity_basics.md",
            "doc_id": "network_connectivity_basics",
            "chunk_index": 0,
            "category": "network",
            "distance": 0.25,
            "score": 0.75,
        },
        *background_docs(),
    ]


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


def test_clarification_returns_questions_without_offering_search() -> None:
    ticket = SupportTicket(ticket_id="T-VAGUE", description="It stopped working after yesterday.")
    triage = TicketTriage(
        category=TicketCategory.OTHER,
        rationale="No system or symptom named.",
        completeness=_facts(None, "stopped working"),
    )
    with (
        patch("app.orchestration.coordinator.classify_ticket", return_value=triage),
        patch("app.orchestration.coordinator.run_tool_calling_turn") as tool_turn,
        patch("app.orchestration.coordinator.retrieve") as retrieve,
    ):
        trace = analyze_ticket_with_trace(ticket, client=MagicMock())

    tool_turn.assert_not_called()
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


def test_vague_ticket_with_detail_still_clarifies_when_no_document_stands_out() -> None:
    ticket = SupportTicket(ticket_id="T-VAGUE-2", description="Nothing loads on the portal.")
    triage = TicketTriage(
        category=TicketCategory.OTHER,
        rationale="No symptom.",
        completeness=_facts(None, None),
    )
    # Close to many documents, none ahead of the rest.
    vague_matches = background_docs(start=0.30, step=0.005, count=5)
    with (
        patch("app.orchestration.coordinator.classify_ticket", return_value=triage),
        patch("app.orchestration.coordinator.run_tool_calling_turn") as tool_turn,
        patch("app.orchestration.coordinator.retrieve", return_value=vague_matches) as retrieve,
    ):
        trace = analyze_ticket_with_trace(ticket, client=MagicMock())

    retrieve.assert_called_once()
    tool_turn.assert_not_called()
    assert trace.response.outcome == AgentOutcome.CLARIFICATION_NEEDED
    assert trace.clarification_overridden is False


def test_missed_symptom_is_overridden_when_ticket_matches_one_document() -> None:
    """E-REL-004: the extractor sometimes returns no symptom for a plainly specific ticket."""
    ticket = SupportTicket(ticket_id="E-REL-004", description=E_REL_004)
    triage = TicketTriage(
        category=TicketCategory.NETWORK,
        rationale="DNS resolution failure.",
        completeness=_facts("internal hostnames", None),
    )
    with (
        patch("app.orchestration.coordinator.classify_ticket", return_value=triage),
        patch(
            "app.orchestration.coordinator.run_tool_calling_turn",
            return_value={"used_knowledge_base": False, "tool_calls": []},
        ) as tool_turn,
        patch("app.orchestration.coordinator.retrieve", return_value=_strong_match()),
    ):
        trace = analyze_ticket_with_trace(ticket, client=MagicMock())

    tool_turn.assert_called_once()
    assert trace.clarification_overridden is True
    assert trace.response.outcome != AgentOutcome.CLARIFICATION_NEEDED


def test_ticket_that_only_names_a_system_is_not_overridden() -> None:
    """R-AMB-003: 'VPN' alone matches the VPN document strongly, but no symptom is described."""
    ticket = SupportTicket(ticket_id="R-AMB-003", description="The VPN is acting weird.")
    triage = TicketTriage(
        category=TicketCategory.NETWORK,
        rationale="VPN, no symptom.",
        completeness=_facts("VPN", None),
    )
    with (
        patch("app.orchestration.coordinator.classify_ticket", return_value=triage),
        patch("app.orchestration.coordinator.run_tool_calling_turn") as tool_turn,
        patch("app.orchestration.coordinator.retrieve", return_value=_strong_match()) as retrieve,
    ):
        trace = analyze_ticket_with_trace(ticket, client=MagicMock())

    retrieve.assert_not_called()
    tool_turn.assert_not_called()
    assert trace.response.outcome == AgentOutcome.CLARIFICATION_NEEDED
    assert trace.clarification_missing == ["observable_symptom"]


def test_non_troubleshooting_ticket_skips_the_tool_call() -> None:
    ticket = SupportTicket(ticket_id="E-NRT", description="Thanks, that fixed it.")
    triage = TicketTriage(
        category=TicketCategory.OTHER,
        rationale="Acknowledgement, not a failure.",
        completeness=TicketCompleteness(is_troubleshooting_request=False),
    )
    with (
        patch("app.orchestration.coordinator.classify_ticket", return_value=triage),
        patch("app.orchestration.coordinator.run_tool_calling_turn") as tool_turn,
        patch("app.orchestration.coordinator.retrieve") as retrieve,
    ):
        trace = analyze_ticket_with_trace(ticket, client=MagicMock())

    tool_turn.assert_not_called()
    retrieve.assert_not_called()
    assert trace.response.outcome == AgentOutcome.FALLBACK
    assert trace.gemini_requested_retrieval is False
    assert trace.stage_ms["classify"] >= 0
    assert "tool" in trace.stage_ms
