"""WS-C: session-end markers.

`playbook.run_event` returns text lines and writes no marker, so before this
work the buddy report could only call session-end "unmeasured". Now every
firing of the session-end order (via `best --end` or buddy's --fix catch-up)
appends one JSON line to <state_root>/session_ends.jsonl, and the buddy
report counts those lines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smith import session_markers
from smith.cli import _workspace, buddy, project
from smith.enforce import Ledger


@pytest.fixture()
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    # Same as the buddy tests: workspace discovery must not inherit
    # ambient overrides.
    monkeypatch.delenv("AWINO_HOME", raising=False)
    monkeypatch.delenv("SMITH_HOME", raising=False)
    monkeypatch.delenv("AWINO_PROJECT", raising=False)
    monkeypatch.delenv("SMITH_PROJECT", raising=False)
    return CliRunner()


@pytest.fixture()
def state_root(tmp_path: Path) -> Path:
    root = tmp_path / ".smith"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_count_is_zero_when_marker_file_is_absent(state_root: Path) -> None:
    assert not (state_root / "session_ends.jsonl").exists()
    assert session_markers.count_session_ends(state_root) == 0


def test_record_twice_then_count_is_two(state_root: Path) -> None:
    first = session_markers.record_session_end(state_root)
    second = session_markers.record_session_end(state_root)
    assert first == second == state_root / "session_ends.jsonl"
    assert session_markers.count_session_ends(state_root) == 2
    lines = (state_root / "session_ends.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        record = json.loads(line)
        assert record["event"] == "session_end"
        assert record["at"]  # ISO-8601 timestamp, non-empty


def test_record_creates_the_directory_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir" / ".smith"
    path = session_markers.record_session_end(missing)
    assert path.is_file()
    assert session_markers.count_session_ends(missing) == 1


def test_count_ignores_blank_lines(state_root: Path) -> None:
    session_markers.record_session_end(state_root)
    with (state_root / "session_ends.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert session_markers.count_session_ends(state_root) == 1


def test_best_end_branch_records_a_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Exercises the real --end code path factored out of best_command,
    # not a copy of it: run the order, then read back through buddy's
    # real reporting function.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".smith").mkdir(exist_ok=True)
    (tmp_path / ".smith" / "MISSION.md").write_text("# Mission\n", encoding="utf-8")

    project._run_session_end_order()  # the code path `awino best --end` takes

    workspace = _workspace()
    marker = workspace.state_root / "session_ends.jsonl"
    assert marker.is_file()
    assert session_markers.count_session_ends(workspace.state_root) == 1
    assert buddy._playbook_events(Ledger(workspace.state_root)).session_end == 1


def test_buddy_report_shows_the_measured_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".smith"
    state_root.mkdir(exist_ok=True)
    session_markers.record_session_end(state_root)
    session_markers.record_session_end(state_root)
    buddy._run_report()
    out = capsys.readouterr().out
    assert "session-end: 2 (firings recorded in session_ends.jsonl)" in out
    assert "unmeasured" not in out


def test_buddy_fix_catch_up_records_a_marker(
    cli_runner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # buddy --fix runs the session-end order by hand when no marker exists;
    # that firing must count too.
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".smith"
    state_root.mkdir(exist_ok=True)
    (state_root / "MISSION.md").write_text("# Mission\n", encoding="utf-8")

    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert "SESSION_END_MARKED" in result.output
    assert buddy._playbook_events(Ledger(state_root)).session_end == 1


def test_buddy_fix_skips_catch_up_once_a_marker_exists(
    cli_runner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A second --fix must not re-run the session-end order: the marker from
    # the first catch-up means it is measured, not missing.
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".smith"
    state_root.mkdir(exist_ok=True)
    (state_root / "MISSION.md").write_text("# Mission\n", encoding="utf-8")

    first = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert first.exit_code == 0, first.output
    second = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert second.exit_code == 0, second.output
    assert "session-end is measured; no catch-up needed" in second.output
    assert "SESSION_END_MARKED" not in second.output
    assert buddy._playbook_events(Ledger(state_root)).session_end == 1
