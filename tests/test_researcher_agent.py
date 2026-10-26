"""Researcher tests. The model and the retriever are both injected."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.agents.researcher import (
    MAX_QUERY_LENGTH,
    RESEARCH_SYSTEM_INSTRUCTION,
    ResearchRun,
    research_ticket,
    search_tool,
)
from app.agents.state import ClarificationRecord, StageStatus
from app.agents.triage import TriageAssessment
from app.config import settings
from app.schemas import SupportTicket, TicketCategory, TicketCompleteness, TicketTriage
from app.tools.knowledge_base import SEARCH_TECHNICAL_DOCUMENTATION_NAME

CLEAR = "Users cannot connect to the application because the database connection keeps timing out."
HIT = {
    "text": "Check the pool size and the database timeout settings.",
    "source": "database_connection_failures.md",
    "doc_id": "database_connection_failures",
    "chunk_index": 0,
    "category": "database",
    "distance": 0.21,
}


def _triage(*, troubleshooting: bool = True, needed: bool = False) -> TriageAssessment:
    return TriageAssessment(
        classification=TicketTriage(
            category=TicketCategory.DATABASE if troubleshooting else TicketCategory.OTHER,
            rationale="Database timeout." if troubleshooting else "Not a failure.",
            completeness=TicketCompleteness(
                is_troubleshooting_request=troubleshooting,
                affected_system="database connection" if troubleshooting else None,
                observable_symptom="keeps timing out" if troubleshooting else None,
            ),
        ),
        clarification=ClarificationRecord(
            needed=needed,
            questions=["What exactly happens?"] if needed else [],
            reason="A symptom is missing." if needed else "Ticket describes an observable symptom.",
        ),
        missing=["observable_symptom"] if needed else [],
    )


def _call(query: object = "database connection timeout", *, name: str = SEARCH_TECHNICAL_DOCUMENTATION_NAME, args: dict | None = None) -> SimpleNamespace:
    payload = {"query": query} if args is None else args
    return SimpleNamespace(name=name, args=payload, id="call-1")


def _response(*calls: SimpleNamespace, text: str = "See invented.md for the fix.") -> SimpleNamespace:
    return SimpleNamespace(function_calls=list(calls), text=text, candidates=[])


def _run(
    response: SimpleNamespace | None = None,
    *,
    triage: TriageAssessment | None = None,
    hits: list[dict] | None = None,
    retrieve_error: Exception | None = None,
    complete_error: Exception | None = None,
) -> tuple[ResearchRun, list[str], int]:
    queries: list[str] = []

    def retrieve_documents(query: str) -> list[dict]:
        queries.append(query)
        if retrieve_error is not None:
            raise retrieve_error
        return list(hits if hits is not None else [HIT])

    model_calls = 0

    def complete(_contents: object, _config: object) -> SimpleNamespace:
        nonlocal model_calls
        model_calls += 1
        if complete_error is not None:
            raise complete_error
        return response if response is not None else _response(_call())

    result = research_ticket(
        SupportTicket(ticket_id="T-1", description=CLEAR),
        triage or _triage(),
        complete=complete,
        retrieve_documents=retrieve_documents,
    )
    return result, queries, model_calls


def test_triage_that_does_not_need_search_does_not_call_the_model_or_retriever() -> None:
    for triage in (_triage(troubleshooting=False), _triage(needed=True)):
        result, queries, model_calls = _run(triage=triage)
        assert model_calls == 0
        assert queries == []
        assert result.model_calls == 0
        assert result.tool_executions == 0
        assert result.research.status is StageStatus.REJECTED
        assert result.research.value is not None
        assert result.research.value.requested_search is False
        assert result.retrieval.status is StageStatus.MISSING


def test_model_can_decline_the_tool() -> None:
    result, queries, model_calls = _run(_response())
    assert model_calls == 1
    assert result.model_calls == 1
    assert result.tool_executions == 0
    assert queries == []
    assert result.research.status is StageStatus.REJECTED
    assert result.retrieval.status is StageStatus.MISSING


def test_valid_search_uses_the_retriever_and_ignores_invented_citations() -> None:
    result, queries, model_calls = _run(_response(_call("database connection timeout")))

    assert model_calls == 1
    assert result.model_calls == 1
    assert result.tool_executions == 1
    assert queries == ["database connection timeout"]
    assert result.research.status is StageStatus.PRODUCED
    assert result.research.value is not None
    assert result.research.value.query == "database connection timeout"
    assert result.retrieval.status is StageStatus.PRODUCED
    assert result.retrieval.value is not None
    passage = result.retrieval.value.passages[0]
    assert passage.source == HIT["source"]
    assert passage.doc_id == HIT["doc_id"]
    assert passage.chunk_index == HIT["chunk_index"]
    assert passage.distance == HIT["distance"]
    assert passage.excerpt == HIT["text"]
    assert "invented.md" not in passage.source


def test_repeated_tool_calls_execute_the_search_once() -> None:
    result, queries, model_calls = _run(
        _response(
            _call("database connection timeout"),
            _call("database connection timeout"),
            _call("different query that must not run"),
        )
    )

    assert model_calls == 1
    assert result.model_calls == 1
    assert result.tool_executions == 1
    assert result.duplicate_calls == 1
    assert result.rejected_calls == 1
    assert queries == ["database connection timeout"]


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (_call(args={"query": "   "}), "query:"),
        (_call(args={"query": "q" * (MAX_QUERY_LENGTH + 1)}), "query:"),
        (_call(args={}), "query: missing"),
        (_call(args={"query": "database timeout", "top_k": 100}), "top_k: extra_forbidden"),
        (_call(args={"query": "database timeout", "source": "invented.md"}), "source: extra_forbidden"),
        (_call(name="code_execution", args={"query": "database timeout"}), "unknown tool"),
        (_call(args="database timeout"), "malformed"),  # type: ignore[arg-type]
    ],
)
def test_malformed_tool_requests_are_not_executed(call: SimpleNamespace, match: str) -> None:
    result, queries, model_calls = _run(_response(call))

    assert model_calls == 1
    assert result.model_calls == 1
    assert result.tool_executions == 0
    assert queries == []
    assert result.research.status is StageStatus.INVALID
    assert result.research.value is None
    assert result.retrieval.status is StageStatus.MISSING
    assert match in (result.error or "")


def test_empty_retrieval_is_not_treated_as_an_error_or_as_sufficient() -> None:
    result, queries, model_calls = _run(_response(_call()), hits=[])

    assert model_calls == 1
    assert result.model_calls == 1
    assert result.tool_executions == 1
    assert queries == ["database connection timeout"]
    assert result.retrieval.status is StageStatus.PRODUCED
    assert result.retrieval.value is not None
    assert result.retrieval.value.passages == []
    assert result.error is None
    assert not hasattr(result, "sufficient_evidence")


def test_retrieval_errors_are_explicit_and_keep_no_passages() -> None:
    result, queries, model_calls = _run(
        _response(_call()),
        retrieve_error=RuntimeError("chroma unavailable GOOGLE_API_KEY=secret"),
    )

    assert model_calls == 1
    assert result.model_calls == 1
    assert result.tool_executions == 1
    assert queries == ["database connection timeout"]
    assert result.retrieval.status is StageStatus.INVALID
    assert result.retrieval.value is None
    assert result.error == "retrieval failed: RuntimeError"
    assert "GOOGLE_API_KEY" not in (result.error or "")


def test_model_rate_limit_does_not_retrieve() -> None:
    class RateLimit(Exception):
        code = 429

    result, queries, model_calls = _run(complete_error=RateLimit("resource exhausted"))

    assert model_calls == 1
    assert result.model_calls == 1
    assert result.tool_executions == 0
    assert queries == []
    assert result.retrieval.status is StageStatus.MISSING
    assert result.error == "model rate limit"


def test_tool_schema_exposes_only_the_documentation_search() -> None:
    tool = search_tool()
    declaration = tool.function_declarations[0]
    assert declaration.name == SEARCH_TECHNICAL_DOCUMENTATION_NAME
    assert set(declaration.parameters_json_schema["properties"]) == {"query"}
    assert declaration.parameters_json_schema["additionalProperties"] is False
    assert declaration.parameters_json_schema["required"] == ["query"]
    assert tool.code_execution is None
    assert tool.computer_use is None
    assert CLEAR not in RESEARCH_SYSTEM_INSTRUCTION


def test_default_retriever_uses_the_existing_chroma_retrieval() -> None:
    client = MagicMock()
    client.models.generate_content.return_value = _response(_call("database connection timeout"))
    with (
        patch("app.agents.researcher.get_genai_client", side_effect=AssertionError("second client")),
        patch("app.retrieval.retrieval.retrieve", return_value=[HIT]) as retrieve,
        patch("app.policies.evidence.validate_retrieval_evidence", side_effect=AssertionError("evidence gate")) as gate,
    ):
        result = research_ticket(CLEAR, _triage(), client=client, cfg=_settings())

    assert result.model_calls == 1
    assert result.tool_executions == 1
    assert client.models.generate_content.call_count == 1
    retrieve.assert_called_once()
    assert retrieve.call_args.args[0] == "database connection timeout"
    assert retrieve.call_args.kwargs["top_k"] == settings.retrieval_top_k
    gate.assert_not_called()
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.automatic_function_calling.disable is True
    assert config.temperature == 0
    assert CLEAR not in config.system_instruction


def _settings():
    from app.config import Settings

    return Settings(google_api_key="test-key", llm_model="gemini-research-test")
