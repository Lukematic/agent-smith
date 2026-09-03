"""`awino best` through the real CLI: plain form runs the session order with
skill labels; request form is the elevator - route, stance, recall, next
command - and spawns nothing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SMITH_ROOT = Path(__file__).resolve().parents[1]


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


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".git").mkdir(parents=True)
    return p


def test_plain_best_labels_each_step_with_its_skill(tmp_path: Path) -> None:
    result = _cli(["best"], cwd=_project(tmp_path))
    assert "[mission-gap] skill=awino-discover" in result.stdout
    assert "[next-seed] skill=direct" in result.stdout


def test_best_with_a_request_routes_and_names_the_next_command(tmp_path: Path) -> None:
    result = _cli(
        ["best", "pytest is failing with a ValueError in the loader"], cwd=_project(tmp_path)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FLOOR  awino-debug" in result.stdout
    assert "NEXT  awino gate open bugfix" in result.stdout


def test_best_with_a_request_spawns_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _cli(["best", "pytest is failing with a ValueError in the loader"], cwd=project)
    assert not list(project.rglob("dispatch-f*"))
