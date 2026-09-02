"""The dispatch execution loop: match -> confirm -> dispatch -> wait -> verify ->
route -> record, in one call.

Steps 1-2 (match, confirm) reuse ``dispatch.decide`` and ``dispatch.preflight``
directly rather than re-implementing them, so a change to routing or
preconditions cannot silently diverge from what the loop actually enforces.

The load-bearing distinction this suite protects: a completion *claim* is not
verified success. ``SpawnResult.verified`` has three states -
``True`` (independently confirmed), ``False`` (independently refuted), and
``None`` (never checked, because no verification command existed) - and each
must produce a different, honestly-labeled outcome. Collapsing ``None`` into
``True`` is exactly the self-grading gap the loop exists to close.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from smith.dispatch import (
    REMEDIATION_SKILL,
    DispatchOutcome,
    run_dispatch,
)
from smith.enforce import Ledger, TaskClass
from smith.health import Health, Result
from smith.paths import SmithPaths
from smith.skill_catalog import SkillCatalog
from smith.spawn import Assignment, Runner, SpawnResult, spawn_one
from smith.spawn import verify as spawn_verify

SMITH_ROOT = Path(__file__).resolve().parents[1]

# Verified in tests/test_dispatch_routing.py to score unambiguously high against
# the real bundled catalog, and to a skill that is not the remediation target -
# so a reroute in these tests is genuinely a different floor.
_DELEGATE_REQUEST = (
    "we have parallel independent workstreams that need disjoint file "
    "ownership and should run simultaneously with the orchestrator "
    "coordinating rather than one agent doing everything"
)
_NO_MATCH_REQUEST = "xyzzy plugh wibble"


def _healthy(_paths: SmithPaths, *, fast: bool = False) -> list[Result]:
    del fast
    return [Result("uv_env", Health.OK, "fine")]


def _unhealthy(_paths: SmithPaths, *, fast: bool = False) -> list[Result]:
    del fast
    return [
        Result("clone_freshness", Health.FAIL, "uncommitted tracked changes", remedy="git pull")
    ]


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "state")


@pytest.fixture
def run_id(ledger: Ledger) -> str:
    return ledger.open(TaskClass.QUESTION, "dispatch trip").run_id


@pytest.fixture
def paths(tmp_path: Path) -> SmithPaths:
    root = tmp_path / "smith-home"
    root.mkdir()
    (root / "plugin.json").write_text("{}", encoding="utf-8")
    (root / "knowledge").mkdir()
    return SmithPaths(root=root)


@pytest.fixture
def catalog() -> SkillCatalog:
    return SkillCatalog(
        project_root=Path("/nonexistent-project-root-for-dispatch-loop-test"),
        global_root=Path("/nonexistent-global-root-for-dispatch-loop-test"),
        bundled_root=SMITH_ROOT / "skills",
    )


def _result(
    assignment: Assignment,
    *,
    outcome: str = "CLAIMED",
    verified: bool | None = None,
    output_tail: str = "",
    invocation_id: str | None = None,
) -> SpawnResult:
    return SpawnResult(
        assignment.agent_id,
        outcome,
        0 if outcome == "CLAIMED" else 1,
        1,
        output_tail,
        claimed_complete=outcome == "CLAIMED",
        verified=verified,
        invocation_id=invocation_id or f"{assignment.agent_id}-inv",
        stdout_tail=output_tail,
    )


def _run(
    ledger: Ledger,
    run_id: str,
    catalog: SkillCatalog,
    paths: SmithPaths,
    tmp_path: Path,
    *,
    request: str = _DELEGATE_REQUEST,
    execute,
    verify_fn,
    health_check=_healthy,
    confirmed_budget: bool = True,
    depth=lambda: 0,
    max_floors: int = 3,
):
    return run_dispatch(
        ledger,
        run_id,
        request,
        catalog,
        paths,
        tmp_path,
        tmp_path,
        Runner.CLAUDE,
        'python -c "raise SystemExit(0)"',
        file_scope=["x.py"],
        confirmed_budget=confirmed_budget,
        max_floors=max_floors,
        execute=execute,
        verify_fn=verify_fn,
        depth=depth,
        health_check=health_check,
    )


class TestHappyPath:
    def test_a_verified_success_produces_complete_with_recorded_artifacts(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        def execute(assignment, *_args):
            return _result(assignment)

        def verify_fn(result, _assignment, _project):
            result.verified = True
            return result

        result = _run(
            ledger, run_id, catalog, paths, tmp_path, execute=execute, verify_fn=verify_fn
        )

        assert result.outcome is DispatchOutcome.COMPLETE
        assert len(result.floors) == 1
        assert result.floors[0].verified is True

        floor_artifacts = ledger.artifacts(run_id, "dispatch-floor")
        route_artifacts = ledger.artifacts(run_id, "dispatch-route")
        terminal_artifacts = ledger.artifacts(run_id, "dispatch-terminal")
        assert len(floor_artifacts) == 1
        assert len(route_artifacts) == 1
        assert len(terminal_artifacts) == 1
        assert terminal_artifacts[0].payload["outcome"] == "complete"


class TestUnverifiedClaimIsNotAccepted:
    def test_a_claim_with_no_verification_command_result_is_unverified_not_complete(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        def execute(assignment, *_args):
            return _result(assignment)

        def verify_fn(result, _assignment, _project):
            # verified stays None: spawn.verify's real behavior when the
            # assignment carries no verification command at all.
            return result

        result = _run(
            ledger, run_id, catalog, paths, tmp_path, execute=execute, verify_fn=verify_fn
        )

        assert result.outcome is DispatchOutcome.UNVERIFIED
        assert len(result.floors) == 1
        assert result.floors[0].verified is None


class TestVerificationFailureReroutesToRemediation:
    def test_a_failed_verification_reroutes_with_the_exact_failure_text_carried_forward(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        assignments: list[Assignment] = []

        def execute(assignment, *_args):
            assignments.append(assignment)
            return _result(assignment)

        def verify_fn(result, _assignment, _project):
            if len(assignments) == 1:
                result.verified = False
                result.output_tail = "boom: the exact reason this floor failed"
            else:
                result.verified = True
            return result

        result = _run(
            ledger, run_id, catalog, paths, tmp_path, execute=execute, verify_fn=verify_fn
        )

        assert result.outcome is DispatchOutcome.COMPLETE
        assert len(result.floors) == 2
        assert result.floors[0].skill != REMEDIATION_SKILL
        assert result.floors[1].skill == REMEDIATION_SKILL
        assert "boom: the exact reason this floor failed" in assignments[1].objective


class TestAmbiguousOrEmptyRequestAsksAQuestionAndSpawnsNothing:
    def test_a_request_matching_no_skill_produces_question_with_no_spawn(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def execute(assignment, *_args):
            calls.append(assignment.agent_id)
            pytest.fail("must not spawn on a QUESTION outcome")

        def verify_fn(*_args):
            pytest.fail("must not verify what was never spawned")

        result = _run(
            ledger,
            run_id,
            catalog,
            paths,
            tmp_path,
            request=_NO_MATCH_REQUEST,
            execute=execute,
            verify_fn=verify_fn,
        )

        assert result.outcome is DispatchOutcome.QUESTION
        assert result.floors == ()
        assert calls == []


class TestPreflightBlockerPreventsAnySpawn:
    def test_an_unhealthy_project_blocks_before_dispatching(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def execute(assignment, *_args):
            calls.append(assignment.agent_id)
            pytest.fail("must not spawn when preflight blocks")

        def verify_fn(*_args):
            pytest.fail("must not verify what was never spawned")

        result = _run(
            ledger,
            run_id,
            catalog,
            paths,
            tmp_path,
            execute=execute,
            verify_fn=verify_fn,
            health_check=_unhealthy,
        )

        assert result.outcome is DispatchOutcome.BLOCKED
        assert result.floors == ()
        assert calls == []


class TestIterationCapIsRespected:
    def test_repeated_verification_failure_exhausts_the_exact_budget(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def execute(assignment, *_args):
            calls.append(assignment.agent_id)
            return _result(assignment)

        def verify_fn(result, _assignment, _project):
            result.verified = False
            result.output_tail = "still wrong"
            return result

        result = _run(
            ledger,
            run_id,
            catalog,
            paths,
            tmp_path,
            execute=execute,
            verify_fn=verify_fn,
            max_floors=3,
        )

        assert result.outcome is DispatchOutcome.MAX_ITERATIONS
        assert len(result.floors) == 3
        assert len(calls) == 3


class TestNestedInvocationIsRefused:
    def test_a_nested_dispatch_call_is_blocked_before_any_routing_decision(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        def execute(*_args):
            pytest.fail("must not spawn from inside a nested invocation")

        def verify_fn(*_args):
            pytest.fail("must not verify from inside a nested invocation")

        result = _run(
            ledger,
            run_id,
            catalog,
            paths,
            tmp_path,
            execute=execute,
            verify_fn=verify_fn,
            depth=lambda: 1,
        )

        assert result.outcome is DispatchOutcome.BLOCKED
        assert "nested" in result.reason


class TestUnconfirmedBudgetIsRefused:
    def test_unconfirmed_budget_is_blocked_before_any_routing_decision(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        def execute(*_args):
            pytest.fail("must not spawn without confirmed budget")

        def verify_fn(*_args):
            pytest.fail("must not verify without confirmed budget")

        result = _run(
            ledger,
            run_id,
            catalog,
            paths,
            tmp_path,
            execute=execute,
            verify_fn=verify_fn,
            confirmed_budget=False,
        )

        assert result.outcome is DispatchOutcome.BLOCKED
        assert "budget" in result.reason


class TestDistinctInvocationIdentitiesArePersisted:
    def test_each_floor_in_a_reroute_trip_has_a_distinct_persisted_invocation_id(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, paths: SmithPaths, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def execute(assignment, *_args):
            calls.append(assignment.agent_id)
            return _result(assignment, invocation_id=f"{assignment.agent_id}-{len(calls)}")

        def verify_fn(result, _assignment, _project):
            result.verified = len(calls) >= 2
            result.output_tail = "not yet" if not result.verified else ""
            return result

        _run(ledger, run_id, catalog, paths, tmp_path, execute=execute, verify_fn=verify_fn)

        floor_artifacts = ledger.artifacts(run_id, "dispatch-floor")
        invocation_ids = {item.payload["invocation_id"] for item in floor_artifacts}
        assert len(floor_artifacts) == 2
        assert len(invocation_ids) == 2


class TestRealSubprocessInvocationsAreDistinct:
    def test_two_real_subprocess_floors_produce_two_distinct_persisted_invocation_ids(
        self,
        ledger: Ledger,
        run_id: str,
        catalog: SkillCatalog,
        paths: SmithPaths,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        counter_file = tmp_path / "verify-counter.txt"
        verify_command = (
            f'"{sys.executable}" -c "'
            f"import pathlib; p = pathlib.Path(r'{counter_file}'); "
            f"n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); "
            f'raise SystemExit(0 if n >= 2 else 1)"'
        )

        def command(_runner: Runner, prompt_file: Path, *, read_only: bool) -> list[str]:
            del read_only
            first_line = prompt_file.read_text(encoding="utf-8").splitlines()[0]
            agent_id = first_line.removeprefix("# Assignment: ").strip()
            return [sys.executable, "-c", f'print("{agent_id} COMPLETE")']

        monkeypatch.setattr(Runner, "command", command)

        result = run_dispatch(
            ledger,
            run_id,
            _DELEGATE_REQUEST,
            catalog,
            paths,
            tmp_path,
            tmp_path,
            Runner.CLAUDE,
            verify_command,
            file_scope=["x.py"],
            confirmed_budget=True,
            max_floors=3,
            execute=spawn_one,
            verify_fn=spawn_verify,
            depth=lambda: 0,
            health_check=_healthy,
        )

        assert result.outcome is DispatchOutcome.COMPLETE
        assert len(result.floors) == 2

        floor_artifacts = ledger.artifacts(run_id, "dispatch-floor")
        invocation_ids = [item.payload["invocation_id"] for item in floor_artifacts]
        assert len(invocation_ids) == 2
        assert len(set(invocation_ids)) == 2
        assert all(invocation_ids)
