"""Integration: one real floor trip through the actual CLI, end to end.

This is the test Phase 0 proved was missing: everything else fakes the
worker. Here the full boundary is real - `python -m smith.cli floor open`
writes a real prompt into a real temp project, a real subprocess plays the
worker (any environment can: that is the portability contract), and
`floor close` re-runs the real verification command before routing.

Marked integration: slower than unit tests, but with no external dependency -
the worker is plain python, exactly because floors are harness-agnostic.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SMITH_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=cwd,
        env={**dict(os.environ), "PYTHONPATH": str(SMITH_ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def test_a_full_floor_trip_completes_against_a_real_project(tmp_path: Path) -> None:
    project = tmp_path / "field"
    (project / ".git").mkdir(parents=True)
    marker = project / "notes.txt"
    check = project / "check.py"
    check.write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path('notes.txt')\n"
        "sys.exit(0 if p.exists() and 'FIELD-OK' in p.read_text() else 1)\n",
        encoding="utf-8",
    )

    opened = _cli(
        [
            "gate",
            "open",
            "question",
            "integration floor trip",
        ],
        cwd=project,
    )
    assert opened.returncode == 0, opened.stdout + opened.stderr

    floor = _cli(
        [
            "floor",
            "open",
            "pytest is failing because notes.txt is missing the line FIELD-OK; fix it",
            "--verify",
            f'"{sys.executable}" check.py',
            "--scope",
            "notes.txt",
        ],
        cwd=project,
    )
    assert floor.returncode == 0, floor.stdout + floor.stderr
    assert "FLOOR_OPEN" in floor.stdout
    prompt_path = next(
        line.split("PROMPT", 1)[1].strip()
        for line in floor.stdout.splitlines()
        if line.startswith("PROMPT")
    )
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    # D1: the routed skill's procedure rides in the prompt.
    assert "The skill you were routed to" in prompt

    # The "worker": any process that can read the prompt and do the work.
    marker.write_text("FIELD-OK\n", encoding="utf-8")

    closed = _cli(["floor", "close"], cwd=project)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert "COMPLETE" in closed.stdout


def test_a_floor_close_with_failing_verification_routes_to_revise(tmp_path: Path) -> None:
    project = tmp_path / "field2"
    (project / ".git").mkdir(parents=True)
    (project / "check.py").write_text("import sys; sys.exit(1)\n", encoding="utf-8")

    assert _cli(["gate", "open", "question", "revise trip"], cwd=project).returncode == 0
    floor = _cli(
        [
            "floor",
            "open",
            "pytest is failing with an error in the checker; fix it",
            "--verify",
            f'"{sys.executable}" check.py',
            "--scope",
            "whatever.txt",
        ],
        cwd=project,
    )
    assert floor.returncode == 0, floor.stdout + floor.stderr

    closed = _cli(["floor", "close"], cwd=project)
    assert closed.returncode == 1
    assert "REVISE" in closed.stdout
    assert "FLOOR_OPEN  floor=2" in closed.stdout
