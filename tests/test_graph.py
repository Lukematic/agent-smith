from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smith import cli, fair
from smith.enforce import Ledger, TaskClass, adjudicate
from smith.graph import GraphOutcome, run_worker_reviewer_graph
from smith.spawn import Assignment, Role, Runner, SpawnResult, spawn_one

ROOT = Path(__file__).parents[1]


def _result(
    assignment: Assignment,
    output: str = "",
    *,
    outcome: str = "CLAIMED",
    verified: bool | None = None,
) -> SpawnResult:
    return SpawnResult(
        assignment.agent_id,
        outcome,
        0 if outcome in {"CLAIMED", "NO_SIGNAL"} else 1,
        1,
        output,
        claimed_complete=outcome == "CLAIMED",
        verified=verified,
        invocation_id=f"{assignment.agent_id}-invocation",
        stdout_tail=output,
    )


def _marker(verdict: str, feedback: str = "") -> str:
    return "AWINO_REVIEW: " + json.dumps({"verdict": verdict, "feedback": feedback})


@pytest.fixture
def graph_run(tmp_path: Path) -> tuple[Ledger, str]:
    ledger = Ledger(tmp_path / "state")
    run = ledger.open(TaskClass.QUESTION, "build it", opened_by="orchestrator")
    return ledger, run.run_id


def _run(
    graph_run: tuple[Ledger, str],
    tmp_path: Path,
    execute: Callable[[Assignment, Path, Path, Runner], SpawnResult],
    *,
    max_rounds: int = 3,
    verify_worker=None,
    runner: Runner = Runner.CLAUDE,
):
    ledger, run_id = graph_run
    return run_worker_reviewer_graph(
        ledger,
        run_id,
        "implement the feature",
        tmp_path,
        tmp_path,
        runner,
        file_scope=["src/feature.py"],
        worker_verification='python -c "raise SystemExit(0)"',
        confirmed_budget=True,
        max_rounds=max_rounds,
        execute=execute,
        verify_worker=verify_worker
        or (lambda result, _assignment, _project: _mark_verified(result)),
        depth=lambda: 0,
    )


def _mark_verified(result: SpawnResult, value: bool = True) -> SpawnResult:
    result.verified = value
    return result


def test_revise_feedback_reaches_fresh_worker_and_second_reviewer_ships(
    graph_run: tuple[Ledger, str], tmp_path: Path
) -> None:
    assignments: list[Assignment] = []

    def execute(assignment: Assignment, *_args) -> SpawnResult:
        assignments.append(assignment)
        if assignment.role.value == "builder":
            return _result(assignment)
        reviewers = [item for item in assignments if item.role.value == "reviewer"]
        if len(reviewers) == 1:
            return _result(assignment, _marker("REVISE", "handle the empty-input case exactly"))
        return _result(assignment, _marker("SHIP"), outcome="NO_SIGNAL")

    result = _run(graph_run, tmp_path, execute)

    assert result.outcome is GraphOutcome.SHIP
    assert [item.route for item in result.rounds] == [GraphOutcome.REVISE, GraphOutcome.SHIP]
    workers = [item for item in assignments if item.role.value == "builder"]
    reviewers = [item for item in assignments if item.role.value == "reviewer"]
    assert "handle the empty-input case exactly" in workers[1].objective
    assert len({item.agent_id for item in workers}) == 2
    assert len({item.agent_id for item in reviewers}) == 2
    assert not reviewers[0].file_scope
    assert "AWINO_REVIEW:" in reviewers[0].objective

    ledger, run_id = graph_run
    persisted = ledger.load(run_id)
    assert persisted.provenance is not None
    assert persisted.provenance.verified_by == f"{reviewers[1].agent_id}-invocation"
    assert persisted.closed_at is None
    assert adjudicate(persisted, ledger.evidence(run_id)).can_close
    artifacts = ledger.artifacts(run_id)
    reviewer_phases = [
        item
        for item in artifacts
        if item.kind == "graph-phase" and item.payload["node"] == "reviewer"
    ]
    assert len({item.payload["invocation_id"] for item in reviewer_phases}) == 2
    assert len({item.actor for item in reviewer_phases}) == 2
    assert [item.payload["route"] for item in artifacts if item.kind == "graph-route"] == [
        "revise",
        "ship",
    ]
    assert ledger.latest_artifact(run_id, "graph-terminal").payload["outcome"] == "ship"
    assert len(persisted.checkpoints) == 2


@pytest.mark.parametrize("phase", ["worker", "reviewer"])
def test_phase_failure_is_blocked(
    graph_run: tuple[Ledger, str], tmp_path: Path, phase: str
) -> None:
    def execute(assignment: Assignment, *_args) -> SpawnResult:
        if assignment.role.value == phase.replace("worker", "builder"):
            return _result(assignment, outcome="FAILED")
        return _result(assignment)

    result = _run(graph_run, tmp_path, execute)

    assert result.outcome is GraphOutcome.BLOCKED
    assert phase in result.reason
    _assert_blocked_route_persisted(graph_run, result.reason)


def _assert_blocked_route_persisted(graph_run: tuple[Ledger, str], reason: str) -> None:
    ledger, run_id = graph_run
    route = ledger.latest_artifact(run_id, "graph-route")
    assert route is not None
    assert route.payload["route"] == "blocked"
    assert route.payload["feedback"] == reason
    checkpoint = ledger.load(run_id).checkpoints[-1]
    assert checkpoint.phase.startswith("graph-round-")
    assert reason in checkpoint.summary


@pytest.mark.parametrize(
    ("review_output", "reason"),
    [
        ("reviewer-r1 COMPLETE", "structured verdict"),
        ("AWINO_REVIEW: not-json", "structured verdict"),
        (_marker("BLOCKED", "dependency unavailable"), "dependency unavailable"),
    ],
)
def test_missing_malformed_or_blocked_verdict_stops_blocked(
    graph_run: tuple[Ledger, str], tmp_path: Path, review_output: str, reason: str
) -> None:
    def execute(assignment: Assignment, *_args) -> SpawnResult:
        output = review_output if assignment.role.value == "reviewer" else ""
        return _result(assignment, output)

    result = _run(graph_run, tmp_path, execute)

    assert result.outcome is GraphOutcome.BLOCKED
    assert reason in result.reason
    _assert_blocked_route_persisted(graph_run, result.reason)


def test_marker_only_reviewer_stdout_is_accepted(
    graph_run: tuple[Ledger, str], tmp_path: Path
) -> None:
    def execute(assignment: Assignment, *_args) -> SpawnResult:
        if assignment.role is Role.REVIEWER:
            return _result(assignment, _marker("SHIP"), outcome="NO_SIGNAL")
        return _result(assignment)

    result = _run(graph_run, tmp_path, execute)

    assert result.outcome is GraphOutcome.SHIP


def test_unsafe_reviewer_runner_is_refused_before_spawn(
    graph_run: tuple[Ledger, str], tmp_path: Path
) -> None:
    def never(*_args):
        pytest.fail("unsafe runner must not spawn")

    result = _run(graph_run, tmp_path, never, runner=Runner.GOOSE)

    assert result.outcome is GraphOutcome.BLOCKED
    assert "read-only" in result.reason


def test_worker_must_pass_independent_verification_before_review(
    graph_run: tuple[Ledger, str], tmp_path: Path
) -> None:
    assignments: list[Assignment] = []

    def execute(assignment: Assignment, *_args) -> SpawnResult:
        assignments.append(assignment)
        return _result(assignment)

    result = _run(
        graph_run,
        tmp_path,
        execute,
        verify_worker=lambda result, _assignment, _project: _mark_verified(result, False),
    )

    assert result.outcome is GraphOutcome.BLOCKED
    assert "verification" in result.reason
    assert [item.role for item in assignments] == [Role.BUILDER]
    _assert_blocked_route_persisted(graph_run, result.reason)


def test_repeated_revise_exhausts_exact_budget(
    graph_run: tuple[Ledger, str], tmp_path: Path
) -> None:
    calls: list[str] = []

    def execute(assignment: Assignment, *_args) -> SpawnResult:
        calls.append(assignment.agent_id)
        output = _marker("REVISE", "still wrong") if assignment.role.value == "reviewer" else ""
        return _result(assignment, output)

    result = _run(graph_run, tmp_path, execute, max_rounds=3)

    assert result.outcome is GraphOutcome.MAX_ITERATIONS
    assert len(result.rounds) == 3
    assert len(calls) == 6
    ledger, run_id = graph_run
    terminal = ledger.latest_artifact(run_id, "graph-terminal")
    assert terminal is not None
    assert terminal.payload == {"budget": 3, "outcome": "max-iterations", "rounds_used": 3}


def test_unconfirmed_budget_and_nested_execution_are_blocked(
    graph_run: tuple[Ledger, str], tmp_path: Path
) -> None:
    ledger, run_id = graph_run

    def never(*_args):
        pytest.fail("must not spawn")

    unconfirmed = run_worker_reviewer_graph(
        ledger,
        run_id,
        "task",
        tmp_path,
        tmp_path,
        Runner.CLAUDE,
        file_scope=["x.py"],
        worker_verification='python -c "pass"',
        confirmed_budget=False,
        execute=never,
        verify_worker=lambda result, _assignment, _project: result,
        depth=lambda: 0,
    )
    nested = run_worker_reviewer_graph(
        ledger,
        run_id,
        "task",
        tmp_path,
        tmp_path,
        Runner.CLAUDE,
        file_scope=["x.py"],
        worker_verification='python -c "pass"',
        confirmed_budget=True,
        execute=never,
        verify_worker=lambda result, _assignment, _project: result,
        depth=lambda: 1,
    )

    assert unconfirmed.outcome is GraphOutcome.BLOCKED
    assert "budget confirmation" in unconfirmed.reason
    assert nested.outcome is GraphOutcome.BLOCKED
    assert "nested" in nested.reason


@pytest.mark.parametrize("max_rounds", [0, 4])
def test_invalid_round_budget_is_rejected_without_silent_clamping(
    graph_run: tuple[Ledger, str], tmp_path: Path, max_rounds: int
) -> None:
    with pytest.raises(ValueError, match="max_rounds must be between 1 and 3"):
        _run(
            graph_run, tmp_path, lambda *_args: pytest.fail("must not spawn"), max_rounds=max_rounds
        )


def test_cli_refuses_without_explicit_budget_confirmation() -> None:
    result = CliRunner().invoke(cli.app, ["gate", "graph", "--task", "build it"])

    assert result.exit_code != 0
    assert "--confirm-budget" in result.output


def test_cli_reports_exact_confirmed_pair_count_and_rejects_invalid_budget() -> None:
    runner = CliRunner()
    confirmed = runner.invoke(
        cli.app,
        [
            "gate",
            "graph",
            "--task",
            "build it",
            "--verify",
            'python -c "pass"',
            "--scope",
            "x.py",
            "--max-rounds",
            "2",
            "--confirm-budget",
        ],
    )
    invalid = runner.invoke(
        cli.app,
        [
            "gate",
            "graph",
            "--task",
            "build it",
            "--verify",
            'python -c "pass"',
            "--scope",
            "x.py",
            "--max-rounds",
            "4",
            "--confirm-budget",
        ],
    )

    assert "BUDGET_CONFIRMED  pairs=2  subprocesses<=4" in confirmed.output
    assert invalid.exit_code == 2
    assert "max-rounds must be between 1 and 3" in invalid.output


def test_spawn_one_uses_fresh_real_subprocess_invocation_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assignment = Assignment(
        "worker",
        Role.BUILDER,
        "run the subprocess",
        ["x.py"],
        verification='python -c "pass"',
    )

    def command(_runner: Runner, _prompt: Path, *, read_only: bool) -> list[str]:
        del read_only
        return [sys.executable, "-c", 'print("worker COMPLETE")']

    monkeypatch.setattr(Runner, "command", command)
    first = spawn_one(assignment, tmp_path, tmp_path, Runner.CLAUDE)
    second = spawn_one(assignment, tmp_path, tmp_path, Runner.CLAUDE)

    assert first.outcome == second.outcome == "CLAIMED"
    assert first.invocation_id
    assert second.invocation_id
    assert first.invocation_id != second.invocation_id
    prompts = list((tmp_path / ".smith" / "state" / "assignments").glob("worker-*.md"))
    assert len(prompts) == 2
    assert prompts[0].read_bytes() != prompts[1].read_bytes()
    assert not (tmp_path / ".smith" / "assignments").exists()


@pytest.mark.parametrize(
    ("error", "outcome"),
    [(subprocess.TimeoutExpired("agent", 1), "TIMEOUT"), (OSError("cannot start"), "FAILED")],
)
def test_spawn_failure_preserves_invocation_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, outcome: str
) -> None:
    assignment = Assignment(
        "worker",
        Role.BUILDER,
        "time out",
        ["x.py"],
        verification='python -c "pass"',
    )

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    result = spawn_one(assignment, tmp_path, tmp_path, Runner.CLAUDE, timeout=1)

    assert result.outcome == outcome
    assert result.invocation_id.startswith("worker-")


def test_real_subprocess_reviewers_persist_unique_invocation_identities(
    graph_run: tuple[Ledger, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def command(_runner: Runner, _prompt: Path, *, read_only: bool) -> list[str]:
        agent_id = _prompt.stem.rsplit("-", 1)[0]
        output = _marker("REVISE", "one more pass") if read_only else f"{agent_id} COMPLETE"
        if read_only and (tmp_path / "reviewed").exists():
            output = _marker("SHIP")
        script = (
            "from pathlib import Path; "
            f"Path(r'{tmp_path / 'reviewed'}').touch() if {read_only!r} else None; "
            f"print({output!r})"
        )
        return [sys.executable, "-c", script]

    monkeypatch.setattr(shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(Runner, "command", command)
    ledger, run_id = graph_run
    result = run_worker_reviewer_graph(
        ledger,
        run_id,
        "real subprocess identity proof",
        tmp_path,
        tmp_path,
        Runner.CLAUDE,
        file_scope=["x.py"],
        worker_verification='python -c "pass"',
        confirmed_budget=True,
        max_rounds=2,
        depth=lambda: 0,
    )

    assert result.outcome is GraphOutcome.SHIP
    reviewers = [
        item
        for item in ledger.artifacts(run_id, "graph-phase")
        if item.payload["node"] == "reviewer"
    ]
    identities = [item.payload["invocation_id"] for item in reviewers]
    assert len(identities) == 2
    assert len(set(identities)) == 2
    assert all(identity.startswith("reviewer-r") for identity in identities)


def test_specs_directory_has_a_genuine_fair_readme() -> None:
    status = fair.inspect(ROOT / "specs", ROOT)

    assert status.ok
    body = (ROOT / "specs" / "README.md").read_text(encoding="utf-8")
    assert fair.GENERATED_MARKER not in body
    assert fair.STUB_MARKER not in body
    for principle in ("Findable", "Accessible", "Interoperable", "Reusable"):
        assert principle in body
