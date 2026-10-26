"""Per-request counts of paid Gemini calls. Inactive outside start_usage()."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass
class Usage:
    gemini_calls: int = 0
    embed_api_calls: int = 0
    embed_cache_hits: int = 0


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


def note_embed_api_call() -> None:
    usage = _current.get()
    if usage is not None:
        usage.embed_api_calls += 1


def note_embed_cache_hit(count: int = 1) -> None:
    usage = _current.get()
    if usage is not None:
        usage.embed_cache_hits += count
