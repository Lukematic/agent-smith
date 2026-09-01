"""Bounded worker-reviewer graph with durable routing evidence."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from smith.enforce import MAX_ATTEMPTS, Ledger, ProvenanceGateResult, ReviewVerdict
from smith.spawn import Assignment, Role, Runner, SpawnResult, current_depth, spawn_one, verify

REVIEW_MARKER = "AWINO_REVIEW:"
SpawnExecutor = Callable[[Assignment, Path, Path, Runner], SpawnResult]
WorkerVerifier = Callable[[SpawnResult, Assignment, Path], SpawnResult]


class GraphOutcome(StrEnum):
    SHIP = "ship"
    REVISE = "revise"
    BLOCKED = "blocked"
    MAX_ITERATIONS = "max-iterations"


@dataclass(frozen=True)
class GraphRound:
    number: int
    worker: SpawnResult
    reviewer: SpawnResult | None
    feedback: str
    route: GraphOutcome


@dataclass(frozen=True)
class GraphResult:
    outcome: GraphOutcome
    rounds: list[GraphRound]
    reason: str


def build_worker_assignment(
    task: str,
    round_number: int,
    feedback: str | None,
    file_scope: list[str],
    verification: str,
) -> Assignment:
    objective = f"Implement this task: {task}"
    if feedback:
        objective += f"\n\nThe independent reviewer requested this exact revision:\n{feedback}"
    return Assignment(
        agent_id=f"worker-r{round_number}",
        role=Role.BUILDER,
        objective=objective,
        file_scope=file_scope,
        verification=verification,
    )


def build_reviewer_assignment(task: str, round_number: int, worker: SpawnResult) -> Assignment:
    objective = f"""Independently review this task against the actual project: {task}

The worker invocation was {worker.invocation_id or worker.agent_id}. Do not edit any file.
Emit exactly one final stdout line beginning `{REVIEW_MARKER} ` followed by one JSON object:
{{"verdict":"SHIP|REVISE|BLOCKED","feedback":"actionable text"}}
Use SHIP only when complete, REVISE for actionable defects, and BLOCKED when review cannot proceed.
"""
    return Assignment(
        agent_id=f"reviewer-r{round_number}",
        role=Role.REVIEWER,
        objective=objective,
        verification='python -c "raise SystemExit(0)"',
    )


def parse_review(result: SpawnResult) -> tuple[GraphOutcome | None, str]:
    lines = [line.strip() for line in result.stdout_tail.splitlines()]
    marked = [line for line in lines if line.startswith(REVIEW_MARKER)]
    if len(marked) != 1:
        return None, "reviewer produced no unique structured verdict"
    try:
        payload = json.loads(marked[0][len(REVIEW_MARKER) :].strip())
        verdict = GraphOutcome(str(payload["verdict"]).lower())
        feedback = str(payload.get("feedback", "")).strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, "reviewer produced a malformed structured verdict"
    if verdict not in {GraphOutcome.SHIP, GraphOutcome.REVISE, GraphOutcome.BLOCKED}:
        return None, "reviewer produced an unsupported structured verdict"
    if verdict in {GraphOutcome.REVISE, GraphOutcome.BLOCKED} and not feedback:
        return None, f"{verdict} verdict requires feedback"
    return verdict, feedback


def _persist_phase(
    ledger: Ledger,
    run_id: str,
    round_number: int,
    node: str,
    result: SpawnResult,
    budget: int,
) -> None:
    ledger.append_artifact(
        run_id,
        "graph-phase",
        result.invocation_id or result.agent_id,
        {
            "budget": budget,
            "duration_ms": result.duration_ms,
            "exit_code": result.exit_code,
            "invocation_id": result.invocation_id or result.agent_id,
            "node": node,
            "outcome": result.outcome,
            "output_tail": result.output_tail,
            "round": round_number,
        },
    )


def _terminal(
    ledger: Ledger,
    run_id: str,
    outcome: GraphOutcome,
    rounds: list[GraphRound],
    budget: int,
    reason: str,
) -> GraphResult:
    ledger.append_artifact(
        run_id,
        "graph-terminal",
        "graph-engine",
        {"budget": budget, "outcome": outcome.value, "rounds_used": len(rounds)},
    )
    return GraphResult(outcome, rounds, reason)


def _persist_route(
    ledger: Ledger,
    run_id: str,
    round_number: int,
    route: GraphOutcome,
    feedback: str,
    budget: int,
    actor: str,
) -> None:
    ledger.append_artifact(
        run_id,
        "graph-route",
        actor,
        {
            "budget": budget,
            "feedback": feedback,
            "route": route.value,
            "round": round_number,
        },
    )
    ledger.checkpoint(
        run_id,
        phase=f"graph-round-{round_number}",
        summary=f"worker -> reviewer -> {route.value}: {feedback}",
        next_action="finish graph" if route is GraphOutcome.SHIP else "stop or run next worker",
    )


def _blocked_round(
    ledger: Ledger,
    run_id: str,
    rounds: list[GraphRound],
    budget: int,
    number: int,
    worker: SpawnResult,
    reviewer: SpawnResult | None,
    reason: str,
) -> GraphResult:
    rounds.append(GraphRound(number, worker, reviewer, reason, GraphOutcome.BLOCKED))
    actor_result = reviewer or worker
    _persist_route(
        ledger,
        run_id,
        number,
        GraphOutcome.BLOCKED,
        reason,
        budget,
        actor_result.invocation_id or actor_result.agent_id,
    )
    return _terminal(ledger, run_id, GraphOutcome.BLOCKED, rounds, budget, reason)


def run_worker_reviewer_graph(
    ledger: Ledger,
    run_id: str,
    task: str,
    smith_home: Path,
    project: Path,
    runner: Runner,
    *,
    file_scope: list[str],
    worker_verification: str,
    confirmed_budget: bool,
    max_rounds: int = MAX_ATTEMPTS,
    execute: SpawnExecutor = spawn_one,
    verify_worker: WorkerVerifier = verify,
    depth: Callable[[], int] = current_depth,
) -> GraphResult:
    """Run fresh worker and reviewer subprocess assignments until a terminal route."""
    if not 1 <= max_rounds <= MAX_ATTEMPTS:
        raise ValueError(f"max_rounds must be between 1 and {MAX_ATTEMPTS}")
    budget = max_rounds
    if not confirmed_budget:
        return _terminal(
            ledger,
            run_id,
            GraphOutcome.BLOCKED,
            [],
            budget,
            "explicit budget confirmation required",
        )
    if depth() >= 1:
        return _terminal(
            ledger, run_id, GraphOutcome.BLOCKED, [], budget, "nested graph execution is refused"
        )
    if not runner.enforces_read_only:
        return _terminal(
            ledger,
            run_id,
            GraphOutcome.BLOCKED,
            [],
            budget,
            f"runner {runner} cannot mechanically enforce reviewer read-only access",
        )
    if execute is spawn_one and not runner.available:
        return _terminal(
            ledger, run_id, GraphOutcome.BLOCKED, [], budget, "no agent runner is available"
        )

    rounds: list[GraphRound] = []
    feedback: str | None = None
    for number in range(1, budget + 1):
        worker_assignment = build_worker_assignment(
            task, number, feedback, file_scope, worker_verification
        )
        worker = execute(worker_assignment, smith_home, project, runner)
        _persist_phase(ledger, run_id, number, "worker", worker, budget)
        if worker.outcome != "CLAIMED" or worker.exit_code != 0:
            return _blocked_round(
                ledger, run_id, rounds, budget, number, worker, None, "worker phase failed"
            )
        worker = verify_worker(worker, worker_assignment, project)
        if not worker.trustworthy:
            return _blocked_round(
                ledger,
                run_id,
                rounds,
                budget,
                number,
                worker,
                None,
                "worker independent verification failed",
            )

        reviewer_assignment = build_reviewer_assignment(task, number, worker)
        reviewer = execute(reviewer_assignment, smith_home, project, runner)
        _persist_phase(ledger, run_id, number, "reviewer", reviewer, budget)
        marker_only = reviewer.outcome == "NO_SIGNAL" and reviewer.exit_code == 0
        if not marker_only and (reviewer.outcome != "CLAIMED" or reviewer.exit_code != 0):
            return _blocked_round(
                ledger, run_id, rounds, budget, number, worker, reviewer, "reviewer phase failed"
            )

        route, feedback = parse_review(reviewer)
        selected = route or GraphOutcome.BLOCKED
        rounds.append(GraphRound(number, worker, reviewer, feedback, selected))
        _persist_route(
            ledger,
            run_id,
            number,
            selected,
            feedback,
            budget,
            reviewer.invocation_id or reviewer.agent_id,
        )
        if route is None:
            return _terminal(ledger, run_id, selected, rounds, budget, feedback)
        if route is GraphOutcome.BLOCKED:
            return _terminal(ledger, run_id, route, rounds, budget, feedback)
        if route is GraphOutcome.SHIP:
            ledger.record_provenance(
                run_id,
                verdict=ReviewVerdict.APPROVED,
                gate_results=[
                    ProvenanceGateResult(
                        gate="graph-review", command=reviewer.agent_id, exit_code=reviewer.exit_code
                    )
                ],
                changed_files=file_scope,
                verified_by=reviewer.invocation_id or reviewer.agent_id,
                risks=feedback or None,
            )
            return _terminal(
                ledger, run_id, route, rounds, budget, f"reviewer shipped round {number}"
            )

    return _terminal(
        ledger,
        run_id,
        GraphOutcome.MAX_ITERATIONS,
        rounds,
        budget,
        f"review did not ship within {budget} rounds",
    )
