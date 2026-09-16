"""Tests for truthful header status line generated from stored state."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from awino import controller
from awino.cli import app
from awino.enforce import Ledger, TaskClass


def test_render_header_default_state(tmp_path: Path) -> None:
    state = tmp_path / ".awino"
    state.mkdir()
    header = controller.render_header(state)
    assert (
        header
        == "[A.W.I.N.O. | phase: idle | loop: direct | run: none | knowledge: 0/3 | skill: none | stance: advisor | host: unrecorded]"
    )


def test_render_header_with_active_plan_and_receipts(tmp_path: Path) -> None:
    state = tmp_path / ".awino"
    state.mkdir()
    ledger = Ledger(state)
    run = ledger.open(TaskClass.BUGFIX, "fix memory leak", loop="ralph")
    adapter = controller.for_machine(state, run.run_id)
    adapter.controller.submit_event(
        event_id="sk-1",
        kind="skill_selected",
        payload={"skill": "awino-debug", "version": "v1", "status": "selected"},
    )
    adapter.controller.submit_event(
        event_id="st-1",
        kind="stance_checked",
        payload={"stance": "steel-man", "status": "checked", "response_hash": "abc1234"},
    )
    adapter.controller.submit_event(
        event_id="h-1",
        kind="host_user_turn",
        payload={"host": "kilo", "session_id": "ses-1"},
    )

    header = controller.render_header(state)
    assert f"run: {run.run_id}" in header
    assert "loop: ralph" in header
    assert "skill: awino-debug" in header
    assert "stance: steel-man" in header
    assert "host: kilo" in header


def test_header_cli_command(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("AWINO_PROJECT", str(tmp_path))
    (tmp_path / ".awino").mkdir(parents=True, exist_ok=True)
    result = runner.invoke(app, ["header"])
    assert result.exit_code == 0
    assert "[A.W.I.N.O. | phase:" in result.output
