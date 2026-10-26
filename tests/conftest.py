"""Keep live API tests out of the default offline run.

Tests marked `live` call Gemini or embed against ChromaDB. They run only when
RUN_LIVE_TESTS=1 and GOOGLE_API_KEY are both set, so an exported key alone never
turns the offline suite into a billed, non-deterministic run.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: calls the real Gemini API (opt in with RUN_LIVE_TESTS=1)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        reason = "live test: set RUN_LIVE_TESTS=1 and GOOGLE_API_KEY to run"
    elif not os.getenv("GOOGLE_API_KEY"):
        reason = "live test: GOOGLE_API_KEY is not set"
    else:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
