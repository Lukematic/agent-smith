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

from smith import stance
from smith.cli import buddy
from smith.enforce import Ledger, LoopEvent, TaskClass

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


# ── (b) loop honesty: event trail first, run heuristic as fallback ──────────


def _record_loop_event(
    ledger: Ledger,
    loop_id: str,
    loop_kind: str,
    kind: str,
    phase: str = "research",
    detail: str = "",
) -> None:
    ledger.record_loop_event(
        LoopEvent(
            loop_id=loop_id,
            loop_kind=loop_kind,
            phase=phase,
            kind=kind,
            at=datetime.now(UTC).isoformat(),
            detail=detail,
        )
    )


@pytest.fixture()
def ledger_with_loop_events(tmp_path: Path) -> Ledger:
    ledger = _make_ledger(tmp_path)
    # rpi loop A: walked -- artifact validated, advanced to plan
    _record_loop_event(ledger, "rpi-a", "rpi", "loop_started")
    _record_loop_event(ledger, "rpi-a", "rpi", "phase_started", "research")
    _record_loop_event(ledger, "rpi-a", "rpi", "artifact_validated", "research")
    _record_loop_event(ledger, "rpi-a", "rpi", "phase_started", "plan")
    # rpi loop B: started but never walked
    _record_loop_event(ledger, "rpi-b", "rpi", "loop_started")
    _record_loop_event(ledger, "rpi-b", "rpi", "phase_started", "research")
    # ralph loop C: walked by advancing past its first phase, no validation
    _record_loop_event(ledger, "ralph-c", "ralph", "loop_started")
    _record_loop_event(ledger, "ralph-c", "ralph", "phase_started", "research")
    _record_loop_event(ledger, "ralph-c", "ralph", "phase_started", "plan")
    return ledger


def test_loop_honesty_reads_loop_events(ledger_with_loop_events: Ledger) -> None:
    honesty, source = buddy._loop_honesty(ledger_with_loop_events)
    assert honesty == {"rpi": (2, 1), "ralph": (1, 1)}
    assert "loop ledger events" in source


def test_loop_honesty_falls_back_to_run_heuristic(
    ledger_with_runs: Ledger,
) -> None:
    # Runs that predate the event trail keep the old checkpoints/skill-used
    # heuristic, and the report says so.
    honesty, source = buddy._loop_honesty(ledger_with_runs)
    assert honesty["rpi"] == (3, 2)
    assert honesty["direct"] == (2, 0)
    assert "predates loop events" in source


def test_loop_honesty_empty_ledger(tmp_path: Path) -> None:
    honesty, source = buddy._loop_honesty(_make_ledger(tmp_path))
    assert honesty == {}
    assert source  # a source is always named, even with nothing to count


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
    assert "source: run checkpoints/skill-used (predates loop events)" in result.output


def test_buddy_command_reads_loop_events_when_present(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ledger = _make_ledger(tmp_path)
    at = datetime.now(UTC).isoformat()
    ledger.record_loop_event(
        LoopEvent(
            loop_id="rpi-1",
            loop_kind="rpi",
            phase="research",
            kind="loop_started",
            at=at,
            detail="",
        )
    )
    ledger.record_loop_event(
        LoopEvent(
            loop_id="rpi-1",
            loop_kind="rpi",
            phase="research",
            kind="phase_started",
            at=at,
            detail="",
        )
    )
    _write_mission_and_seeds(tmp_path, time.time() - 86400)
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    # Started but never validated or advanced: declared 1, walked 0.
    assert "declared rpi: 1, with phase evidence: 0" in result.output
    assert "gap: 1 run(s) declared a loop with no phase evidence" in result.output
    assert "source: loop ledger events (loops.jsonl)" in result.output


def test_buddy_command_handles_missing_state(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    for header in SECTION_HEADERS:
        assert header in result.output
    assert "none found" in result.output


# ── (f) --fix: mechanical corrections, same section order as the report ─────


def test_fix_refreshes_stale_mission(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    mission_epoch = time.time() - 5 * 86400
    _write_mission_and_seeds(tmp_path, mission_epoch)
    mission = tmp_path / ".smith" / "MISSION.md"
    old_text = mission.read_text(encoding="utf-8")
    old_mtime = mission.stat().st_mtime
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert "FIX mission refreshed:" in result.output
    assert mission.read_text(encoding="utf-8") != old_text
    assert mission.stat().st_mtime > old_mtime
    assert "BUDDY-FIX done: 2 correction(s) applied, 0 need a human" in result.output


def test_fix_marks_unwalked_loop_and_prints_exact_redo(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ledger = _make_ledger(tmp_path)
    at = datetime.now(UTC).isoformat()
    # loop_started carries the original task in its detail, as the driver writes
    ledger.record_loop_event(
        LoopEvent(
            loop_id="rpi-9",
            loop_kind="rpi",
            phase="research",
            kind="loop_started",
            at=at,
            detail="task: fix the loader",
        )
    )
    ledger.record_loop_event(
        LoopEvent(
            loop_id="rpi-9",
            loop_kind="rpi",
            phase="research",
            kind="phase_started",
            at=at,
            detail="initial phase",
        )
    )
    # never-walked loop with no recorded task: objective unknowable
    ledger.record_loop_event(
        LoopEvent(
            loop_id="rpi-10",
            loop_kind="rpi",
            phase="research",
            kind="loop_started",
            at=at,
            detail="",
        )
    )
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    markers = [
        event
        for event in ledger.loop_events("rpi-9")
        if event.detail.startswith(buddy._UNWALKED_MARKER)
    ]
    assert len(markers) == 1
    assert markers[0].kind == "loop_started"  # a real kind, never a new one
    assert markers[0].phase == ""
    assert 'awino loop run rpi --task "fix the loader"' in result.output
    assert 'awino loop run rpi --task "..."' in result.output
    assert 'fill in the original task where "..." stands' in result.output
    # idempotent: a second --fix records no further marker ...
    again = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert again.exit_code == 0, again.output
    markers_again = [
        event
        for event in ledger.loop_events("rpi-9")
        if event.detail.startswith(buddy._UNWALKED_MARKER)
    ]
    assert len(markers_again) == 1
    # ... and the marker never inflates the honesty counts of a later report
    report = cli_runner.invoke(buddy.buddy_app, [])
    assert report.exit_code == 0, report.output
    assert "declared rpi: 2, with phase evidence: 0" in report.output


def test_fix_stance_miss_prints_sample_and_exact_pattern(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _make_ledger(tmp_path)
    fake_miss = [buddy.StanceProbe("steel-man", "challenge this", None)]
    monkeypatch.setattr(buddy, "_stance_self_test", lambda: fake_miss)
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    pattern = dict(stance._RULES)["steel-man"].pattern
    assert (
        f"  MISS steel-man <- 'challenge this' : expected pattern r'{pattern}'"
        in result.output
    )
    assert (
        "ACTION  update the steel-man regex in src/smith/stance.py _RULES"
        in result.output
    )
    assert "BUDDY-FIX done: 1 correction(s) applied, 2 need a human" in result.output


def test_fix_all_passing_stance_self_test_says_so(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _make_ledger(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert "all 14 samples fired their stance (no correction needed)" in result.output
    assert "  MISS " not in result.output


def test_fix_session_end_runs_catch_up_once(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".smith"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "MISSION.md").write_text("# Mission\n", encoding="utf-8")
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    # the session-end order's real steps ran, each line prefixed FIX
    assert "  FIX [summary] skill=direct" in result.output
    assert "  FIX [lesson-check] skill=awino-memory" in result.output
    assert "  FIX [mission-refresh] skill=awino-discover" in result.output
    assert "BUDDY-FIX done: 1 correction(s) applied, 0 need a human" in result.output


def test_fix_missing_mission_prints_concrete_human_action(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _make_ledger(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert (
        '  ACTION  write the mission line: awino mission --set "objective=<one sentence>"'
        in result.output
    )
    assert "BUDDY-FIX done: 1 correction(s) applied, 1 need a human" in result.output


def test_fix_run_level_gap_prints_redo_without_fake_marker(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Runs that predate the loop event trail: no loop id exists, so --fix must
    # not invent a loop_started marker; the redo command is the human action.
    monkeypatch.chdir(tmp_path)
    ledger = _make_ledger(tmp_path)
    run = ledger.open(TaskClass.BUGFIX, "bare rpi run", loop="rpi")
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert (
        f'  ACTION  re-run the rpi loop by hand: awino loop run rpi --task "{run.objective}"'
        in result.output
    )
    assert ledger.loop_events() == []
    assert "no ledger marker recorded" in result.output
