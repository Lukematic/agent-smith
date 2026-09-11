"""Tests for the outcome verdict (`awino loop close`) and buddy's outcome rates.

- `loop close --id <id> --verdict yes|partial|no` records an outcome_verdict
  ledger event carrying the verdict, the loop id/kind, the seed id (or
  "seed: none" explicitly), and the mission goal statement ("goal: unstated"
  when no mission source names one -- never invented).
- Without --verdict the command prints the outcome question and exits non-zero.
- `awino buddy` reports OUTCOME RATES -- per-project totals and per-loop-type
  rates of accomplished / partial / not -- from the ledger's verdict events.
- Loops closed without a verdict are flagged "outcome unmeasured"; buddy
  --fix prints the exact prompting command without inventing a verdict.

All state lives in tmp dirs; the real workspace is never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import loops
from awino.cli import buddy, loopctl
from awino.cli.loopctl import loop_app
from awino.enforce import Ledger, LoopEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"

runner = CliRunner()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    project.mkdir(parents=True)
    return project


@pytest.fixture()
def cli_env(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AWINO_PROJECT", str(project))
    return project


def _state_root(project: Path) -> Path:
    return project / ".awino"


def _ledger(project: Path) -> Ledger:
    return Ledger(_state_root(project))


def _run_loop(project: Path, kind: str = "rpi") -> str:
    """Start a loop through the CLI; return its loop id."""
    args = ["run", kind, "--task", "add the thing"]
    if kind == "ralph":
        args += ["--check", "true"]
    result = runner.invoke(loop_app, args)
    assert result.exit_code == 0, result.output
    for line in result.output.splitlines():
        if line.startswith("LOOP"):
            return line.split(None, 1)[1].strip()
    raise AssertionError("no LOOP line in output")


def _attach_seed(project: Path, loop_id: str, seed_id: str) -> None:
    """Attach a seed to an existing loop state without the sd tracker."""
    driver = loops.RpiDriver(
        project_root=project,
        loops_dir=_state_root(project) / "loops",
        skill_md=SKILL_MD,
    )
    state = driver.load(loop_id)
    state.seed_id = seed_id
    driver.save(state)


def _verdict_event(ledger: Ledger, loop_id: str) -> LoopEvent:
    events = [event for event in ledger.loop_events(loop_id) if event.kind == "outcome_verdict"]
    assert len(events) == 1, f"expected one verdict event, got {len(events)}"
    return events[0]


def _write_mission_goal(project: Path, goal: str) -> None:
    _state_root(project).mkdir(parents=True, exist_ok=True)
    (_state_root(project) / "MISSION.md").write_text(
        f"# Demo project\n\n## {goal}\n", encoding="utf-8"
    )


# ── `awino loop close` ───────────────────────────────────────────────────────


@pytest.mark.parametrize("verdict", ["yes", "partial", "no"])
def test_close_records_verdict_event_with_seed_and_goal_linkage(
    cli_env: Path, verdict: str
) -> None:
    project = cli_env
    loop_id = _run_loop(project)
    _attach_seed(project, loop_id, "seed-42")
    _write_mission_goal(project, "Ship the thing")

    result = runner.invoke(
        loop_app, ["close", "--id", loop_id, "--verdict", verdict, "--note", "shipped"]
    )
    assert result.exit_code == 0, result.output
    assert "Did this accomplish the goal?" in result.output
    assert f"VERDICT  {verdict}" in result.output
    assert "PURPOSE" in result.output
    assert "YOU" in result.output
    assert "CHECK" in result.output
    assert "next:" in result.output

    event = _verdict_event(_ledger(project), loop_id)
    assert event.kind == "outcome_verdict"
    assert event.loop_kind == "rpi"
    assert f"verdict: {verdict}" in event.detail
    assert f"loop_id: {loop_id}" in event.detail
    assert "loop_kind: rpi" in event.detail
    assert "seed: seed-42" in event.detail
    assert "goal: Ship the thing" in event.detail
    assert "seed_status:" in event.detail
    assert "note: shipped" in event.detail


def test_close_without_verdict_exits_nonzero_and_prints_the_question(
    cli_env: Path,
) -> None:
    project = cli_env
    loop_id = _run_loop(project)

    result = runner.invoke(loop_app, ["close", "--id", loop_id])
    assert result.exit_code != 0
    assert "Did this accomplish the goal?" in result.output
    assert "--verdict" in result.output
    # No verdict was recorded.
    assert not [
        event for event in _ledger(project).loop_events(loop_id) if event.kind == "outcome_verdict"
    ]


def test_close_with_bad_verdict_exits_nonzero(cli_env: Path) -> None:
    project = cli_env
    loop_id = _run_loop(project)
    result = runner.invoke(loop_app, ["close", "--id", loop_id, "--verdict", "maybe"])
    assert result.exit_code != 0
    assert "Did this accomplish the goal?" in result.output
    assert "yes|partial|no" in result.output or "yes, partial" in result.output


def test_close_without_seed_records_seed_none_explicitly(cli_env: Path) -> None:
    project = cli_env
    loop_id = _run_loop(project)

    result = runner.invoke(loop_app, ["close", "--id", loop_id, "--verdict", "yes"])
    assert result.exit_code == 0, result.output
    event = _verdict_event(_ledger(project), loop_id)
    assert "seed: none" in event.detail
    # No mission on file: the goal must be recorded as unstated, never invented.
    assert "goal: unstated" in event.detail
    assert "goal: unstated" in result.output


def test_close_seed_closed_already_status(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The verdict event states the seed's resulting status without changing it."""
    project = cli_env
    loop_id = _run_loop(project)
    _attach_seed(project, loop_id, "seed-7")

    class _ClosedIssue:
        id = "seed-7"
        title = "The tracked work"
        status = "closed"

        @property
        def open(self) -> bool:
            return False

    class _FakeTracker:
        def __init__(self, root: Path) -> None:
            self.root = root

        def state(self):
            class _State:
                usable = True

            return _State(), "ready"

        def show(self, issue_id: str):
            assert issue_id == "seed-7"
            return _ClosedIssue()

    monkeypatch.setattr(loopctl, "Seeds", _FakeTracker)
    result = runner.invoke(loop_app, ["close", "--id", loop_id, "--verdict", "yes"])
    assert result.exit_code == 0, result.output
    assert "The tracked work" in result.output  # seed title in the goal context
    event = _verdict_event(_ledger(project), loop_id)
    assert "seed: seed-7" in event.detail
    assert "seed_status: closed already" in event.detail


def test_close_seed_still_open_for_rpi_handoff(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An RPI loop leaves its seed open for the gate: the verdict says so."""
    project = cli_env
    loop_id = _run_loop(project)
    _attach_seed(project, loop_id, "seed-9")

    class _OpenIssue:
        id = "seed-9"
        title = "Gate-owned work"
        status = "open"

        @property
        def open(self) -> bool:
            return True

    class _FakeTracker:
        def __init__(self, root: Path) -> None:
            self.root = root

        def state(self):
            class _State:
                usable = True

            return _State(), "ready"

        def show(self, issue_id: str):
            return _OpenIssue()

    monkeypatch.setattr(loopctl, "Seeds", _FakeTracker)
    result = runner.invoke(loop_app, ["close", "--id", loop_id, "--verdict", "partial"])
    assert result.exit_code == 0, result.output
    event = _verdict_event(_ledger(project), loop_id)
    assert "seed: seed-9" in event.detail
    assert "still open" in event.detail
    assert "gate" in event.detail


# ── buddy outcome rates ──────────────────────────────────────────────────────


@pytest.fixture()
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    # Workspace discovery must not inherit ambient overrides.
    monkeypatch.delenv("AWINO_HOME", raising=False)
    monkeypatch.delenv("SMITH_HOME", raising=False)
    monkeypatch.delenv("AWINO_PROJECT", raising=False)
    monkeypatch.delenv("SMITH_PROJECT", raising=False)
    return CliRunner()


def _record(
    ledger: Ledger,
    loop_id: str,
    loop_kind: str,
    kind: str,
    phase: str = "done",
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


def _verdict_detail(verdict: str, seed: str, goal: str) -> str:
    return (
        f"verdict: {verdict}; loop_id: x; loop_kind: x; "
        f"seed: {seed}; goal: {goal}; seed_status: n/a: no seed attached"
    )


def _verdict_ledger(tmp_path: Path) -> Ledger:
    """2 yes (rpi), 1 partial (ralph), 1 no (delegate), plus one closed-but-
    unmeasured rpi loop."""
    state_root = tmp_path / ".awino"
    state_root.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(state_root)
    _record(ledger, "rpi-a", "rpi", "loop_closed", detail="all phases complete")
    _record(
        ledger,
        "rpi-a",
        "rpi",
        "outcome_verdict",
        detail=_verdict_detail("yes", "none", "Ship the thing"),
    )
    _record(ledger, "rpi-b", "rpi", "loop_closed", detail="all phases complete")
    _record(
        ledger,
        "rpi-b",
        "rpi",
        "outcome_verdict",
        detail=_verdict_detail("yes", "seed-1", "Ship the thing"),
    )
    _record(ledger, "ralph-c", "ralph", "loop_closed", detail="verified")
    _record(
        ledger,
        "ralph-c",
        "ralph",
        "outcome_verdict",
        detail=_verdict_detail("partial", "none", "Ship the thing"),
    )
    _record(ledger, "delegate-d", "delegate", "loop_closed", detail="verified")
    _record(
        ledger,
        "delegate-d",
        "delegate",
        "outcome_verdict",
        detail=_verdict_detail("no", "none", "Ship the thing"),
    )
    # Closed without a verdict: the outcome is unmeasured.
    _record(ledger, "rpi-e", "rpi", "loop_closed", detail="all phases complete")
    return ledger


def test_buddy_reports_outcome_rates_per_project_and_per_type(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _verdict_ledger(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    assert "OUTCOME RATES" in result.output
    # Project totals: 4 verdicts -> 2 accomplished (50%), 1 partial (25%), 1 not (25%).
    assert "4 verdicts: 2 accomplished (50%), 1 partial (25%), 1 not (25%)" in result.output
    # Per loop type.
    assert "rpi: 2 verdicts: 2 accomplished (100%), 0 partial (0%), 0 not (0%)" in result.output
    assert "ralph: 1 verdicts: 0 accomplished (0%), 1 partial (100%), 0 not (0%)" in result.output
    assert (
        "delegate: 1 verdicts: 0 accomplished (0%), 0 partial (0%), 1 not (100%)" in result.output
    )


def test_buddy_flags_loop_closed_without_a_verdict(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _verdict_ledger(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    assert "outcome unmeasured" in result.output
    assert "rpi-e" in result.output
    assert "(kind rpi)" in result.output


def test_buddy_fix_prompts_the_exact_close_command(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ledger = _verdict_ledger(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert "outcome unmeasured" in result.output
    assert "awino loop close --id rpi-e --verdict yes|partial|no" in result.output
    # --fix never invents a verdict: no outcome_verdict event for the loop.
    assert not [event for event in ledger.loop_events("rpi-e") if event.kind == "outcome_verdict"]


def test_buddy_outcome_rates_with_no_verdicts(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".awino"
    state_root.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(state_root)
    _record(ledger, "rpi-z", "rpi", "loop_closed", detail="all phases complete")
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    assert "OUTCOME RATES" in result.output
    assert "no outcome_verdict events" in result.output
    assert "outcome unmeasured" in result.output
    assert "rpi-z" in result.output
