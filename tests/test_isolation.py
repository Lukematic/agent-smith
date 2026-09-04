"""861e: two projects under one shared A.W.I.N.O. home must not see each other's
session state, ledger, intent, or mission. Proven with real project folders
and the real CLI, not by reading the code."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SMITH_ROOT = Path(__file__).resolve().parents[1]


def _cli(args: list[str], cwd: Path, stdin: str | None = None) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=cwd,
        input=stdin,
        env={**dict(os.environ), "PYTHONPATH": str(SMITH_ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    return completed.stdout + completed.stderr


def _project(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    (p / ".git").mkdir(parents=True)
    return p


def test_session_logs_do_not_cross_projects(tmp_path: Path) -> None:
    a, b = _project(tmp_path, "alpha"), _project(tmp_path, "beta")
    _cli(["hook", "prompt"], a, json.dumps({"prompt": "ALPHA-ONLY-SECRET-PHRASE"}))
    _cli(["hook", "prompt"], b, json.dumps({"prompt": "beta prompt"}))

    a_text = "".join(p.read_text(encoding="utf-8") for p in (a / ".smith").rglob("*.jsonl"))
    b_text = "".join(p.read_text(encoding="utf-8") for p in (b / ".smith").rglob("*.jsonl"))
    assert "ALPHA-ONLY-SECRET-PHRASE" in a_text
    assert "ALPHA-ONLY-SECRET-PHRASE" not in b_text
    home_state = SMITH_ROOT / "state"
    for p in home_state.rglob("*.jsonl"):
        assert "ALPHA-ONLY-SECRET-PHRASE" not in p.read_text(encoding="utf-8", errors="replace")


def test_intent_and_ledger_do_not_cross_projects(tmp_path: Path) -> None:
    a, b = _project(tmp_path, "alpha"), _project(tmp_path, "beta")
    _cli(["best", "pytest is failing with a ValueError in the loader"], a)
    _cli(["gate", "open", "question", "beta objective"], b)

    assert (a / ".smith" / "intent.json").is_file()
    assert not (b / ".smith" / "intent.json").exists()
    assert "CARRYING" not in _cli(["best"], b)
    assert "beta objective" not in _cli(["gate", "status"], a)


def test_mission_documents_are_per_project(tmp_path: Path) -> None:
    a, b = _project(tmp_path, "alpha"), _project(tmp_path, "beta")
    _cli(["mission", "--set", "objective=alpha mission text"], a)
    _cli(["mission", "--heilmeier"], b)
    assert "alpha mission text" in (a / ".smith" / "MISSION.md").read_text(encoding="utf-8")
    assert "alpha mission text" not in (b / ".smith" / "MISSION.md").read_text(encoding="utf-8")
