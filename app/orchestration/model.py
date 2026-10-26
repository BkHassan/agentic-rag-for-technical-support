"""Shared Gemini call helper. Agents and the coordinator use this one retry policy."""

from __future__ import annotations

import logging
import re
import time
from contextvars import ContextVar
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.usage import note_gemini_call, note_gemini_response, note_gemini_retry

logger = logging.getLogger(__name__)

# Monotonic deadline of the bounded stage this call runs in (set by the coordinator).
# A retry that cannot finish before it is not attempted: waiting would only turn a
# rate limit into a timeout and leave an abandoned worker making paid calls.
call_deadline: ContextVar[float | None] = ContextVar("call_deadline", default=None)

def _is_transient_gemini_error(exc: Exception) -> bool:
    message = (getattr(exc, "message", None) or str(exc)).lower()
    code = getattr(exc, "code", None)
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None
    return code_int in {429, 503, 500} or any(
        needle in message
        for needle in (
            "high demand",
            "try again later",
            "temporarily unavailable",
            "resource exhausted",
            "unavailable",
        )
    )


_RETRY_AFTER_RE = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def _gemini_error_message(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc)


MODEL_REQUEST_REJECTED = "model request rejected"


def describe_model_failure(exc: Exception) -> str:
    """
    Public description of a failed model call. A 4xx other than 429 means Gemini refused
    the request itself (for example an unsupported schema): that is a defect in this
    system, not an outage, and must not be reported as temporary unavailability.
    """
    code = getattr(exc, "code", None)
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None
    if isinstance(exc, genai_errors.ClientError) and code_int is not None and 400 <= code_int < 500 and code_int != 429:
        return f"{MODEL_REQUEST_REJECTED}: {type(exc).__name__} {code_int}"
    return f"model call failed: {type(exc).__name__}"


def is_daily_quota_error(message: str | None) -> bool:
    """True when Gemini rejected the call for the daily free-tier cap."""
    text = (message or "").lower()
    return "per day" in text or "daily" in text


def retry_delay_seconds(message: str, attempt: int, base_delay_sec: float) -> float:
    """Use Gemini's retry hint for per-minute limits, capped so a daily cap does not sleep for hours."""
    match = _RETRY_AFTER_RE.search(message or "")
    if match and not is_daily_quota_error(message):
        return min(float(match.group(1)) + 1.0, 90.0)
    return base_delay_sec * attempt


def _generate_content_with_retry(
    client: genai.Client,
    *,
    model: str,
    contents: Any,
    config: types.GenerateContentConfig,
    max_attempts: int = 5,
    base_delay_sec: float = 1.5,
) -> Any:
    """Call Gemini with retries for transient overload and per-minute rate limits."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        note_gemini_call()
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            note_gemini_response(response)
            return response
        except (genai_errors.ServerError, genai_errors.ClientError) as exc:
            last_exc = exc
            message = _gemini_error_message(exc)
            if is_daily_quota_error(message) or attempt >= max_attempts or not _is_transient_gemini_error(exc):
                raise
            delay = retry_delay_seconds(message, attempt, base_delay_sec)
            deadline = call_deadline.get()
            if deadline is not None and time.monotonic() + delay >= deadline:
                logger.warning(
                    "Not retrying Gemini error on attempt %s/%s: a %.1fs wait would pass the stage deadline",
                    attempt,
                    max_attempts,
                    delay,
                )
                raise
            logger.warning(
                "Transient Gemini error on attempt %s/%s (%s). Retrying in %.1fs",
                attempt,
                max_attempts,
                message,
                delay,
            )
            note_gemini_retry()
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
