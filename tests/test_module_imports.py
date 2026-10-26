"""Each module must import on its own, in a fresh interpreter, whatever was imported first."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module",
    ["app.agents.triage", "app.agents.researcher", "app.agents.writer", "app.orchestration",
     "app.main", "evaluation.run_evaluation"],
)
def test_module_imports_alone(module: str) -> None:
    done = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr.strip().splitlines()[-1:]
