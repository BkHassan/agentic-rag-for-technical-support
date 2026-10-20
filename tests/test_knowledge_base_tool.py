"""Tests for the knowledge-base Gemini tool (no live API required for unit cases)."""

from __future__ import annotations

from unittest.mock import patch

from google.genai import types

from app.tools.knowledge_base import (
    SEARCH_TECHNICAL_DOCUMENTATION_DECLARATION,
    SEARCH_TECHNICAL_DOCUMENTATION_NAME,
    execute_tool_call,
    get_knowledge_base_tool,
    search_technical_documentation,
)


def test_tool_declaration_exposes_search_function() -> None:
    tool = get_knowledge_base_tool()
    assert isinstance(tool, types.Tool)
    assert tool.function_declarations
    decl = tool.function_declarations[0]
    assert decl.name == SEARCH_TECHNICAL_DOCUMENTATION_NAME
    assert SEARCH_TECHNICAL_DOCUMENTATION_DECLARATION["name"] == decl.name
    assert "query" in (SEARCH_TECHNICAL_DOCUMENTATION_DECLARATION["parameters"]["properties"])


def test_execute_tool_call_dispatches_to_search() -> None:
    fake_payload = {
        "query": "504 gateway timeout",
        "result_count": 1,
        "relevant_count": 1,
        "results": [{"source": "http_504_gateway_timeout.md", "distance": 0.2}],
    }
    with patch(
        "app.tools.knowledge_base.search_technical_documentation",
        return_value=fake_payload,
    ) as mocked:
        result = execute_tool_call(
            SEARCH_TECHNICAL_DOCUMENTATION_NAME,
            {"query": "504 gateway timeout", "top_k": 3},
        )

    mocked.assert_called_once_with(query="504 gateway timeout", top_k=3, cfg=None)
    assert result["result_count"] == 1
    assert result["results"][0]["source"] == "http_504_gateway_timeout.md"


def test_execute_unknown_tool_returns_error() -> None:
    result = execute_tool_call("not_a_real_tool", {"query": "x"})
    assert "error" in result
    assert result["results"] == []


def test_search_rejects_blank_query_without_chromadb() -> None:
    result = search_technical_documentation("   ")
    assert result["result_count"] == 0
    assert result["error"]


def test_search_returns_structured_results_from_retrieve() -> None:
    fake_hits = [
        {
            "text": "504 means gateway timeout.",
            "source": "http_504_gateway_timeout.md",
            "doc_id": "http_504_gateway_timeout",
            "chunk_index": 0,
            "category": "web",
            "distance": 0.21,
            "score": 0.79,
        }
    ]
    with (
        patch("app.tools.knowledge_base.retrieve", return_value=fake_hits),
        patch("app.tools.knowledge_base.filter_by_relevance", return_value=fake_hits),
    ):
        result = search_technical_documentation("Why 504 errors?")

    assert result["result_count"] == 1
    assert result["relevant_count"] == 1
    assert result["results"][0]["source"] == "http_504_gateway_timeout.md"
    assert result["results"][0]["text"] == "504 means gateway timeout."
    assert result["results"][0]["distance"] == 0.21
