"""Per-request counts of paid Gemini calls. Inactive outside start_usage()."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass
class Usage:
    gemini_calls: int = 0
    # Attempts repeated after a transient error (a subset of gemini_calls).
    gemini_retries: int = 0
    embed_api_calls: int = 0
    embed_cache_hits: int = 0
    # Token counts come only from Gemini's usage_metadata. A response without it is
    # counted in gemini_responses but adds no tokens, so totals are never guessed.
    gemini_responses: int = 0
    gemini_responses_with_usage: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0


_current: ContextVar[Usage | None] = ContextVar("gemini_usage", default=None)


def start_usage() -> Token[Usage | None]:
    return _current.set(Usage())


def reset_usage(token: Token[Usage | None]) -> None:
    _current.reset(token)


def current_usage() -> Usage | None:
    return _current.get()


def note_gemini_call() -> None:
    usage = _current.get()
    if usage is not None:
        usage.gemini_calls += 1


def note_gemini_retry() -> None:
    usage = _current.get()
    if usage is not None:
        usage.gemini_retries += 1


def note_gemini_response(response: object) -> None:
    """Add the token counts Gemini reported for one successful response."""
    usage = _current.get()
    if usage is None:
        return
    usage.gemini_responses += 1
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return

    def count(name: str) -> int:
        value = getattr(metadata, name, None)
        return value if isinstance(value, int) and value >= 0 else 0

    usage.gemini_responses_with_usage += 1
    usage.prompt_tokens += count("prompt_token_count")
    usage.output_tokens += count("candidates_token_count")
    usage.thinking_tokens += count("thoughts_token_count")
    usage.total_tokens += count("total_token_count")


def note_embed_api_call() -> None:
    usage = _current.get()
    if usage is not None:
        usage.embed_api_calls += 1


def note_embed_cache_hit(count: int = 1) -> None:
    usage = _current.get()
    if usage is not None:
        usage.embed_cache_hits += count
