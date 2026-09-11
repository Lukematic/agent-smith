"""Tests for the buddy diagnostic: effectiveness reported from real state.

All fixtures are synthetic state built in tmp dirs: fake ledger runs, fake
mission files with known mtimes, and a fake seeds tracker. No test touches
the real workspace.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smith.cli import buddy
from smith.enforce import Ledger, TaskClass

EXPECTED_STANCES = {
    "advisor",
    "first-principles",
    "steel-man",
    "assumption-audit",
    "teach-back",
    "research-intake",
    "expert",
}


def _make_ledger(tmp_path: Path) -> Ledger:
    state_root = tmp_path / ".smith"
    state_root.mkdir(parents=True, exist_ok=True)
    return Ledger(state_root)


# ── (a) stance self-test: every stance fires on its samples ──────────────────


def test_stance_self_test_covers_all_seven_stances_with_two_samples_each() -> None:
    probes = buddy._stance_self_test()
    by_stance: dict[str, list[str]] = {}
    for probe in probes:
        by_stance.setdefault(probe.expected, []).append(probe.sample)
    assert set(by_stance) == EXPECTED_STANCES
    for name, samples in by_stance.items():
        assert len(samples) == 2, f"stance {name} needs exactly 2 samples"


def test_stance_self_test_each_sample_fires_its_stance() -> None:
    for probe in buddy._stance_self_test():
        if probe.expected == "advisor":
            # advisor is the default: no detector rule, so nothing fires
            assert probe.fired is None, f"advisor sample should not fire: {probe.sample!r}"
        else:
            assert probe.fired == probe.expected, (
                f"sample {probe.sample!r} fired {probe.fired!r}, "
                f"expected {probe.expected!r}"
            )


# ── (b) loop honesty counts ──────────────────────────────────────────────────


@pytest.fixture()
def ledger_with_runs(tmp_path: Path) -> Ledger:
    ledger = _make_ledger(tmp_path)
    # 3 rpi runs: one with a checkpoint, one with a skill-used record, one bare
    rpi_checkpointed = ledger.open(TaskClass.BUGFIX, "reproduce the crash", loop="rpi")
    ledger.checkpoint(
        rpi_checkpointed.run_id, phase="reproduce", summary="crash reproduced", next_action="fix"
    )
    rpi_skill_used = ledger.open(TaskClass.BUGFIX, "fix the loader", loop="rpi")
    ledger.note_skill(rpi_skill_used.run_id, "awino-debug", state="used", reason="repro")
    ledger.open(TaskClass.BUGFIX, "bare rpi run", loop="rpi")
    # 2 direct runs: one with only a skill-*loaded* record (not evidence of
    # walked phases), one bare
    direct_loaded = ledger.open(TaskClass.BUGFIX, "loaded but unused", loop="direct")
    ledger.note_skill(direct_loaded.run_id, "awino-debug", state="loaded", reason="peek")
    ledger.open(TaskClass.BUGFIX, "bare direct run", loop="direct")
    return ledger


def test_loop_honesty_counts_match_fixture(ledger_with_runs: Ledger) -> None:
    honesty = buddy._loop_honesty(ledger_with_runs)
    assert honesty["rpi"] == (3, 2)
    assert honesty["direct"] == (2, 0)


def test_loop_honesty_empty_ledger(tmp_path: Path) -> None:
    assert buddy._loop_honesty(_make_ledger(tmp_path)) == {}


# ── (c) playbook event counts ────────────────────────────────────────────────


def test_playbook_task_close_counts_closed_runs(ledger_with_runs: Ledger) -> None:
    runs = buddy._iter_runs(ledger_with_runs)
    assert len(runs) == 5
    ledger_with_runs.mark_complete(runs[0].run_id)
    ledger_with_runs.mark_complete(runs[3].run_id)
    events = buddy._playbook_events(ledger_with_runs)
    assert events.task_close == 2


def test_playbook_session_end_is_unmeasured(ledger_with_runs: Ledger) -> None:
    # best --end records no marker, so it must be reported as unmeasured,
    # not as zero firings.
    events = buddy._playbook_events(ledger_with_runs)
    assert events.session_end is None


# ── (d) mission freshness math ───────────────────────────────────────────────


def _write_mission_and_seeds(tmp_path: Path, mission_epoch: float) -> tuple[Path, Path]:
    state_root = tmp_path / ".smith"
    state_root.mkdir(parents=True, exist_ok=True)
    mission = state_root / "MISSION.md"
    mission.write_text("# Mission\n", encoding="utf-8")
    os.utime(mission, (mission_epoch, mission_epoch))
    seeds_dir = tmp_path / ".seeds"
    seeds_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        # closed after the mission changed -> counts
        {"id": "1", "title": "a", "status": "closed",
         "updated": datetime.fromtimestamp(mission_epoch + 100, UTC).isoformat()},
        # closed before the mission changed -> does not count
        {"id": "2", "title": "b", "status": "closed",
         "updated": datetime.fromtimestamp(mission_epoch - 100, UTC).isoformat()},
        # still open -> never counts
        {"id": "3", "title": "c", "status": "open",
         "updated": datetime.fromtimestamp(mission_epoch + 200, UTC).isoformat()},
    ]
    (seeds_dir / "issues.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    return tmp_path, state_root


def test_mission_freshness_math(tmp_path: Path) -> None:
    mission_epoch = time.time() - 5 * 86400
    project_root, state_root = _write_mission_and_seeds(tmp_path, mission_epoch)
    now = mission_epoch + 5 * 86400 + 3600
    fresh = buddy._mission_freshness(project_root, state_root, now=now)
    assert fresh.mission_path == state_root / "MISSION.md"
    assert fresh.days_since_change == 5
    assert fresh.seeds_closed_since == 1


def test_mission_freshness_missing_mission(tmp_path: Path) -> None:
    state_root = tmp_path / ".smith"
    state_root.mkdir(parents=True, exist_ok=True)
    fresh = buddy._mission_freshness(tmp_path, state_root)
    assert fresh.mission_path is None
    assert fresh.days_since_change is None
    assert fresh.seeds_closed_since is None
    assert any("no mission found" in note for note in fresh.notes)


def test_mission_freshness_missing_tracker(tmp_path: Path) -> None:
    state_root = tmp_path / ".smith"
    state_root.mkdir(parents=True, exist_ok=True)
    mission = state_root / "MISSION.md"
    mission.write_text("# Mission\n", encoding="utf-8")
    fresh = buddy._mission_freshness(tmp_path, state_root)
    assert fresh.days_since_change is not None
    assert fresh.seeds_closed_since is None
    assert any("no .seeds tracker" in note for note in fresh.notes)


# ── (e) the buddy command: exit 0 and all section headers ────────────────────

SECTION_HEADERS = [
    "STANCE SELF-TEST",
    "LOOP HONESTY",
    "PLAYBOOK EVENTS",
    "MISSION FRESHNESS",
]


@pytest.fixture()
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    # Workspace discovery must not inherit ambient overrides.
    monkeypatch.delenv("AWINO_HOME", raising=False)
    monkeypatch.delenv("SMITH_HOME", raising=False)
    monkeypatch.delenv("AWINO_PROJECT", raising=False)
    monkeypatch.delenv("SMITH_PROJECT", raising=False)
    return CliRunner()


def test_buddy_command_exits_zero_with_all_sections(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ledger = _make_ledger(tmp_path)
    run = ledger.open(TaskClass.BUGFIX, "fixture run", loop="rpi")
    ledger.checkpoint(run.run_id, phase="reproduce", summary="s", next_action="n")
    ledger.mark_complete(run.run_id)
    _write_mission_and_seeds(tmp_path, time.time() - 86400)
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    for header in SECTION_HEADERS:
        assert header in result.output, f"missing section header: {header}"
    assert "declared rpi: 1, with phase evidence: 1" in result.output
    assert "task-close: 1" in result.output


def test_buddy_command_handles_missing_state(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    for header in SECTION_HEADERS:
        assert header in result.output
    assert "none found" in result.output
