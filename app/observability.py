"""Request-scoped, privacy-conscious diagnostics on top of the standard logging module.

- A request ID lives in a ContextVar, so concurrent requests never share it. The
  coordinator runs agents in worker threads with a copied context, so their log
  records carry the same ID.
- `log_event` writes one `key=value` line per event on the `app.events` logger. Callers
  pass counts, codes, and durations only: never ticket text, passages, or prompts.
- Every log record in the process is redacted when it is created: API keys, bearer
  tokens, and secret-looking `name=value` pairs are replaced, including in tracebacks.
"""

from __future__ import annotations

import logging
import re
import threading
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

EVENT_LOGGER = logging.getLogger("app.events")
REDACTED = "[REDACTED]"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Error categories for server-side diagnostics. Client bodies keep their own fixed messages.
TIMEOUT = "timeout"
RATE_LIMITED = "rate_limited"
MODEL_UNAVAILABLE = "model_unavailable"
MODEL_REQUEST_REJECTED = "model_request_rejected"
INVALID_AGENT_OUTPUT = "invalid_agent_output"
RETRIEVAL_FAILED = "retrieval_failed"
WORKFLOW_ERROR = "workflow_error"
UPSTREAM_CLIENT_ERROR = "upstream_client_error"
UPSTREAM_SERVER_ERROR = "upstream_server_error"
CONFIGURATION_OR_INPUT = "configuration_or_input"
INTERNAL_ERROR = "internal_error"


# --- Request ID ---------------------------------------------------------------------


def current_request_id() -> str | None:
    return _request_id.get()


def new_request_id(candidate: str | None = None) -> str:
    """Accept a caller-supplied ID only if it is short and plain; otherwise make one."""
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Bind a request ID for this context. Reuses the active one when none is given."""
    existing = _request_id.get()
    if request_id is None and existing is not None:
        yield existing
        return
    token = _request_id.set(new_request_id(request_id))
    try:
        yield _request_id.get() or ""
    finally:
        _request_id.reset(token)


# --- Events -------------------------------------------------------------------------


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.1f}"
    text = str(value)
    if not text or re.search(r"[\s\"=]", text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """One structured line. Fields that are None are left out rather than guessed."""
    parts = [f"event={event}", f"request_id={_format_value(current_request_id() or '-')}"]
    parts += [f"{key}={_format_value(value)}" for key, value in fields.items() if value is not None]
    EVENT_LOGGER.log(level, " ".join(parts))


def error_category(exc: BaseException) -> str:
    """Category for an exception that escaped the coordinator."""
    name = type(exc).__name__
    code = getattr(exc, "code", None)
    if name in {"CoordinatorTimeout", "TimeoutError"}:
        return TIMEOUT
    if name == "ClientError":
        try:
            return RATE_LIMITED if int(code) == 429 else UPSTREAM_CLIENT_ERROR
        except (TypeError, ValueError):
            return UPSTREAM_CLIENT_ERROR
    if name == "ServerError":
        return UPSTREAM_SERVER_ERROR
    if isinstance(exc, ValueError):
        return CONFIGURATION_OR_INPUT
    return INTERNAL_ERROR


# --- Redaction ----------------------------------------------------------------------

_SECRET_VALUES: set[str] = set()
_SECRET_LOCK = threading.Lock()
_PATTERNS = (
    # Google API keys.
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), REDACTED),
    # Bearer tokens in any header-like text.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]+"), f"Bearer {REDACTED}"),
    # key=..., api_key: ..., x-goog-api-key: ..., Authorization: ..., token/secret/password=...
    (
        re.compile(
            r"(?i)\b((?:[a-z_]*api[_-]?key|x-goog-api-key|authorization|access[_-]?token|"
            r"refresh[_-]?token|secret|password|passwd|key)\b[\"']?\s*[:=]\s*[\"']?)"
            r"(?!\[REDACTED\])[^\s\"'&,;}]+"
        ),
        rf"\1{REDACTED}",
    ),
)


def register_secret(value: str | None) -> None:
    """Redact this exact value wherever it appears. Short values are ignored."""
    if value and len(value) >= 8:
        with _SECRET_LOCK:
            _SECRET_VALUES.add(value)


def redact(text: str) -> str:
    with _SECRET_LOCK:
        secrets = sorted(_SECRET_VALUES, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _message(record: logging.LogRecord) -> str:
    try:
        return record.getMessage()
    except Exception:  # noqa: BLE001 - a malformed log call must not break the request
        return str(record.msg)


def _clean(value: str) -> str:
    # Escaping line breaks stops a user-supplied value (a ticket ID) from forging log lines.
    return redact(value).replace("\r", "\\r").replace("\n", "\\n")


def _string_args(args: Any) -> list[str]:
    if isinstance(args, tuple):
        return [item for item in args if isinstance(item, str)]
    if isinstance(args, dict):
        return [value for value in args.values() if isinstance(value, str)]
    return []


def _redact_args(args: Any) -> Any:
    if isinstance(args, tuple):
        return tuple(_clean(item) if isinstance(item, str) else item for item in args)
    if isinstance(args, dict):
        return {key: _clean(value) if isinstance(value, str) else value for key, value in args.items()}
    return args


def _redact_record(record: logging.LogRecord) -> None:
    """
    Redact in place while keeping `msg` and `args` usable: some formatters (uvicorn's
    access log) unpack `record.args`. Only when a secret sits inside a non-string
    argument is the record collapsed into one pre-formatted, redacted message.
    """
    message = _message(record)
    has_line_breaks = any("\n" in item or "\r" in item for item in _string_args(record.args))
    if redact(message) == message and not has_line_breaks:
        return
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    record.args = _redact_args(record.args)
    message_now = _message(record)
    if redact(message_now) != message_now:
        record.msg, record.args = redact(message_now), None


def _install_record_factory() -> None:
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_app_redacting", False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        record.request_id = _request_id.get() or "-"
        _redact_record(record)
        if record.exc_info and record.exc_info[0] is not None:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip())
        return record

    factory._app_redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


_install_record_factory()


def _register_configured_secrets() -> None:
    from app.config import settings

    register_secret(settings.google_api_key)


_register_configured_secrets()
