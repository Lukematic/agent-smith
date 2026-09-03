"""Phase 6: the UserPromptSubmit hook routes and detects stance, injecting
advisory context - and never spawns.

Before this, the hook only logged the prompt and flagged repeats, while the
docs honestly admitted "it does not yet call dispatch." Now it calls
dispatch.decide and stance.detect, so every prompt in a hook-loaded harness
(Claude Code) carries the routing verdict without anyone remembering to ask.
Kilo/Roo still depend on the persona - that boundary statement in
agent-guide.md must survive this change.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SMITH_ROOT = Path(__file__).resolve().parents[1]
AGENT_GUIDE = SMITH_ROOT / "docs" / "agent-guide.md"


def _hook(prompt: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", "hook", "prompt"],
        input=json.dumps({"prompt": prompt}),
        cwd=cwd,
        env={**dict(os.environ), "PYTHONPATH": str(SMITH_ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "hooked"
    (project / ".git").mkdir(parents=True)
    return project


class TestHookInjectsRouting:
    def test_a_high_confidence_prompt_injects_matched(self, tmp_path: Path) -> None:
        result = _hook("pytest is failing with a ValueError in the loader", _project(tmp_path))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "MATCHED awino-debug" in result.stdout

    def test_an_unroutable_prompt_injects_the_question(self, tmp_path: Path) -> None:
        result = _hook("xyzzy plugh wibble", _project(tmp_path))
        assert result.returncode == 0
        assert "ROUTING none" in result.stdout

    def test_a_stance_trigger_injects_the_switch(self, tmp_path: Path) -> None:
        result = _hook("I think we should rewrite the module in rust", _project(tmp_path))
        assert result.returncode == 0
        assert "STANCE -> steel-man" in result.stdout

    def test_plain_words_inject_no_stance_line(self, tmp_path: Path) -> None:
        result = _hook("pytest is failing with a ValueError", _project(tmp_path))
        assert "STANCE ->" not in result.stdout


class TestHookNeverSpawns:
    def test_the_hook_writes_no_floor_prompt_and_no_dispatch_artifacts(
        self, tmp_path: Path
    ) -> None:
        project = _project(tmp_path)
        _hook("pytest is failing with a ValueError in the loader", project)
        state = project / ".smith"
        assert not list(state.rglob("dispatch-f*")) if state.exists() else True


class TestDocsBoundaryStatementSurvives:
    def test_agent_guide_still_states_the_kilo_roo_persona_dependency(self) -> None:
        text = " ".join(AGENT_GUIDE.read_text(encoding="utf-8").split())
        assert "Kilo and Roo do not load that hook" in text
