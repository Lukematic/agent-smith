"""Real regression for the loop engine: the automated re-run + completion
check the user's own repeated-manual-retesting incident named as missing.

MAX_ATTEMPTS in enforce.py is a real brake, but before this module nothing
automatically re-invoked a failing command; a human had to choose to retry
every single time this whole session. These tests use a genuinely stateful
Python one-liner (a counter file on disk) so "fails twice, passes third
time" is real subprocess behavior, not a mocked return value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from smith.enforce import Gate, Ledger, LedgerError, TaskClass
from smith.loop import LoopOutcome, run_loop, run_with_verification


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path)


def _flaky_command(counter_file: Path, succeed_on_attempt: int) -> str:
    """A real command whose exit code genuinely depends on how many times
    it has already been invoked - not a canned mock."""
    return (
        f'"{sys.executable}" -c "'
        f"import pathlib; p = pathlib.Path(r'{counter_file}'); "
        f"n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); "
        f'raise SystemExit(0 if n >= {succeed_on_attempt} else 1)"'
    )


class TestRunLoop:
    def test_a_command_that_fails_twice_then_passes_ships_on_attempt_three(
        self, ledger: Ledger, tmp_path: Path
    ) -> None:
        run = ledger.open(TaskClass.BUGFIX, "fix the flaky check")
        counter = tmp_path / "counter.txt"
        command = _flaky_command(counter, succeed_on_attempt=3)

        result = run_loop(ledger, run.run_id, Gate.TESTED, lambda _n: command, max_iterations=3)

        assert result.outcome is LoopOutcome.SHIPPED
        assert len(result.iterations) == 3
        assert [it.passed for it in result.iterations] == [False, False, True]
        # This is the real proof the loop, not a human, re-ran it: three
        # genuinely distinct subprocess invocations recorded as evidence.
        assert len(ledger.evidence(run.run_id)) == 3

    def test_a_command_that_never_passes_stops_at_the_cap_and_reports_blocked(
        self, ledger: Ledger, tmp_path: Path
    ) -> None:
        run = ledger.open(TaskClass.BUGFIX, "fix the always-broken check")
        command = f'"{sys.executable}" -c "raise SystemExit(1)"'

        result = run_loop(ledger, run.run_id, Gate.TESTED, lambda _n: command, max_iterations=3)

        assert result.outcome is LoopOutcome.MAX_ITERATIONS
        assert len(result.iterations) == 3
        assert all(not it.passed for it in result.iterations)
        # Never silently claims success at the cap.
        assert "did not pass" in result.reason

    def test_command_fn_returning_none_reports_blocked_without_running_anything(
        self, ledger: Ledger
    ) -> None:
        run = ledger.open(TaskClass.BUGFIX, "nothing left to try")
        result = run_loop(ledger, run.run_id, Gate.TESTED, lambda _n: None, max_iterations=3)

        assert result.outcome is LoopOutcome.BLOCKED
        assert result.iterations == []

    def test_the_fourth_identical_attempt_is_structurally_refused_not_looped_past(
        self, ledger: Ledger
    ) -> None:
        # THREE_STRIKES already exists in enforce.py; the loop must respect
        # it as a real refusal, not swallow it and keep going past the cap.
        run = ledger.open(TaskClass.BUGFIX, "fix a genuinely stuck problem")
        command = f'"{sys.executable}" -c "raise SystemExit(1)"'
        for _ in range(3):
            ledger.record(run.run_id, Gate.TESTED, command)

        with pytest.raises(LedgerError, match="THREE_STRIKES"):
            ledger.record(run.run_id, Gate.TESTED, command)

        result = run_loop(ledger, run.run_id, Gate.TESTED, lambda _n: command, max_iterations=1)
        assert result.outcome is LoopOutcome.BLOCKED
        assert "THREE_STRIKES" in result.reason


class TestRunWithVerification:
    """The 'Open Loop' anti-pattern fix: SHIPPED requires a genuinely
    distinct verifier's agreement, not just the loop's own last passing
    command."""

    def test_shipped_only_when_the_independent_verifier_agrees(
        self, ledger: Ledger, tmp_path: Path
    ) -> None:
        run = ledger.open(TaskClass.BUGFIX, "fix it")
        counter = tmp_path / "counter.txt"
        command = _flaky_command(counter, succeed_on_attempt=1)

        result = run_with_verification(
            ledger,
            run.run_id,
            Gate.TESTED,
            lambda _n: command,
            verify_fn=lambda _r: True,
            max_iterations=3,
        )
        assert result.outcome is LoopOutcome.SHIPPED

    def test_a_rejecting_verifier_downgrades_a_passing_loop_to_blocked(
        self, ledger: Ledger, tmp_path: Path
    ) -> None:
        # This is the exact self-grading gap the container-detection
        # incident exposed: the underlying command genuinely passed, but an
        # independent check disagrees, and that disagreement must win.
        run = ledger.open(TaskClass.BUGFIX, "fix it")
        counter = tmp_path / "counter.txt"
        command = _flaky_command(counter, succeed_on_attempt=1)

        result = run_with_verification(
            ledger,
            run.run_id,
            Gate.TESTED,
            lambda _n: command,
            verify_fn=lambda _r: False,
            max_iterations=3,
        )
        assert result.outcome is LoopOutcome.BLOCKED
        assert "OPEN_LOOP" in result.reason

    def test_verifier_is_never_consulted_when_the_loop_never_ships(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.BUGFIX, "fix it")
        command = f'"{sys.executable}" -c "raise SystemExit(1)"'
        calls: list[bool] = []

        result = run_with_verification(
            ledger,
            run.run_id,
            Gate.TESTED,
            lambda _n: command,
            verify_fn=lambda r: calls.append(True) or True,
            max_iterations=2,
        )
        assert result.outcome is LoopOutcome.MAX_ITERATIONS
        assert calls == []
