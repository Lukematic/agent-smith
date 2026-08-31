"""The loop engine: automated re-run + automated completion check.

Per the book's own framing (chapters/9-mental-models/8-loop-engineering.md,
fetched and grounding this module): a harness is one pass. A loop decides
whether to run that pass again. Before this module, nothing in A.W.I.N.O.
did that decision automatically - MAX_ATTEMPTS in enforce.py is a real
brake, but every re-run was a human or agent CHOOSING to re-invoke
`gate record` by hand. An agent that stops asking for tools has ended its
turn, which is not the same as finishing the task; this module is the
thing that keeps running passes until the task's own goal is met or the
budget is exhausted - never claiming success on the loop's own say-so.

This is deliberately built on the primitives that already exist rather
than a parallel state machine: Ledger.record() is the harness pass,
Verdict.can_close (via adjudicate()) is the automated completion check,
and MAX_ATTEMPTS is the existing budget cap this loop respects rather
than reimplementing.

Named per the book's own "Open Loop" anti-pattern: a self-directing loop
with no verifier strong enough to gate its output is the failure mode
this exists to prevent. A LoopRunner never returns SHIPPED on the
strength of its own last passing command alone if a distinct verify_fn
is supplied - see run_with_verification().
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from smith.enforce import Gate, Ledger, LedgerError


class LoopOutcome(StrEnum):
    """The only three ways a loop may end. Mirrors TerminalState's own
    discipline: a loop cannot drift into "done" by omission."""

    SHIPPED = "shipped"
    BLOCKED = "blocked"
    MAX_ITERATIONS = "max-iterations"


@dataclass(frozen=True)
class LoopIteration:
    """One pass of the loop, with the real evidence it produced."""

    number: int
    command: str
    exit_code: int
    passed: bool


@dataclass(frozen=True)
class LoopResult:
    outcome: LoopOutcome
    iterations: list[LoopIteration]
    reason: str


def run_loop(
    ledger: Ledger,
    run_id: str,
    gate: Gate,
    command_fn: Callable[[int], str | None],
    *,
    max_iterations: int = 3,
    cwd: Path | None = None,
) -> LoopResult:
    """Automatically re-invoke ``gate record`` until the gate passes, the
    caller has no next command to try, or ``max_iterations`` is spent.

    ``command_fn(iteration_number)`` returns the next command to attempt,
    or ``None`` to signal there is nothing left to try (a real BLOCKED
    condition, not a silent stop). This is the automation your own retries
    this session were standing in for by hand: the loop, not a human,
    decides whether to run the harness pass again.
    """
    iterations: list[LoopIteration] = []
    for number in range(1, max_iterations + 1):
        command = command_fn(number)
        if command is None:
            return LoopResult(
                LoopOutcome.BLOCKED,
                iterations,
                "command_fn returned no further command to try",
            )
        try:
            evidence = ledger.record(run_id, gate, command, cwd=cwd)
        except LedgerError as exc:
            # THREE_STRIKES or a plan-validity refusal from the harness
            # itself - the loop must stop, not swallow the ledger's own
            # refusal and keep going.
            return LoopResult(LoopOutcome.BLOCKED, iterations, str(exc))
        iterations.append(LoopIteration(number, command, evidence.exit_code, evidence.passed))
        if evidence.passed:
            return LoopResult(LoopOutcome.SHIPPED, iterations, f"{gate} passed on attempt {number}")
    return LoopResult(
        LoopOutcome.MAX_ITERATIONS,
        iterations,
        f"{gate} did not pass within {max_iterations} iteration(s)",
    )


def run_with_verification(
    ledger: Ledger,
    run_id: str,
    gate: Gate,
    command_fn: Callable[[int], str | None],
    verify_fn: Callable[[LoopResult], bool],
    *,
    max_iterations: int = 3,
    cwd: Path | None = None,
) -> LoopResult:
    """``run_loop`` plus the "Open Loop" fix: SHIPPED is only reported if a
    genuinely distinct ``verify_fn`` also confirms it, not on the strength
    of the loop's own last passing command alone.

    ``verify_fn`` must be backed by something with a real, checkable
    difference from ``command_fn`` - a fresh subprocess, a separate
    reviewer identity via record_provenance's verified_by check, or a
    distinct verification command entirely. Passing a verify_fn that is
    just "check the same command's own exit code again" does not close
    the self-grading gap this exists to close; it is the caller's
    responsibility to supply real independence, the same way gate review
    refuses when verified_by matches opened_by rather than trusting the
    caller to have been honest about who is checking.
    """
    result = run_loop(ledger, run_id, gate, command_fn, max_iterations=max_iterations, cwd=cwd)
    if result.outcome is not LoopOutcome.SHIPPED:
        return result
    if not verify_fn(result):
        return LoopResult(
            LoopOutcome.BLOCKED,
            result.iterations,
            f"{gate} passed but independent verification rejected it - OPEN_LOOP guard fired",
        )
    return result
