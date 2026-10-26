"""Unit tests for the manual Gemini tool-calling loop in the agent."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agent import _extract_function_calls, run_tool_calling_turn
from app.schemas import SupportTicket
from app.tools.knowledge_base import SEARCH_TECHNICAL_DOCUMENTATION_NAME


def test_extract_function_calls_from_response_helper() -> None:
    call = SimpleNamespace(name="search_technical_documentation", args={"query": "dns"})
    response = SimpleNamespace(function_calls=[call], candidates=[])
    assert _extract_function_calls(response) == [call]


def test_run_tool_calling_turn_executes_requested_tool_in_python() -> None:
    """Gemini requests the tool; the application executes it and continues."""
    ticket = SupportTicket(
        ticket_id="T-1",
        description="Users receive HTTP 504 gateway timeouts.",
    )

    function_call = SimpleNamespace(
        name=SEARCH_TECHNICAL_DOCUMENTATION_NAME,
        args={"query": "HTTP 504 gateway timeout"},
    )
    model_content = SimpleNamespace(
        role="model",
        parts=[SimpleNamespace(function_call=function_call, text=None)],
    )
    first_response = SimpleNamespace(
        function_calls=[function_call],
        candidates=[SimpleNamespace(content=model_content)],
        text=None,
    )
    second_response = SimpleNamespace(
        function_calls=[],
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]))],
        text="Check upstream timeouts and proxy logs. Source: http_504_gateway_timeout.md",
    )

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [first_response, second_response]

    tool_result = {
        "query": "HTTP 504 gateway timeout",
        "result_count": 1,
        "relevant_count": 1,
        "results": [{"source": "http_504_gateway_timeout.md", "distance": 0.18}],
    }

    with (
        patch("app.orchestration.coordinator.get_genai_client", return_value=fake_client),
        patch("app.orchestration.coordinator.execute_tool_call", return_value=tool_result) as exec_tool,
    ):
        outcome = run_tool_calling_turn(ticket, client=fake_client)

    exec_tool.assert_called_once()
    assert exec_tool.call_args.args[0] == SEARCH_TECHNICAL_DOCUMENTATION_NAME
    assert outcome["used_knowledge_base"] is True
    assert outcome["tool_calls"][0]["name"] == SEARCH_TECHNICAL_DOCUMENTATION_NAME
    assert "504" in outcome["response_text"] or "timeout" in outcome["response_text"].lower()
    assert fake_client.models.generate_content.call_count == 2


def test_finish_after_tool_does_not_spend_a_follow_up_call() -> None:
    """The host writes the answer, so the tool result is not sent back to Gemini."""
    ticket = SupportTicket(ticket_id="T-1", description="Users receive HTTP 504 gateway timeouts.")
    function_call = SimpleNamespace(
        name=SEARCH_TECHNICAL_DOCUMENTATION_NAME,
        args={"query": "HTTP 504 gateway timeout"},
    )
    model_content = SimpleNamespace(
        role="model",
        parts=[SimpleNamespace(function_call=function_call, text=None)],
    )
    first_response = SimpleNamespace(
        function_calls=[function_call],
        candidates=[SimpleNamespace(content=model_content)],
        text=None,
    )
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = first_response
    tool_result = {"query": "HTTP 504", "result_count": 1, "relevant_count": 1, "results": []}

    with (
        patch("app.orchestration.coordinator.get_genai_client", return_value=fake_client),
        patch("app.orchestration.coordinator.execute_tool_call", return_value=tool_result),
    ):
        outcome = run_tool_calling_turn(ticket, client=fake_client, finish_after_tool=True, max_tool_rounds=1)

    assert fake_client.models.generate_content.call_count == 1
    assert outcome["used_knowledge_base"] is True
    assert outcome["response_text"] == ""
