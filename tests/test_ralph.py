"""Tests for the Ralph loop driver (attempt -> verify -> retry) and its CLI.

Ralph is the "get it truly done" loop: the attempt artifact describes the
work, but only the check command's exit code decides. A failed verification
routes to retry with the failure evidence; the third failure escalates to a
human with a structured report and leaves any linked seed open.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import loops
from awino.cli.loopctl import loop_app
from awino.enforce import Ledger
from awino.seeds import Issue, Seeds, SeedsResult, SeedsState

REPO_ROOT = Path(__file__).resolve().parents[1]
RALPH_SKILL = REPO_ROOT / "skills" / "awino-ralph" / "SKILL.md"

ATTEMPT_OK = """# Attempt: fix the flaky test

## What I changed
Fixed the race in `src/worker.py` by joining the thread before asserting.
See src/worker.py:42 for the join and src/worker.py:87 for the assertion.

## Why
The test flaked when the worker thread had not finished before the assert ran.

## Check
Run the verification command for this loop.
"""


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    return project


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return _project(tmp_path)


@pytest.fixture()
def driver(project: Path, tmp_path: Path) -> loops.RalphDriver:
    return loops.RalphDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=RALPH_SKILL,
    )


@pytest.fixture()
def event_driver(
    project: Path, tmp_path: Path, loop_ledger: Ledger
) -> loops.RalphDriver:
    return loops.RalphDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=RALPH_SKILL,
        ledger=loop_ledger,
    )


@pytest.fixture()
def loop_ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / ".awino")


@pytest.fixture()
def seeds_closed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Seeds mocked READY with an open seed; records every close call."""
    closed: list[tuple[str, str]] = []
    monkeypatch.setattr(Seeds, "state", lambda self: (SeedsState.READY, "ok"))
    monkeypatch.setattr(
        Seeds,
        "show",
        lambda self, issue_id: Issue(
            id=issue_id, title="Fix the flaky test", status="open",
            type="task", priority=2,
        ),
    )

    def fake_close(self, issue_id: str, reason: str) -> SeedsResult:
        closed.append((issue_id, reason))
        return SeedsResult(ok=True, command="sd close", detail="closed", payload=None)

    monkeypatch.setattr(Seeds, "close", fake_close)
    return closed


def _write_attempt(driver: loops.RalphDriver, state: loops.LoopState, text: str) -> None:
    path = driver.project_root / state.ralph_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _at_verify(driver: loops.RalphDriver, check: str = "true") -> loops.LoopState:
    state = driver.new("fix the flaky test", check=check)
    _write_attempt(driver, state, ATTEMPT_OK)
    assert driver.check(state) == []
    assert driver.advance(state) == "verify"
    return driver.load(state.id)


class TestRalphNew:
    def test_new_requires_a_check_command(self, driver: loops.RalphDriver) -> None:
        with pytest.raises(loops.LoopError, match="check"):
            driver.new("fix the flaky test")

    def test_new_stores_check_and_attempt_phase(self, driver: loops.RalphDriver) -> None:
        state = driver.new("fix the flaky test", check="pytest -q")
        assert state.phase == "attempt"
        assert state.check_command == "pytest -q"
        assert state.id.startswith("ralph-")
        assert loops.kind_of(state.id) == "ralph"

    def test_attempt_prompt_comes_from_the_ralph_skill(
        self, driver: loops.RalphDriver
    ) -> None:
        text = loops.skill_section(RALPH_SKILL, "The loop", "awino-ralph")
        assert text.startswith("## The loop")
        assert len(text) > 100

    def test_verify_prompt_comes_from_the_ralph_skill(
        self, driver: loops.RalphDriver
    ) -> None:
        text = loops.skill_section(RALPH_SKILL, "Verification is the skill", "awino-ralph")
        assert text.strip()

    def test_missing_ralph_skill_section_refuses_to_improvise(
        self, driver: loops.RalphDriver, tmp_path: Path
    ) -> None:
        empty = tmp_path / "EMPTY.md"
        empty.write_text("# nothing here\n", encoding="utf-8")
        with pytest.raises(loops.LoopError, match="refusing to invent"):
            loops.skill_section(empty, "The loop", "awino-ralph")


class TestRalphAttemptValidation:
    def test_valid_attempt_passes(self, driver: loops.RalphDriver) -> None:
        state = driver.new("fix the flaky test", check="true")
        _write_attempt(driver, state, ATTEMPT_OK)
        assert driver.check(state) == []

    def test_short_attempt_rejected(self, driver: loops.RalphDriver) -> None:
        state = driver.new("fix the flaky test", check="true")
        _write_attempt(driver, state, "too short\n")
        missing = driver.check(state)
        assert missing
        assert any("too short" in item for item in missing)

    def test_three_failed_attempts_lock_the_loop(
        self, driver: loops.RalphDriver
    ) -> None:
        state = driver.new("fix the flaky test", check="true")
        _write_attempt(driver, state, "too short\n")
        for _ in range(loops.MAX_ATTEMPTS):
            assert driver.check(state)
        state = driver.load(state.id)
        assert state.locked
        with pytest.raises(loops.LoopLocked):
            driver.advance(state)


class TestRalphVerifyPass:
    def test_passing_check_completes_the_loop(
        self, event_driver: loops.RalphDriver
    ) -> None:
        state = _at_verify(event_driver, check="true")
        assert event_driver.advance(state) == "done"
        state = event_driver.load(state.id)
        assert state.phase == "done"
        assert not state.locked

    def test_verify_history_records_command_exit_and_tail(
        self, driver: loops.RalphDriver
    ) -> None:
        state = _at_verify(driver, check="echo hello-verification-output")
        driver.advance(state)
        state = driver.load(state.id)
        assert len(state.verify_history) == 1
        record = state.verify_history[0]
        assert record["command"] == "echo hello-verification-output"
        assert record["exit_code"] == 0
        assert record["outcome"] == "passed"
        assert "hello-verification-output" in record["output_tail"]

    def test_passing_verification_emits_verify_passed(
        self, event_driver: loops.RalphDriver, loop_ledger: Ledger
    ) -> None:
        state = _at_verify(event_driver, check="true")
        event_driver.advance(state)
        kinds = [event.kind for event in loop_ledger.loop_events(state.id)]
        assert "verify_passed" in kinds
        assert kinds[-1] == "loop_closed"

    def test_success_closes_a_linked_seed(
        self, driver: loops.RalphDriver, seeds_closed: list[tuple[str, str]]
    ) -> None:
        state = driver.new("fix the flaky test", check="true", seed_id="seed-1")
        _write_attempt(driver, state, ATTEMPT_OK)
        assert driver.check(state) == []
        assert driver.advance(state) == "verify"
        assert driver.advance(state) == "done"
        assert len(seeds_closed) == 1
        seed_id, reason = seeds_closed[0]
        assert seed_id == "seed-1"
        assert "true" in reason
        assert "exited 0" in reason

    def test_failed_seed_close_leaves_seed_open(
        self, driver: loops.RalphDriver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Seeds, "state", lambda self: (SeedsState.READY, "ok"))
        monkeypatch.setattr(
            Seeds, "show",
            lambda self, issue_id: Issue(
                id=issue_id, title="t", status="open", type="task", priority=2
            ),
        )
        monkeypatch.setattr(
            Seeds, "close",
            lambda self, issue_id, reason: SeedsResult(
                ok=False, command="sd close", detail="boom", payload=None
            ),
        )
        state = driver.new("fix the flaky test", check="true", seed_id="seed-9")
        _write_attempt(driver, state, ATTEMPT_OK)
        driver.advance(state)
        # The loop still completes; only the seed close failed, and the note says so.
        assert driver.advance(state) == "done"
        assert "seed-9" in (driver.completion_seed_note or "")
        assert "NOT closed" in (driver.completion_seed_note or "")


class TestRalphVerifyFailRetry:
    def test_failing_check_routes_to_retry(
        self, event_driver: loops.RalphDriver
    ) -> None:
        state = _at_verify(event_driver, check="false")
        assert event_driver.advance(state) == "retry"
        state = event_driver.load(state.id)
        assert state.phase == "retry"
        assert not state.locked
        assert state.phase != "done"

    def test_failed_verification_emits_verify_failed_with_evidence(
        self, event_driver: loops.RalphDriver, loop_ledger: Ledger
    ) -> None:
        state = _at_verify(event_driver, check="echo some-failure-detail >&2; exit 3")
        event_driver.advance(state)
        events = loop_ledger.loop_events(state.id)
        failed = [e for e in events if e.kind == "verify_failed"]
        assert len(failed) == 1
        assert "exited 3" in failed[0].detail
        assert "some-failure-detail" in failed[0].detail

    def test_retry_prompt_carries_prior_failure_evidence(
        self, driver: loops.RalphDriver
    ) -> None:
        state = _at_verify(driver, check="echo some-failure-detail >&2; exit 3")
        driver.advance(state)
        state = driver.load(state.id)
        prompt = driver.prompt_block(state, "retry")
        assert "some-failure-detail" in prompt
        assert "exited 3" in prompt

    def test_retry_cycles_back_to_verify(self, driver: loops.RalphDriver) -> None:
        state = _at_verify(driver, check="false")
        assert driver.advance(state) == "retry"
        _write_attempt(driver, state, ATTEMPT_OK + "\nSecond try, addressing the failure.\n")
        assert driver.check(state) == []
        # Swap in a passing check to prove the cycle completes.
        state.check_command = "true"
        driver.save(state)
        assert driver.advance(state) == "verify"
        assert driver.advance(state) == "done"

    def test_second_failure_returns_to_retry(self, driver: loops.RalphDriver) -> None:
        state = _at_verify(driver, check="false")
        assert driver.advance(state) == "retry"
        _write_attempt(driver, state, ATTEMPT_OK + "\nAnother attempt.\n")
        assert driver.check(state) == []
        assert driver.advance(state) == "verify"
        assert driver.advance(state) == "retry"
        state = driver.load(state.id)
        assert len(state.verify_history) == 2
        assert not state.locked


class TestRalphEscalation:
    def _three_failures(self, driver: loops.RalphDriver) -> loops.LoopState:
        state = _at_verify(driver, check="echo nope >&2; exit 1")
        assert driver.advance(state) == "retry"
        _write_attempt(driver, state, ATTEMPT_OK + "\nTry two.\n")
        assert driver.check(state) == []
        assert driver.advance(state) == "verify"
        assert driver.advance(state) == "retry"
        _write_attempt(driver, state, ATTEMPT_OK + "\nTry three.\n")
        assert driver.check(state) == []
        assert driver.advance(state) == "verify"
        return driver.load(state.id)

    def test_third_failure_escalates_done_and_locked(
        self, event_driver: loops.RalphDriver
    ) -> None:
        state = self._three_failures(event_driver)
        assert event_driver.advance(state) == "done"
        state = event_driver.load(state.id)
        assert state.phase == "done"
        assert state.locked

    def test_escalation_report_names_evidence_and_next_action(
        self, event_driver: loops.RalphDriver, loop_ledger: Ledger
    ) -> None:
        state = self._three_failures(event_driver)
        event_driver.advance(state)
        events = loop_ledger.loop_events(state.id)
        closed = [e for e in events if e.kind == "loop_closed"]
        assert len(closed) == 1
        detail = closed[0].detail
        assert "RALPH ESCALATION" in detail
        assert "nope" in detail  # the failure evidence, each time
        assert "three times" in detail
        assert "exact next human action" in detail

    def test_escalation_leaves_seed_open(
        self, driver: loops.RalphDriver, seeds_closed: list[tuple[str, str]]
    ) -> None:
        state = driver.new("fix the flaky test", check="false", seed_id="seed-2")
        _write_attempt(driver, state, ATTEMPT_OK)
        driver.advance(state)
        for _ in range(2):
            assert driver.advance(state) == "retry"
            _write_attempt(driver, state, ATTEMPT_OK + "\nAnother try.\n")
            assert driver.check(state) == []
            assert driver.advance(state) == "verify"
        assert driver.advance(state) == "done"
        assert seeds_closed == []  # escalation never closes the seed
        assert "seed-2" in (driver.completion_seed_note or "")
        assert "open" in (driver.completion_seed_note or "").lower()

    def test_locked_loop_refuses_advance(self, driver: loops.RalphDriver) -> None:
        state = self._three_failures(driver)
        driver.advance(state)
        state = driver.load(state.id)
        with pytest.raises(loops.LoopLocked):
            driver.advance(state)

    def test_check_timeout_counts_as_failure(
        self, driver: loops.RalphDriver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as subprocess_mod

        def fake_run(*args, **kwargs):
            raise subprocess_mod.TimeoutExpired(cmd="sleep 30", timeout=300)

        monkeypatch.setattr(loops.subprocess, "run", fake_run)
        state = _at_verify(driver, check="sleep 30")
        assert driver.advance(state) == "retry"
        state = driver.load(state.id)
        record = state.verify_history[0]
        assert record["outcome"] == "failed"
        assert "timed out" in record["error"]


class TestRalphCli:
    @pytest.fixture()
    def cli_env(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setenv("AWINO_PROJECT", str(project))
        return project

    def _artifact_path(self, output: str) -> Path:
        for line in output.splitlines():
            if line.startswith("ARTIFACT"):
                return Path(line.split(None, 1)[1].strip())
        raise AssertionError("no ARTIFACT line in output")

    def test_run_ralph_prints_attempt_prompt(self, cli_env: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            loop_app, ["run", "ralph", "--task", "fix it", "--check", "true"]
        )
        assert result.exit_code == 0, result.output
        assert "LOOP  ralph-" in result.output
        assert "phase: attempt" in result.output
        assert "ARTIFACT" in result.output

    def test_run_ralph_requires_check(self, cli_env: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(loop_app, ["run", "ralph", "--task", "fix it"])
        assert result.exit_code != 0

    def test_next_runs_check_and_completes(self, cli_env: Path) -> None:
        runner = CliRunner()
        created = runner.invoke(
            loop_app, ["run", "ralph", "--task", "fix it", "--check", "true"]
        )
        assert created.exit_code == 0, created.output
        artifact = cli_env / self._artifact_path(created.output)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(ATTEMPT_OK, encoding="utf-8")

        nxt = runner.invoke(loop_app, ["next"])
        assert nxt.exit_code == 0, nxt.output
        assert "ADVANCED  phase=verify" in nxt.output

        done = runner.invoke(loop_app, ["next"])
        assert done.exit_code == 0, done.output
        assert "COMPLETE" in done.output
        assert "verification passed" in done.output

    def test_next_routes_failed_verify_to_retry(self, cli_env: Path) -> None:
        runner = CliRunner()
        created = runner.invoke(
            loop_app, ["run", "ralph", "--task", "fix it", "--check", "false"]
        )
        assert created.exit_code == 0, created.output
        artifact = cli_env / self._artifact_path(created.output)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(ATTEMPT_OK, encoding="utf-8")

        assert runner.invoke(loop_app, ["next"]).exit_code == 0
        routed = runner.invoke(loop_app, ["next"])
        assert routed.exit_code == 0, routed.output
        assert "ADVANCED  phase=retry" in routed.output

    def test_status_shows_ralph_kind_and_next(self, cli_env: Path) -> None:
        runner = CliRunner()
        created = runner.invoke(
            loop_app, ["run", "ralph", "--task", "fix it", "--check", "true"]
        )
        assert created.exit_code == 0, created.output
        status = runner.invoke(loop_app, ["status"])
        assert status.exit_code == 0, status.output
        assert "kind: ralph" in status.output
        assert "phase: attempt" in status.output
        assert "next:" in status.output
