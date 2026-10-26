"""Diagnostics: stage and request events, error categories, request isolation, and redaction.

The real Gemini call helper runs against a fake client, so model-call, retry, and token
counts come from the same code path production uses. No network is used.
"""

from __future__ import annotations

import json
import logging
import shlex
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

import app.orchestration.model as model_module
from app import observability as obs
from app.config import Settings
from app.main import app
from app.orchestration.coordinator import analyze_ticket_with_trace
from app.schemas import AgentOutcome, AgentResponse, SupportTicket, TicketCategory
from app.tools.knowledge_base import SEARCH_TECHNICAL_DOCUMENTATION_NAME
from tests.test_pipeline_integration import CLEAR, DB_PASSAGE, DB_SOURCE, FakeRetriever, _background, _standout

SECRET_KEY = "AIzaSyD-test-key-0123456789abcdefghijklmn"


def _usage(prompt: int, output: int) -> SimpleNamespace:
    return SimpleNamespace(prompt_token_count=prompt, candidates_token_count=output,
                           thoughts_token_count=0, total_token_count=prompt + output)


def _json_reply(payload: dict, usage: SimpleNamespace | None) -> SimpleNamespace:
    return SimpleNamespace(parsed=payload, text=json.dumps(payload), function_calls=[], usage_metadata=usage)


TRIAGE = {"category": "database", "rationale": "Database timeout.",
          "completeness": {"is_troubleshooting_request": True, "affected_system": "database connection",
                           "observable_symptom": "keeps timing out"}}
WRITER = {"can_answer": True, "summary": "The passage documents a pool check.",
          "recommendations": [{"text": DB_PASSAGE, "source": DB_SOURCE, "quote": DB_PASSAGE}]}


class FakeGemini:
    """Stands in for genai.Client. Routes by the request config, like the three agents do."""

    def __init__(self, *, errors: dict[str, list[Exception]] | None = None, with_usage: bool = True,
                 writer: dict | None = None) -> None:
        self.errors = {key: list(value) for key, value in (errors or {}).items()}
        self.with_usage = with_usage
        self.writer = writer or WRITER
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, *, model: str, contents: Any, config: Any) -> SimpleNamespace:
        stage = "research" if config.tools else ("triage" if config.response_schema.__name__ == "TicketTriage" else "writer")
        logging.getLogger("tests.fake_gemini").info("fake call stage=%s", stage)
        pending = self.errors.get(stage)
        if pending:
            raise pending.pop(0)
        usage = _usage(100, 10) if self.with_usage else None
        if stage == "triage":
            return _json_reply(TRIAGE, usage)
        if stage == "writer":
            return _json_reply(self.writer, usage)
        call = SimpleNamespace(name=SEARCH_TECHNICAL_DOCUMENTATION_NAME, args={"query": "database timeout"}, id="c1")
        return SimpleNamespace(function_calls=[call], text="", candidates=[], usage_metadata=usage)


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_module.time, "sleep", lambda _seconds: None)


def _events(caplog: pytest.LogCaptureFixture, event: str | None = None) -> list[dict[str, str]]:
    parsed = []
    for record in caplog.records:
        if record.name != "app.events":
            continue
        fields = dict(part.split("=", 1) for part in shlex.split(record.getMessage()))
        if event is None or fields["event"] == event:
            parsed.append(fields)
    return parsed


def _analyze(gemini: FakeGemini, hits: list[dict] | None = None, description: str = CLEAR):
    return analyze_ticket_with_trace(
        SupportTicket(ticket_id="T-OBS-1", description=description),
        client=gemini,
        cfg=Settings(google_api_key="test-key"),
        retrieve_documents=FakeRetriever(_standout() if hits is None else hits),
    )


# --- Key events ------------------------------------------------------------------------


def test_grounded_request_logs_each_stage_with_measured_counts(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        trace = _analyze(FakeGemini())

    assert trace.response.outcome is AgentOutcome.GROUNDED_ANSWER
    stages = _events(caplog, "stage")
    assert [event["stage"] for event in stages] == ["triage", "route", "research", "evidence_gate", "writer"]
    assert len({event["request_id"] for event in _events(caplog)}) == 1
    assert all(float(event["duration_ms"]) >= 0 and event["status"] == "ok" for event in stages)
    by_stage = {event["stage"]: event for event in stages}
    assert by_stage["triage"]["model_calls"] == "1" and by_stage["triage"]["input_tokens"] == "100"
    assert by_stage["research"]["tool_executions"] == "1" and by_stage["research"]["retrieval_count"] == "5"
    assert by_stage["evidence_gate"]["gate_decision"] == "accepted"
    assert by_stage["evidence_gate"]["reason_code"] == "accepted"
    assert by_stage["writer"]["steps"] == "1"
    (request,) = _events(caplog, "request")
    assert request["status"] == "completed" and request["outcome"] == "grounded_answer"
    assert (request["model_calls"], request["retries"], request["tool_executions"]) == ("3", "0", "1")
    assert (request["input_tokens"], request["output_tokens"], request["total_tokens"]) == ("300", "30", "330")


def test_events_never_contain_ticket_or_document_text(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        _analyze(FakeGemini())

    events = "\n".join(record.getMessage() for record in caplog.records if record.name == "app.events")
    assert CLEAR not in events
    assert DB_PASSAGE not in events
    assert "keeps timing out" not in events


def test_rejected_evidence_logs_a_reason_code_and_no_writer_stage(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        trace = _analyze(FakeGemini(), hits=_background(count=5, start=0.45))

    assert trace.response.outcome is AgentOutcome.FALLBACK
    stages = {event["stage"]: event for event in _events(caplog, "stage")}
    assert "writer" not in stages
    assert stages["evidence_gate"]["gate_decision"] == "rejected"
    assert stages["evidence_gate"]["reason_code"] == "distance_above_threshold"
    assert _events(caplog, "request")[0]["outcome"] == "fallback"


def test_writer_decline_logs_why_there_is_no_draft(caplog: pytest.LogCaptureFixture) -> None:
    declined = {"can_answer": False, "recommendations": [], "summary": "The passages do not cover this."}
    with caplog.at_level(logging.INFO):
        trace = _analyze(FakeGemini(writer=declined))

    assert trace.response.outcome is AgentOutcome.FALLBACK
    writer = _events(caplog, "stage")[-1]
    assert (writer["stage"], writer["draft_status"], writer["writer_failure"]) == (
        "writer", "missing", "insufficient_evidence")


def test_missing_usage_metadata_is_reported_not_filled_in(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        _analyze(FakeGemini(with_usage=False))

    (request,) = _events(caplog, "request")
    assert request["tokens"] == "not_reported"
    assert "input_tokens" not in request and "total_tokens" not in request


# --- Failure paths ---------------------------------------------------------------------


def test_transient_error_is_counted_as_a_retry(caplog: pytest.LogCaptureFixture) -> None:
    overloaded = genai_errors.ServerError(503, {"error": {"message": "The model is overloaded."}})
    with caplog.at_level(logging.INFO):
        _analyze(FakeGemini(errors={"triage": [overloaded]}))

    triage = _events(caplog, "stage")[0]
    assert (triage["model_calls"], triage["retries"]) == ("2", "1")
    assert _events(caplog, "request")[0]["retries"] == "1"


def test_rejected_model_request_is_categorized_as_a_defect(caplog: pytest.LogCaptureFixture) -> None:
    rejected = genai_errors.ClientError(400, {"error": {"message": "Invalid JSON payload received."}})
    with caplog.at_level(logging.INFO):
        trace = _analyze(FakeGemini(errors={"writer": [rejected]}))

    assert trace.error is not None
    writer = _events(caplog, "stage")[-1]
    assert (writer["stage"], writer["status"], writer["error_category"]) == ("writer", "failed", "model_request_rejected")
    (request,) = _events(caplog, "request")
    assert (request["status"], request["error_category"]) == ("failed", "model_request_rejected")
    assert "outcome" not in request


def test_rate_limit_that_escapes_is_logged_before_it_is_raised(caplog: pytest.LogCaptureFixture) -> None:
    quota = genai_errors.ClientError(429, {"error": {"message": "Quota exceeded: requests per day."}})
    with caplog.at_level(logging.INFO), pytest.raises(genai_errors.ClientError):
        _analyze(FakeGemini(errors={"triage": [quota]}))

    (triage,) = _events(caplog, "stage")
    assert (triage["status"], triage["error_category"]) == ("error", "rate_limited")
    assert _events(caplog, "request")[0]["error_category"] == "rate_limited"


# --- Isolation -------------------------------------------------------------------------


def test_concurrent_requests_keep_separate_ids_in_worker_thread_logs(caplog: pytest.LogCaptureFixture) -> None:
    barrier = threading.Barrier(3, timeout=10)
    ids: dict[int, str] = {}

    def one(index: int) -> None:
        with obs.request_context() as request_id:
            ids[index] = request_id
            barrier.wait()
            _analyze(FakeGemini())

    with caplog.at_level(logging.INFO):
        threads = [threading.Thread(target=one, args=(index,)) for index in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert len(set(ids.values())) == 3
    for request_id in ids.values():
        mine = [record for record in caplog.records if getattr(record, "request_id", None) == request_id]
        # Each request's own fake model calls ran in coordinator worker threads.
        assert sum(record.name == "tests.fake_gemini" for record in mine) == 3
        assert sum(record.name == "app.events" and "event=request " in record.getMessage() for record in mine) == 1


def test_http_response_carries_a_request_id_and_only_a_fixed_error_message() -> None:
    client = TestClient(app)
    ticket = {"ticket_id": "T-1", "description": "Database connection keeps timing out."}
    ok = AgentResponse(ticket_id="T-1", category=TicketCategory.DATABASE, outcome=AgentOutcome.FALLBACK,
                       recommendations=["Escalate."], sources=[], sufficient_evidence=False, fallback_triggered=True)
    with patch("app.main.handle_support_ticket", return_value=ok):
        generated = client.post("/tickets/analyze", json=ticket)
        echoed = client.post("/tickets/analyze", json=ticket, headers={"X-Request-ID": "support-123"})
        replaced = client.post("/tickets/analyze", json=ticket, headers={"X-Request-ID": "bad id\nwith=junk"})
    with patch("app.main.handle_support_ticket", side_effect=RuntimeError(f"key={SECRET_KEY}")):
        failed = client.post("/tickets/analyze", json=ticket)

    assert len(generated.headers["X-Request-ID"]) == 32
    assert echoed.headers["X-Request-ID"] == "support-123"
    assert replaced.headers["X-Request-ID"] != "bad id\nwith=junk" and len(replaced.headers["X-Request-ID"]) == 32
    assert failed.status_code == 503 and failed.json() == {"detail": "The analysis could not be completed."}
    assert failed.headers["X-Request-ID"]


# --- Redaction -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "leaked"),
    [
        (f"POST https://generativelanguage.googleapis.com/v1/models?key={SECRET_KEY}", SECRET_KEY),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ("x-goog-api-key: plain-secret-value", "plain-secret-value"),
        ("config GOOGLE_API_KEY=hunter2hunter2 loaded", "hunter2hunter2"),
        ('{"api_key": "abc123secretvalue"}', "abc123secretvalue"),
        ("password=correct-horse-battery", "correct-horse-battery"),
    ],
)
def test_secrets_are_redacted_in_messages(caplog: pytest.LogCaptureFixture, message: str, leaked: str) -> None:
    with caplog.at_level(logging.INFO):
        logging.getLogger("app.anything").info("upstream said: %s", message)

    assert leaked not in caplog.text
    assert obs.REDACTED in caplog.text
    assert "upstream said:" in caplog.text


def test_configured_key_and_tracebacks_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    obs.register_secret("configured-key-value-42")
    with caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError("client built with configured-key-value-42")
        except RuntimeError:
            logging.getLogger("app.anything").exception("call failed")

    assert "configured-key-value-42" not in caplog.text
    assert "RuntimeError: client built with [REDACTED]" in caplog.text


def test_plain_diagnostics_are_not_redacted(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        logging.getLogger("app.anything").info("ticket_id=%s stage=%s status=%s", "T-9", "research", "ok")

    assert "ticket_id=T-9 stage=research status=ok" in caplog.text


@pytest.mark.parametrize("path", ["/tickets/analyze", f"/tickets/analyze?key={SECRET_KEY}"])
def test_uvicorn_access_log_still_formats_after_redaction(path: str) -> None:
    """Seen live: collapsing record.args broke uvicorn's access formatter on every request."""
    from uvicorn.logging import AccessFormatter

    record = logging.getLogger("uvicorn.access").makeRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "POST", path, "1.1", 200), None,
    )
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)

    assert "POST /tickets/analyze" in line and "200" in line
    assert SECRET_KEY not in line
    assert isinstance(record.args, tuple)


def test_retry_that_cannot_finish_before_the_stage_deadline_is_not_attempted(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-minute 429 asking for 80 s inside a 65 s research bound fails now, as a rate limit."""
    slept: list[float] = []
    monkeypatch.setattr(model_module.time, "sleep", slept.append)
    limited = genai_errors.ClientError(429, {"error": {"message": "Resource exhausted. Please retry in 80s."}})

    with caplog.at_level(logging.INFO):
        trace = _analyze(FakeGemini(errors={"research": [limited]}))

    assert slept == []
    assert trace.error == "The language model is temporarily unavailable."
    research = [event for event in _events(caplog, "stage") if event["stage"] == "research"][0]
    assert (research["status"], research["error_category"]) == ("failed", "rate_limited")
    assert (research["model_calls"], research["retries"]) == ("1", "0")


def test_user_supplied_values_cannot_forge_log_lines(caplog: pytest.LogCaptureFixture) -> None:
    forged = "T-1\nINFO app.events event=request status=completed outcome=grounded_answer"
    with caplog.at_level(logging.INFO):
        logging.getLogger("app.main").warning("Bad request for ticket_id=%s: %s", forged, "invalid")

    (record,) = [r for r in caplog.records if r.name == "app.main"]
    assert "\n" not in record.getMessage()
    assert "ticket_id=T-1\\nINFO app.events" in record.getMessage()
