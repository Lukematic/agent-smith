"""`awino auto`: the bounded Seed driver.

Dispatch chains floors within one task; auto chains Seeds within one sitting.
Per Seed: gate open -> floor open -> the present harness executes -> floor
close (independent verification) -> gates -> gate close -> Seed closed. The
loop stops itself at --max-seeds, on the first Seed that does not complete, or
when nothing is ready - a stuck Seed is a human decision, not something to
skip past.

Honest scope: this is one command driving a bounded loop, not a scheduler.
There is no trigger and no unattended operation; a human starts every sitting
and confirms the budget. Claiming more would be UNGROUNDED_CAPABILITY.

Everything here is composition: seeds.Seeds for ready work, dispatch floors
for execution, the ledger for evidence. The only new logic is the loop and its
stopping rules, which is exactly why it is testable with an injected executor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from smith.dispatch import DispatchOutcome, FloorResult, FloorState, close_floor, open_floor
from smith.enforce import CONTRACTS, MAX_ATTEMPTS, Gate, Ledger, TaskClass
from smith.seeds import Issue, Seeds
from smith.skill_catalog import SkillCatalog

# A worker callback: given the floor state, do the work. Returns nothing; the
# closer verifies. In production this is the present harness (a subagent, a
# human); in tests it is a function that edits files.
Worker = Callable[[FloorState], None]


@dataclass(frozen=True)
class SeedResult:
    issue_id: str
    title: str
    outcome: str  # closed | blocked | refused
    detail: str


@dataclass(frozen=True)
class AutoResult:
    seeds: tuple[SeedResult, ...]
    stopped_because: str


def _task_class_for(issue: Issue) -> TaskClass:
    return {
        "bug": TaskClass.BUGFIX,
        "feature": TaskClass.CODE_CHANGE,
        "task": TaskClass.CODE_CHANGE,
        "epic": TaskClass.CODE_CHANGE,
    }.get(issue.type, TaskClass.CODE_CHANGE)


def _requires_plan(task_class: TaskClass) -> bool:
    return Gate.PLANNED in CONTRACTS[task_class]


def run_auto(
    ledger: Ledger,
    tracker: Seeds,
    catalog: SkillCatalog,
    smith_home: Path,
    project: Path,
    worker: Worker,
    verification: str,
    *,
    max_seeds: int,
    confirmed_budget: bool,
    max_floors: int = MAX_ATTEMPTS,
) -> AutoResult:
    """Drive ready Seeds through floors until done, blocked, or out of budget."""
    if max_seeds < 1:
        raise ValueError("max_seeds must be at least 1")
    if not confirmed_budget:
        return AutoResult((), "explicit budget confirmation required")

    results: list[SeedResult] = []
    for _ in range(max_seeds):
        ready = [i for i in tracker.ready() if i.status == "open"]
        if not ready:
            return AutoResult(tuple(results), "no ready seeds")
        issue = ready[0]
        task_class = _task_class_for(issue)

        if _requires_plan(task_class) and "plan approved" not in {
            label.lower() for label in issue.labels
        }:
            results.append(
                SeedResult(
                    issue.id,
                    issue.title,
                    "refused",
                    f"{task_class} requires an approved plan; label it 'plan approved' "
                    "after a human reviews one",
                )
            )
            return AutoResult(tuple(results), "seed requires a human-approved plan")

        run = ledger.open(task_class, issue.title, issue_id=issue.id)
        request = f"{issue.title}. {issue.description}".strip()
        try:
            state = open_floor(
                ledger,
                run.run_id,
                request,
                catalog,
                smith_home,
                verification,
                file_scope=["(seed-scoped)"],
                max_floors=max_floors,
            )
        except ValueError as exc:
            results.append(SeedResult(issue.id, issue.title, "blocked", str(exc)))
            return AutoResult(tuple(results), "routing could not proceed")

        outcome: FloorResult | None = None
        while True:
            worker(state)
            outcome = close_floor(ledger, run.run_id, project)
            if outcome.outcome is DispatchOutcome.COMPLETE:
                break
            if outcome.next_state is None:
                break
            state = outcome.next_state

        if outcome.outcome is not DispatchOutcome.COMPLETE:
            ledger.checkpoint(
                run.run_id,
                phase="auto-blocked",
                summary=f"seed {issue.id} did not verify: {outcome.detail[:120]}",
                next_action="human decides whether to continue",
                pending_decision=f"Seed {issue.id} stopped ({outcome.outcome}). Continue, rework, or drop?",
                options=["continue", "rework", "drop"],
            )
            results.append(SeedResult(issue.id, issue.title, "blocked", outcome.detail))
            return AutoResult(tuple(results), "seed did not complete; human decision pending")

        tracker.close(issue.id, f"auto: verified complete in run {run.run_id}")
        results.append(SeedResult(issue.id, issue.title, "closed", f"run {run.run_id}"))

    return AutoResult(tuple(results), "max seeds reached")
