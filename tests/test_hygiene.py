"""Tests for buddy's state-hygiene check ("clean folders").

Fixtures are synthetic state built in tmp dirs: a stale session, an
orphaned loop state, a partial (unparseable) loop state, unreferenced
clutter, ledger-referenced clutter, and duplicate session-end markers.
The report must flag each; `buddy --fix` must archive the stale session
(never delete it) with a ledger note, tidy the unambiguous clutter, leave
the referenced clutter and the loop states for a human, and afterwards the
ledger must still resolve every artifact it references. No test touches the
real workspace.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import session_state
from awino.cli import buddy
from awino.enforce import Ledger, LoopEvent

STALE_DAYS = 40


@pytest.fixture()
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.delenv("AWINO_HOME", raising=False)
    monkeypatch.delenv("SMITH_HOME", raising=False)
    monkeypatch.delenv("AWINO_PROJECT", raising=False)
    monkeypatch.delenv("SMITH_PROJECT", raising=False)
    return CliRunner()


@pytest.fixture()
def messy_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """State dir with one of each hygiene problem."""
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".awino"

    # Sessions: one stale, one active.
    session_state.start(state_root, "new-session")
    old_files = [
        state_root / "session" / "old-session.json",
        state_root / "session" / "old-session.jsonl",
    ]
    for path in old_files:
        path.write_text("{}\n", encoding="utf-8")
    ancient = time.time() - STALE_DAYS * 86400
    for path in old_files:
        os.utime(path, (ancient, ancient))

    # Loops: one orphaned (no events), one partial (does not parse).
    loops_dir = state_root / "loops"
    loops_dir.mkdir(parents=True, exist_ok=True)
    (loops_dir / "rpi-20260911-deadbeef.json").write_text(
        json.dumps(
            {"id": "rpi-20260911-deadbeef", "task": "t", "topic": "t", "phase": "implement"}
        ),
        encoding="utf-8",
    )
    (loops_dir / "rpi-20260911-broken.json").write_text(
        '{"id": "rpi-20260911-broken"}', encoding="utf-8"
    )

    # Clutter: one unreferenced, one the ledger references.
    (state_root / "scratch.tmp").write_text("junk\n", encoding="utf-8")
    (state_root / "keepme.tmp").write_text("load-bearing\n", encoding="utf-8")
    run_dir = state_root / "run" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "evidence.jsonl").write_text(
        '{"note": "keepme.tmp is load-bearing"}\n', encoding="utf-8"
    )

    # Duplicate session-end markers.
    (state_root / "session_ends.jsonl").write_text(
        '{"at": "2026-09-10T00:00:00+00:00"}\n'
        '{"at": "2026-09-10T00:00:00+00:00"}\n'
        '{"at": "2026-09-11T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    # A ledger-referenced artifact: the hygiene pass must never delete it.
    ledger = Ledger(state_root)
    ledger.record_loop_event(
        LoopEvent(
            loop_id="rpi-20260911-good",
            loop_kind="rpi",
            phase="research",
            kind="artifact_validated",
            at=datetime.now(UTC).isoformat(),
            detail="research artifact passed validation: docs/report.md",
        )
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "report.md").write_text("# report\n", encoding="utf-8")
    return state_root


def _findings(state_root: Path) -> list[buddy.HygieneFinding]:
    return buddy._hygiene_findings(state_root, Ledger(state_root))


# ── the report flags each problem ────────────────────────────────────────


def test_hygiene_flags_stale_session(messy_state: Path) -> None:
    stale = [f for f in _findings(messy_state) if f.kind == "stale_session"]
    assert [f.target for f in stale] == ["old-session"]
    assert "new-session" not in [f.target for f in stale]  # active: not stale


def test_hygiene_flags_orphaned_and_partial_loops(messy_state: Path) -> None:
    kinds = {f.target: f.kind for f in _findings(messy_state)}
    assert kinds["rpi-20260911-deadbeef"] == "orphaned_loop"
    assert kinds["rpi-20260911-broken"] == "partial_loop"


def test_hygiene_flags_clutter_and_duplicate_markers(messy_state: Path) -> None:
    findings = _findings(messy_state)
    clutter = {f.target: f.detail for f in findings if f.kind == "clutter"}
    assert clutter["scratch.tmp"] == "unreferenced temp/leftover file"
    assert "referenced by ledger" in clutter["keepme.tmp"]
    assert any(f.kind == "duplicate_marker" for f in findings)


def test_buddy_report_has_hygiene_section(
    messy_state: Path, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    assert "STATE HYGIENE" in result.output
    for marker in (
        "STALE_SESSION",
        "ORPHANED_LOOP",
        "PARTIAL_LOOP",
        "CLUTTER",
        "DUPLICATE_MARKER",
    ):
        assert marker in result.output, f"missing flag: {marker}"


def test_hygiene_clean_state_reports_clean(
    tmp_path: Path, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    assert "state dir is clean" in result.output


# ── --fix: archive, tidy, prompt ──────────────────────────────────────────


def test_fix_archives_stale_session_with_ledger_note(
    messy_state: Path, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert "FIX archived stale session old-session" in result.output
    archived = messy_state / "archive" / "sessions"
    assert (archived / "old-session.json").is_file()
    assert (archived / "old-session.jsonl").is_file()
    # Not deleted: the files survive under archive/.
    assert not (messy_state / "session" / "old-session.json").exists()
    # The ledger note records what happened and where it went.
    notes = [
        event
        for event in Ledger(messy_state).loop_events()
        if event.kind == "state_archived"
    ]
    assert len(notes) == 1
    assert "old-session" in notes[0].detail
    assert "archive/sessions/" in notes[0].detail
    # The active session is untouched, pointer intact.
    assert (messy_state / "session" / ".active").read_text(
        encoding="utf-8"
    ) == "new-session"


def test_fix_tidies_unambiguous_clutter_but_not_referenced(
    messy_state: Path, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert not (messy_state / "scratch.tmp").exists()
    assert (messy_state / "keepme.tmp").is_file()  # ledger-referenced: kept
    assert "PROMPT" in result.output and "keepme.tmp" in result.output


def test_fix_dedupes_markers_and_prompts_on_loop_state(
    messy_state: Path, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    lines = (messy_state / "session_ends.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # the duplicate is gone, both unique markers kept
    # Orphaned/partial loop state is judgmental: flagged, never moved.
    assert (messy_state / "loops" / "rpi-20260911-deadbeef.json").is_file()
    assert (messy_state / "loops" / "rpi-20260911-broken.json").is_file()
    assert "PROMPT" in result.output and "orphaned_loop" in result.output
    assert "PROMPT" in result.output and "partial_loop" in result.output


def test_fix_second_run_is_quiet(
    messy_state: Path, cli_runner: CliRunner
) -> None:
    first = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert first.exit_code == 0, first.output
    second = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert second.exit_code == 0, second.output
    assert "FIX archived stale session" not in second.output
    # The ledger note is recorded once, not once per run.
    notes = [
        event
        for event in Ledger(messy_state).loop_events()
        if event.kind == "state_archived"
    ]
    assert len(notes) == 1


def test_ledger_still_resolves_every_referenced_artifact_after_fix(
    messy_state: Path, cli_runner: CliRunner, tmp_path: Path
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    ledger = Ledger(messy_state)
    # Every artifact path the trail names still exists on disk.
    for event in ledger.loop_events():
        if event.kind != "artifact_validated":
            continue
        artifact = event.detail.split("passed validation:")[-1].strip()
        assert (tmp_path / artifact).is_file(), f"missing artifact: {artifact}"
    # The trail itself still parses, including the hygiene note.
    assert ledger.loop_events()
    # Run evidence the clutter check read is untouched.
    assert (messy_state / "run" / "r1" / "evidence.jsonl").is_file()
