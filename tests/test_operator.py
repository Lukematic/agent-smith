"""Bare ``awino`` is the operator: it orients on project state and narrates the
single next action, in plain language, per project state.

Each state gets a fixture project: fresh (onboarding), mid-loop (resume),
stale checklist (nudge), closed-with-verdicts (outcome summary), and mission
with nothing in flight (next-commitment suggestion). A destructive next
action must pause with an explicit yes/no instead of acting. A regression
class pins --help/--version/subcommand dispatch unchanged by the no-args
behavior change.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import cli, loops, operator, session_markers, working_memory
from awino.enforce import Ledger, LoopEvent

runner = CliRunner()

CRITERION = "suite green -> uv run pytest"
OBJECTIVE = "Ship the operator"


def _project(tmp_path: Path, name: str = "proj") -> Path:
    project = tmp_path / name
    project.mkdir()
    (project / ".git").mkdir()
    return project


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    return project


def _invoke(args: list[str]):
    return runner.invoke(cli.app, args)


def _state_root(project: Path) -> Path:
    return project / ".awino"


def _write_mission(project: Path) -> None:
    root = _state_root(project)
    root.mkdir(parents=True, exist_ok=True)
    (root / "heilmeier.json").write_text(
        json.dumps(
            {"answers": {"objective": OBJECTIVE, "exams": CRITERION}, "source": {}}
        ),
        encoding="utf-8",
    )


def _write_loop(
    project: Path,
    *,
    kind: str = "rpi",
    phase: str = "research",
    task: str = "Build the operator",
    check_command: str = "",
    loop_id: str | None = None,
) -> loops.LoopState:
    root = _state_root(project)
    loops_dir = root / "loops"
    loops_dir.mkdir(parents=True, exist_ok=True)
    state = loops.LoopState(
        id=loop_id or f"{kind}-20260911-abc123",
        task=task,
        topic="operator",
        phase=phase,
        created_at="2026-09-11T10:00:00+00:00",
        check_command=check_command,
    )
    (loops_dir / f"{state.id}.json").write_text(
        json.dumps(state.to_dict(), indent=2), encoding="utf-8"
    )
    (loops_dir / "current").write_text(state.id, encoding="utf-8")
    checklist = working_memory.Checklist(root)
    checklist.note_loop_created(state.id, kind, task, phase)
    return state


def _backdate_checklist(project: Path, days: int = 10) -> None:
    path = _state_root(project) / "checklist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    old = "2026-08-01T10:00:00+00:00"
    assert days >= working_memory.CHECKLIST_STALE_DAYS
    for item in data["items"]:
        for hop in item.get("history", []):
            hop["at"] = old
        item["updated_at"] = old
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _record_verdict(project: Path, state: loops.LoopState, word: str = "yes") -> None:
    Ledger(_state_root(project)).record_loop_event(
        LoopEvent(
            loop_id=state.id,
            loop_kind=state.id.split("-", 1)[0],
            phase=state.phase,
            kind="outcome_verdict",
            at=datetime.now(UTC).isoformat(),
            detail=(
                f"verdict: {word}; loop_id: {state.id}; "
                f"criteria_met: {CRITERION}; criteria_unmet: (none)"
            ),
        )
    )


def _sections(stdout: str) -> None:
    for section in ("MISSION", "CHECKLIST", "SEEDS", "MEMORY", "SESSION", "LOOPS"):
        assert f"\n{section}\n" in stdout, f"missing section {section}"


class TestFreshProjectOnboards:
    def test_no_args_runs_the_operator(self, proj: Path) -> None:
        result = _invoke([])
        assert result.exit_code == 0, result.output
        assert "OPERATOR" in result.output
        assert "Usage:" not in result.output

    def test_fresh_state_asks_the_first_onboarding_question(self, proj: Path) -> None:
        result = _invoke([])
        assert result.exit_code == 0, result.output
        assert "no mission on file" in result.output
        assert "no success criteria on file" in result.output
        assert "What outcome should this project create for its user?" in result.output
        assert "awino onboard --set mission=" in result.output

    def test_fresh_state_renders_every_read(self, proj: Path) -> None:
        result = _invoke([])
        assert result.exit_code == 0, result.output
        _sections(result.output)
        assert "Here's where we are" in result.output
        assert "Here's what's next" in result.output
        assert "Here's what I need from you" in result.output

    def test_operator_records_only_the_safe_session_start_marker(
        self, proj: Path
    ) -> None:
        result = _invoke([])
        assert result.exit_code == 0, result.output
        root = _state_root(proj)
        assert session_markers.count_session_starts(root) == 1
        # Nothing else: orientation is read-only, the marker is the one
        # unpaused internal write.
        assert {p.name for p in root.iterdir()} == {"session_starts.jsonl"}
        # And the session-end count is untouched by the new marker file.
        assert session_markers.count_session_ends(root) == 0


class TestMidLoopResumes:
    def test_resume_shows_loop_kind_phase_and_spine(
        self, proj: Path
    ) -> None:
        _write_mission(proj)
        state = _write_loop(proj)
        result = _invoke([])
        assert result.exit_code == 0, result.output
        assert "1 open loop(s) (rpi)" in result.output
        assert f"phase {state.phase}" in result.output
        assert state.id in result.output
        # SPINE precondition status, in owner order.
        assert "[ok] mission" in result.output
        assert "next:" in result.output

    def test_resume_narrates_the_next_step_without_running_it(
        self, proj: Path
    ) -> None:
        _write_mission(proj)
        state = _write_loop(proj)
        before = (_state_root(proj) / "loops" / f"{state.id}.json").read_bytes()
        result = _invoke([])
        assert result.exit_code == 0, result.output
        assert f"awino loop next --id {state.id}" in result.output
        after = (_state_root(proj) / "loops" / f"{state.id}.json").read_bytes()
        assert before == after, "the operator must not advance the loop itself"


class TestStaleLoopNudges:
    def test_stale_checklist_nudges_with_the_exact_command(
        self, proj: Path
    ) -> None:
        _write_mission(proj)
        state = _write_loop(proj)
        _backdate_checklist(proj)
        result = _invoke([])
        assert result.exit_code == 0, result.output
        assert "hasn't moved in" in result.output
        assert "Unstick the loop" in result.output
        assert f"awino loop next --id {state.id}" in result.output


class TestClosedLoopsSummarize:
    def test_outcome_summary_judges_criteria(self, proj: Path) -> None:
        _write_mission(proj)
        state = _write_loop(proj, phase="done")
        _record_verdict(proj, state, word="yes")
        result = _invoke([])
        assert result.exit_code == 0, result.output
        assert "all loops closed with outcome verdicts" in result.output
        assert "accomplished" in result.output
        assert f"[met] {CRITERION}" in result.output


class TestNoWorkSuggestsCommitment:
    def test_suggests_a_concrete_next_commitment(self, proj: Path) -> None:
        _write_mission(proj)
        result = _invoke([])
        assert result.exit_code == 0, result.output
        assert "nothing in flight" in result.output
        # Concrete, not generic: names the thinking-mode pass to run.
        assert "awino think premortem" in result.output


class TestDestructiveNextActionPauses:
    def test_ralph_verify_with_destructive_check_pauses_with_yes_no(
        self, proj: Path
    ) -> None:
        _write_mission(proj)
        state = _write_loop(
            proj,
            kind="ralph",
            phase="verify",
            check_command="rm -rf ./build && ./deploy.sh --prod",
            loop_id="ralph-20260911-deadbe",
        )
        loop_file = _state_root(proj) / "loops" / f"{state.id}.json"
        before = loop_file.read_bytes()
        result = _invoke([])
        assert result.exit_code == 0, result.output
        assert "PAUSED" in result.output
        assert "rm -rf ./build && ./deploy.sh --prod" in result.output
        assert "YES" in result.output
        assert "NO" in result.output
        # Paused means nothing acted: the loop state is byte-identical and no
        # check command ran.
        assert loop_file.read_bytes() == before
        # The safe session-start marker is still the only write.
        assert session_markers.count_session_starts(_state_root(proj)) == 1

    def test_pause_rule_refuses_unsafe_self_actions(self, tmp_path: Path) -> None:
        action = operator._SelfAction(
            id="continue-loop",
            description="run the check command",
            command_text="rm -rf ./build",
        )
        with pytest.raises(operator._PauseNeeded):
            operator._run_self(action, tmp_path)
        safe = operator._SelfAction(id="session-start-marker", description="mark")
        note = operator._run_self(safe, tmp_path)
        assert "session-start marker" in note
        assert session_markers.count_session_starts(tmp_path) == 1


class TestNoArgsChangeKeepsTheSurface:
    def test_help_still_works(self, proj: Path) -> None:
        result = _invoke(["--help"])
        assert result.exit_code == 0, result.output
        assert "A.W.I.N.O." in result.output

    def test_version_still_works(self, proj: Path) -> None:
        result = _invoke(["--version"])
        assert result.exit_code == 0, result.output
        assert result.output.startswith("awino ")

    def test_subcommand_dispatch_still_works(self, proj: Path) -> None:
        for args in (["loop", "--help"], ["best", "--help"], ["buddy", "--help"]):
            result = _invoke(args)
            assert result.exit_code == 0, f"{args}: {result.output}"

    def test_unknown_command_still_errors(self, proj: Path) -> None:
        result = _invoke(["nope"])
        assert result.exit_code != 0
