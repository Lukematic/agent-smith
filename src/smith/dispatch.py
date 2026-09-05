"""Dispatch routing: match a plain-language request to exactly one canonical
skill, deterministically, or state why it cannot.

This is step 1 of the elevator operator's trip (match), kept strictly separate
from steps 3-7 (dispatch, wait, verify, route, record) in ``run_dispatch`` and
the portable ``open_floor`` / ``close_floor`` pair further down this module.
Routing must be safe to call speculatively - with no filesystem write and no
subprocess - so a caller can ask "what would this route to?" before spending any
budget.

No new scoring heuristic is invented here. This reuses SkillCatalog.recommend's
existing ``3 * name_matches + description_matches`` scoring and the existing
concrete-failure-vs-vague-complaint intent override, because a second scoring
system next to the first one is exactly the kind of undirected proliferation this
project's own doctrine warns against.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from smith.enforce import MAX_ATTEMPTS, Ledger
from smith.health import Health, Result, run_all
from smith.paths import SmithPaths
from smith.skill_catalog import Recommendation, Skill, SkillCatalog, _tokens
from smith.spawn import (
    Assignment,
    Role,
    Runner,
    SpawnResult,
    current_depth,
    spawn_one,
)
from smith.spawn import verify as spawn_verify

HealthCheck = Callable[..., list[Result]]
SpawnExecutor = Callable[[Assignment, Path, Path, Runner], SpawnResult]
DispatchVerifier = Callable[[SpawnResult, Assignment, Path], SpawnResult]

# The skill dispatched when independent verification of a floor fails and the
# request itself did not already name a debugging need. Rerouting to a fixed,
# named remediation skill - rather than re-dispatching the same skill again -
# is what makes "route to another floor" mean something.
REMEDIATION_SKILL = "awino-debug"

# Two recommendations within this many points are indistinguishable enough that
# picking one would be a guess, not a match.
_AMBIGUITY_MARGIN = 2


@dataclass(frozen=True)
class DispatchDecision:
    """The result of matching one request against the skill catalog.

    Exactly one of these three shapes holds:
      - confidence == "high": skill is set, alternatives is empty, question is None.
      - confidence == "ambiguous": skill is None, alternatives has >= 2 entries,
        question names the distinction the caller must resolve.
      - confidence == "none": skill is None, alternatives is empty, question asks
        for the concrete detail that was missing.
    """

    request: str
    skill: Skill | None
    alternatives: tuple[Skill, ...]
    confidence: str
    question: str | None
    rationale: str


def _rank_all(request: str, catalog: SkillCatalog) -> list[Recommendation]:
    """Every skill with a nonzero score, sorted best first - the same scoring
    SkillCatalog.recommend uses internally, but returning the full ranked list
    instead of only the winner, since ambiguity detection needs to see the
    runner-up."""
    words = _tokens(request)
    ranked: list[Recommendation] = []
    for skill in catalog.skills:
        name_matches = tuple(sorted(words & _tokens(skill.name)))
        description_matches = tuple(sorted(words & _tokens(skill.description)))
        score = 3 * len(name_matches) + len(description_matches)
        if score:
            ranked.append(Recommendation(skill, score, name_matches, description_matches))
    ranked.sort(key=lambda item: (-item.score, item.skill.precedence, item.skill.name))
    return ranked


def decide(request: str, catalog: SkillCatalog) -> DispatchDecision:
    """Match ``request`` to a skill. Pure: no filesystem write, no subprocess."""
    intent = catalog.recommend(request)
    if intent is not None and intent.score == 100:
        # SkillCatalog.recommend returns score=100 exactly for the concrete
        # intent override (a named failure mode like "pytest"/"ValueError"),
        # which is deliberately unambiguous by construction - it bypasses the
        # ranked list entirely, so there is no runner-up to compare against.
        return DispatchDecision(
            request=request,
            skill=intent.skill,
            alternatives=(),
            confidence="high",
            question=None,
            rationale=f"concrete failure vocabulary matched {intent.skill.name}",
        )

    ranked = _rank_all(request, catalog)
    if not ranked:
        return DispatchDecision(
            request=request,
            skill=None,
            alternatives=(),
            confidence="none",
            question=(
                "That's too general to route. What specifically needs to happen - "
                "a bug to fix, a question to answer, a file to change?"
            ),
            rationale="no skill name or description shares a token with the request",
        )

    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    if runner_up is not None and (best.score - runner_up.score) <= _AMBIGUITY_MARGIN:
        close = [item.skill for item in ranked if best.score - item.score <= _AMBIGUITY_MARGIN]
        names = ", ".join(skill.name for skill in close)
        return DispatchDecision(
            request=request,
            skill=None,
            alternatives=tuple(close),
            confidence="ambiguous",
            question=f"This could be {names}. Which one matches what you actually need?",
            rationale=f"top scores within {_AMBIGUITY_MARGIN} points: {names}",
        )

    return DispatchDecision(
        request=request,
        skill=best.skill,
        alternatives=(),
        confidence="high",
        question=None,
        rationale=f"lexical match score {best.score} on {best.skill.name}",
    )


@dataclass(frozen=True)
class Preflight:
    """Whether it is currently safe to dispatch into this project at all.

    This is the mechanical form of "something is off with you - go to this
    floor first": a precondition check that runs before any capability is
    spawned, entirely from already-existing reads (health.run_all and
    Ledger.inspect_current), so it adds no new source of truth to keep in sync.
    """

    ok: bool
    blockers: tuple[str, ...]
    reroute_to: str | None
    detail: str


def preflight(
    ledger: Ledger,
    paths: SmithPaths,
    *,
    health_check: HealthCheck = run_all,
) -> Preflight:
    """Check project health and the active run's own state before dispatch.

    Pure read: calls ``health_check`` and ``ledger.inspect_current`` only,
    performs no write of its own. A caller may check this speculatively before
    committing to any dispatch.
    """
    results = health_check(paths, fast=True)
    failing = [r for r in results if r.health is Health.FAIL]
    if failing:
        detail = "; ".join(
            f"{r.name}: {r.detail} ({r.remedy})" if r.remedy else f"{r.name}: {r.detail}"
            for r in failing
        )
        return Preflight(
            ok=False,
            blockers=tuple(r.name for r in failing),
            reroute_to=None,
            detail=f"project health failing: {detail}",
        )

    inspected = ledger.inspect_current()
    if inspected.status == "active" and inspected.run is not None:
        run = inspected.run
        pending = next(
            (
                item
                for item in reversed(run.checkpoints)
                if item.pending_decision is not None and item.selected_decision is None
            ),
            None,
        )
        if pending is not None:
            return Preflight(
                ok=False,
                blockers=("pending_decision",),
                reroute_to=None,
                detail=f"active run has an unresolved decision: {pending.pending_decision}",
            )

        failed_gates = sorted(
            {
                item.gate
                for item in ledger.evidence(run.run_id)
                if not item.passed and not item.command.startswith("ATTEST ")
            }
        )
        if failed_gates:
            names = ", ".join(failed_gates)
            return Preflight(
                ok=False,
                blockers=tuple(failed_gates),
                reroute_to="awino-debug",
                detail=(
                    f"active run {run.run_id} has a failing recorded gate "
                    f"({names}); resolve it before dispatching into new work"
                ),
            )

    return Preflight(ok=True, blockers=(), reroute_to=None, detail="preconditions satisfied")


class DispatchOutcome(StrEnum):
    """Every way one dispatch trip may end. No silent fourth state."""

    COMPLETE = "complete"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"
    QUESTION = "question"
    MAX_ITERATIONS = "max-iterations"
    REVISE = "revise"


@dataclass(frozen=True)
class DispatchFloor:
    """One spawned agent's trip: which skill, what it produced, and whether an
    independent check actually confirmed it - not whether it claimed to."""

    number: int
    skill: str
    result: SpawnResult
    verified: bool | None


@dataclass(frozen=True)
class DispatchResult:
    outcome: DispatchOutcome
    floors: tuple[DispatchFloor, ...]
    reason: str
    decision: DispatchDecision | None = None


def _build_assignment(
    task: str,
    skill: str,
    floor_number: int,
    feedback: str | None,
    file_scope: list[str],
    verification: str,
) -> Assignment:
    objective = f"Dispatched to {skill} for: {task}"
    if feedback:
        objective += f"\n\nThe previous floor's independent verification failed with:\n{feedback}"
    return Assignment(
        agent_id=f"dispatch-f{floor_number}-{skill}",
        role=Role.BUILDER,
        objective=objective,
        file_scope=file_scope,
        verification=verification,
    )


def _persist_floor(ledger: Ledger, run_id: str, floor: DispatchFloor, budget: int) -> None:
    ledger.append_artifact(
        run_id,
        "dispatch-floor",
        floor.result.invocation_id or floor.result.agent_id,
        {
            "budget": budget,
            "floor": floor.number,
            "invocation_id": floor.result.invocation_id or floor.result.agent_id,
            "outcome": floor.result.outcome,
            "skill": floor.skill,
            "verified": floor.verified,
        },
    )


def _persist_route(
    ledger: Ledger,
    run_id: str,
    floor_number: int,
    actor: str,
    outcome: DispatchOutcome,
    detail: str,
    budget: int,
) -> None:
    ledger.append_artifact(
        run_id,
        "dispatch-route",
        actor,
        {"budget": budget, "detail": detail, "floor": floor_number, "outcome": outcome.value},
    )
    ledger.checkpoint(
        run_id,
        phase=f"dispatch-floor-{floor_number}",
        summary=f"{outcome.value}: {detail}",
        next_action="finish dispatch"
        if outcome is DispatchOutcome.COMPLETE
        else "route to next floor or stop",
    )


def _terminal(
    ledger: Ledger,
    run_id: str,
    outcome: DispatchOutcome,
    floors: list[DispatchFloor],
    reason: str,
    budget: int,
    decision: DispatchDecision | None = None,
    *,
    floors_used: int | None = None,
) -> DispatchResult:
    """Record the trip's end. ``floors_used`` defaults to the floors seen in this
    process; the portable floor path passes it explicitly because earlier floors
    live only in the ledger."""
    ledger.append_artifact(
        run_id,
        "dispatch-terminal",
        "dispatch-engine",
        {
            "budget": budget,
            "floors_used": len(floors) if floors_used is None else floors_used,
            "outcome": outcome.value,
        },
    )
    return DispatchResult(outcome, tuple(floors), reason, decision)


def run_dispatch(
    ledger: Ledger,
    run_id: str,
    request: str,
    catalog: SkillCatalog,
    paths: SmithPaths,
    smith_home: Path,
    project: Path,
    runner: Runner,
    verification: str,
    *,
    file_scope: list[str],
    confirmed_budget: bool,
    max_floors: int = MAX_ATTEMPTS,
    execute: SpawnExecutor = spawn_one,
    verify_fn: DispatchVerifier = spawn_verify,
    depth: Callable[[], int] = current_depth,
    health_check: HealthCheck = run_all,
) -> DispatchResult:
    """The full trip: match -> confirm -> dispatch -> wait -> verify -> route -> record.

    A completion claim alone never produces COMPLETE. ``verify_fn`` must set
    ``SpawnResult.verified`` to ``True`` for a floor to count as done; ``False``
    reroutes to REMEDIATION_SKILL carrying the exact failure text forward, and
    ``None`` (never checked) reports UNVERIFIED rather than silently trusting
    the claim.
    """
    if not 1 <= max_floors <= MAX_ATTEMPTS:
        raise ValueError(f"max_floors must be between 1 and {MAX_ATTEMPTS}")
    budget = max_floors

    if not confirmed_budget:
        return _terminal(
            ledger,
            run_id,
            DispatchOutcome.BLOCKED,
            [],
            "explicit budget confirmation required",
            budget,
        )
    if depth() >= 1:
        return _terminal(
            ledger,
            run_id,
            DispatchOutcome.BLOCKED,
            [],
            "nested dispatch execution is refused",
            budget,
        )

    decision = decide(request, catalog)
    if decision.confidence != "high":
        return _terminal(
            ledger,
            run_id,
            DispatchOutcome.QUESTION,
            [],
            decision.question or decision.rationale,
            budget,
            decision,
        )
    assert decision.skill is not None

    pre = preflight(ledger, paths, health_check=health_check)
    if not pre.ok:
        return _terminal(ledger, run_id, DispatchOutcome.BLOCKED, [], pre.detail, budget, decision)

    floors: list[DispatchFloor] = []
    feedback: str | None = None
    skill = pre.reroute_to or decision.skill.name
    for number in range(1, budget + 1):
        assignment = _build_assignment(request, skill, number, feedback, file_scope, verification)
        spawned = execute(assignment, smith_home, project, runner)
        spawned = verify_fn(spawned, assignment, project)
        floor = DispatchFloor(number, skill, spawned, spawned.verified)
        floors.append(floor)
        _persist_floor(ledger, run_id, floor, budget)

        if spawned.verified is True:
            _persist_route(
                ledger,
                run_id,
                number,
                spawned.invocation_id or spawned.agent_id,
                DispatchOutcome.COMPLETE,
                "verified",
                budget,
            )
            return _terminal(
                ledger,
                run_id,
                DispatchOutcome.COMPLETE,
                floors,
                f"verified complete on floor {number}",
                budget,
                decision,
            )

        if spawned.verified is None:
            _persist_route(
                ledger,
                run_id,
                number,
                spawned.invocation_id or spawned.agent_id,
                DispatchOutcome.UNVERIFIED,
                "no verification result recorded",
                budget,
            )
            return _terminal(
                ledger,
                run_id,
                DispatchOutcome.UNVERIFIED,
                floors,
                "the floor's completion claim was never independently verified",
                budget,
                decision,
            )

        # spawned.verified is False: reroute, carrying the exact failure forward.
        feedback = spawned.output_tail or "independent verification failed with no detail"
        skill = REMEDIATION_SKILL
        _persist_route(
            ledger,
            run_id,
            number,
            spawned.invocation_id or spawned.agent_id,
            DispatchOutcome.BLOCKED,
            feedback,
            budget,
        )

    return _terminal(
        ledger,
        run_id,
        DispatchOutcome.MAX_ITERATIONS,
        floors,
        f"did not verify within {budget} floor(s)",
        budget,
        decision,
    )


# ── Portable floors: any environment can be the worker ──────────────────────
#
# Phase 0 of the partner spec proved the external-runner assumption wrong on a
# real machine: `claude -p` demands its own login even when a perfectly good
# authenticated agent session is already running. Splitting the trip into
# open_floor / close_floor, with the ledger holding state between calls, lets
# whatever harness is present - Kilo, Claude Code, a human - execute the work.
# The closer re-runs verification itself, so a completion claim alone still
# never produces COMPLETE no matter who the worker was.


@dataclass(frozen=True)
class FloorState:
    """Everything a harness needs to execute one floor of a dispatch trip."""

    run_id: str
    floor: int
    skill: str
    invocation_id: str
    prompt_path: str
    verification: str
    max_floors: int
    role: str = "worker"
    verdict_path: str = ""


@dataclass(frozen=True)
class FloorResult:
    outcome: DispatchOutcome
    detail: str
    next_state: FloorState | None = None


def _skill_text(smith_home: Path, skill: str) -> str:
    path = smith_home / "skills" / skill / "SKILL.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return f"(skill file not found at {path})"


def _write_floor_prompt(
    ledger: Ledger,
    run_id: str,
    request: str,
    skill: str,
    smith_home: Path,
    verification: str,
    file_scope: list[str],
    floor: int,
    max_floors: int,
    feedback: str | None,
) -> FloorState:
    invocation_id = f"dispatch-f{floor}-{skill}-{uuid.uuid4().hex[:10]}"
    assignment = _build_assignment(request, skill, floor, feedback, file_scope, verification)
    prompt = (
        f"<!-- invocation: {invocation_id} -->\n"
        + assignment.render(smith_home)
        + "\n\n## The skill you were routed to - follow this procedure\n\n"
        + _skill_text(smith_home, skill)
    )
    scratch = ledger.state_root / "assignments"
    scratch.mkdir(parents=True, exist_ok=True)
    prompt_path = scratch / f"{invocation_id}.md"
    prompt_path.write_text(prompt, encoding="utf-8", newline="")

    state = FloorState(
        run_id=run_id,
        floor=floor,
        skill=skill,
        invocation_id=invocation_id,
        prompt_path=str(prompt_path),
        verification=verification,
        max_floors=max_floors,
    )
    ledger.append_artifact(
        run_id,
        "dispatch-pending",
        invocation_id,
        {
            "floor": floor,
            "invocation_id": invocation_id,
            "max_floors": max_floors,
            "prompt_path": str(prompt_path),
            "request": request,
            "skill": skill,
            "smith_home": str(smith_home),
            "verification": verification,
            "file_scope": list(file_scope),
        },
    )
    return state


def _changed_files(project: Path, file_scope: list[str]) -> list[str]:
    """What the reviewer should read: the real diff if this is a git checkout,
    the run's declared scope otherwise."""
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
        names = [n.strip() for n in completed.stdout.splitlines() if n.strip()]
        if completed.returncode == 0 and names:
            return names
    except OSError:
        pass
    return list(file_scope)


def _write_review_prompt(
    ledger: Ledger,
    run_id: str,
    request: str,
    smith_home: Path,
    project: Path,
    file_scope: list[str],
    worker_verification: str,
    floor: int,
    max_floors: int,
) -> FloorState:
    """A reviewer floor: role=Role.REVIEWER (mechanically read-only, no scope),
    context is the worker's real diff, and the deliverable is one verdict line
    written under the ledger, never inside the project - a read-only role
    writing into the thing it is reviewing would defeat the whole point."""
    invocation_id = f"dispatch-review-{floor}-{uuid.uuid4().hex[:10]}"
    changed = _changed_files(project, file_scope)
    reviews = ledger.state_root / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    verdict_path = reviews / f"{invocation_id}.json"

    assignment = Assignment(
        agent_id=f"dispatch-review-f{floor}",
        role=Role.REVIEWER,
        objective=(
            f"Independently review the change made for: {request}\n\n"
            "Do not edit anything. Read the changed files listed below and judge "
            "whether the change is correct and complete.\n\n"
            f"Write exactly one line of JSON to {verdict_path} "
            "(not anywhere inside the project): "
            '{"verdict": "SHIP" | "REVISE" | "BLOCKED", "feedback": "<why>"}. '
            "SHIP only if you would accept this as-is. REVISE if it is close but "
            "wrong in a way you can name; put the exact fix needed in feedback. "
            "BLOCKED if you cannot review it at all; say why."
        ),
        file_scope=[],
        context_paths=changed,
        verification=f"python -c \"import json,sys; d=json.load(open(r{str(verdict_path)!r})); sys.exit(0 if d.get('verdict') in ('SHIP','REVISE','BLOCKED') else 1)\"",
    )
    problems = assignment.problems()
    if problems:
        raise ValueError(f"reviewer assignment invalid: {'; '.join(problems)}")

    prompt = (
        f"<!-- invocation: {invocation_id} -->\n"
        + assignment.render(smith_home)
        + "\n\n## The change you were asked to review was verified against\n\n"
        + f"`{worker_verification}`\n"
        + "\n\n## Reviewer procedure\n\n"
        + _skill_text(smith_home, "awino-consult")
    )
    scratch = ledger.state_root / "assignments"
    scratch.mkdir(parents=True, exist_ok=True)
    prompt_path = scratch / f"{invocation_id}.md"
    prompt_path.write_text(prompt, encoding="utf-8", newline="")

    state = FloorState(
        run_id=run_id,
        floor=floor,
        skill="awino-consult",
        invocation_id=invocation_id,
        prompt_path=str(prompt_path),
        verification=assignment.verification,
        max_floors=max_floors,
        role="reviewer",
        verdict_path=str(verdict_path),
    )
    ledger.append_artifact(
        run_id,
        "dispatch-pending",
        invocation_id,
        {
            "floor": floor,
            "invocation_id": invocation_id,
            "max_floors": max_floors,
            "prompt_path": str(prompt_path),
            "request": request,
            "skill": "awino-consult",
            "smith_home": str(smith_home),
            "verification": assignment.verification,
            "file_scope": list(file_scope),
            "role": "reviewer",
            "verdict_path": str(verdict_path),
            "worker_verification": worker_verification,
        },
    )
    return state


def open_floor(
    ledger: Ledger,
    run_id: str,
    request: str,
    catalog: SkillCatalog,
    smith_home: Path,
    verification: str,
    *,
    file_scope: list[str],
    max_floors: int = MAX_ATTEMPTS,
    role: str = "worker",
    project: Path | None = None,
) -> FloorState:
    """Route the request and write the floor-1 prompt for whatever harness is
    present to execute. Raises rather than guessing when routing is not
    unambiguous, and refuses a second concurrent trip on the same run.

    role="reviewer" skips routing entirely: the reviewer's job is fixed
    (independently judge the worker's diff), not chosen from the catalog.
    `project` is required for role="reviewer" so it can read the real diff.
    Floor numbers are shared across worker and reviewer floors on one run -
    close_floor's "already closed" guard counts total closed floors - so a
    reviewer opened after N worker floors starts at N+1, not 1."""
    if not 1 <= max_floors <= MAX_ATTEMPTS:
        raise ValueError(f"max_floors must be between 1 and {MAX_ATTEMPTS}")
    if not verification.strip():
        raise ValueError("a real verification command is required")

    pending = ledger.latest_artifact(run_id, "dispatch-pending")
    closed = ledger.artifacts(run_id, "dispatch-floor")
    if pending is not None and len(closed) < int(pending.payload["floor"]):
        raise ValueError(f"floor {pending.payload['floor']} is already open; close it first")
    next_floor = len(closed) + 1

    if role == "reviewer":
        if project is None:
            raise ValueError("project is required to open a reviewer floor")
        return _write_review_prompt(
            ledger,
            run_id,
            request,
            smith_home,
            project,
            file_scope,
            verification,
            next_floor,
            max_floors,
        )

    decision = decide(request, catalog)
    if decision.confidence != "high" or decision.skill is None:
        raise ValueError(
            f"routing confidence is {decision.confidence}, not high: "
            f"{decision.question or decision.rationale}"
        )

    return _write_floor_prompt(
        ledger,
        run_id,
        request,
        decision.skill.name,
        smith_home,
        verification,
        file_scope,
        next_floor,
        max_floors,
        None,
    )


def _close_review_floor(ledger: Ledger, run_id: str, payload: dict) -> FloorResult:
    floor = int(payload["floor"])
    max_floors = int(payload["max_floors"])
    invocation_id = str(payload["invocation_id"])
    verdict_path = Path(str(payload["verdict_path"]))

    verdict: str | None = None
    feedback = ""
    if verdict_path.is_file():
        try:
            data = json.loads(verdict_path.read_text(encoding="utf-8"))
            candidate = data.get("verdict")
            if candidate in ("SHIP", "REVISE", "BLOCKED"):
                verdict = candidate
                feedback = str(data.get("feedback", ""))
        except (OSError, json.JSONDecodeError):
            verdict = None

    result = SpawnResult(
        agent_id=f"dispatch-review-f{floor}",
        outcome=verdict or "MALFORMED_VERDICT",
        exit_code=0 if verdict else 1,
        duration_ms=0,
        output_tail=feedback,
        claimed_complete=verdict == "SHIP",
        verified=verdict is not None,
        invocation_id=invocation_id,
    )
    _persist_floor(
        ledger,
        run_id,
        DispatchFloor(floor, "awino-consult", result, verdict is not None),
        max_floors,
    )
    ledger.append_artifact(
        run_id,
        "dispatch-review",
        invocation_id,
        {"verdict": verdict or "MALFORMED", "feedback": feedback, "invocation_id": invocation_id},
    )

    if verdict == "SHIP":
        _terminal(ledger, run_id, DispatchOutcome.COMPLETE, [], "", max_floors, floors_used=floor)
        ledger.checkpoint(
            run_id,
            phase=f"dispatch-review-{floor}",
            summary=f"SHIP: {feedback[:100]}",
            next_action="finish dispatch",
        )
        return FloorResult(DispatchOutcome.COMPLETE, f"reviewer SHIP: {feedback}")

    if verdict == "REVISE" and floor < max_floors:
        next_state = _write_floor_prompt(
            ledger,
            run_id,
            str(payload["request"]),
            REMEDIATION_SKILL,
            Path(str(payload["smith_home"])),
            str(payload["worker_verification"]),
            list(payload.get("file_scope", [])),
            floor + 1,
            max_floors,
            feedback,
        )
        ledger.checkpoint(
            run_id,
            phase=f"dispatch-review-{floor}",
            summary=f"revise: {feedback[:100]}",
            next_action=f"execute floor {floor + 1} prompt",
        )
        return FloorResult(DispatchOutcome.REVISE, feedback, next_state)

    outcome = DispatchOutcome.MAX_ITERATIONS if verdict == "REVISE" else DispatchOutcome.BLOCKED
    detail = feedback if verdict else f"no valid verdict at {verdict_path}"
    _terminal(ledger, run_id, outcome, [], detail, max_floors, floors_used=floor)
    ledger.checkpoint(
        run_id,
        phase=f"dispatch-review-{floor}",
        summary=f"{outcome.value}: {detail[:100]}",
        next_action="human decides whether to continue",
    )
    return FloorResult(outcome, detail)


def close_floor(ledger: Ledger, run_id: str, project: Path) -> FloorResult:
    """Verify the pending floor's work by re-running its verification command
    ourselves, then route: complete, open the next floor with the failure text
    carried forward, or stop at the budget.

    A reviewer floor closes differently: there is no shell command to trust,
    only a verdict file the reviewer was told to write. SHIP completes; REVISE
    opens a fresh worker floor carrying the reviewer's exact feedback; BLOCKED
    or a missing/malformed verdict stops rather than guessing."""
    pending = ledger.latest_artifact(run_id, "dispatch-pending")
    closed = ledger.artifacts(run_id, "dispatch-floor")
    if pending is None or len(closed) >= int(pending.payload["floor"]):
        raise ValueError("no pending floor to close on this run")

    payload = pending.payload
    if payload.get("role") == "reviewer":
        return _close_review_floor(ledger, run_id, payload)

    floor = int(payload["floor"])
    max_floors = int(payload["max_floors"])
    verification = str(payload["verification"])
    invocation_id = str(payload["invocation_id"])
    skill = str(payload["skill"])

    started = time.monotonic()
    try:
        from smith.provision import ensure_project_venv, project_env

        ensure_project_venv(project)
        completed = subprocess.run(
            verification,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(project),
            env=project_env(project),
            check=False,
        )
        exit_code = completed.returncode
        tail = "\n".join(
            ((completed.stdout or "") + (completed.stderr or "")).strip().splitlines()[-8:]
        )
    except OSError as exc:
        exit_code = 127
        tail = str(exc)
    verified = exit_code == 0

    # The same DispatchFloor shape run_dispatch persists, so the ledger holds
    # one record format whether the worker was a subprocess or the harness.
    result = SpawnResult(
        agent_id=f"dispatch-f{floor}-{skill}",
        outcome="VERIFIED" if verified else "FAILED_VERIFICATION",
        exit_code=exit_code,
        duration_ms=int((time.monotonic() - started) * 1000),
        output_tail=tail,
        claimed_complete=verified,
        verified=verified,
        invocation_id=invocation_id,
    )
    _persist_floor(ledger, run_id, DispatchFloor(floor, skill, result, verified), max_floors)

    if verified:
        _terminal(
            ledger,
            run_id,
            DispatchOutcome.COMPLETE,
            [],
            "",
            max_floors,
            floors_used=floor,
        )
        ledger.checkpoint(
            run_id,
            phase=f"dispatch-floor-{floor}",
            summary=f"complete: verified on floor {floor}",
            next_action="finish dispatch",
        )
        return FloorResult(DispatchOutcome.COMPLETE, f"verified on floor {floor}")

    if floor >= max_floors:
        _terminal(
            ledger,
            run_id,
            DispatchOutcome.MAX_ITERATIONS,
            [],
            "",
            max_floors,
            floors_used=floor,
        )
        ledger.checkpoint(
            run_id,
            phase=f"dispatch-floor-{floor}",
            summary=f"max-iterations: verification still failing after {floor} floor(s)",
            next_action="human decides whether to continue",
        )
        return FloorResult(
            DispatchOutcome.MAX_ITERATIONS,
            f"verification failed on the final floor {floor}: {tail}",
        )

    feedback = tail or "independent verification failed with no output"
    next_state = _write_floor_prompt(
        ledger,
        run_id,
        str(payload["request"]),
        REMEDIATION_SKILL,
        Path(str(payload["smith_home"])),
        verification,
        list(payload.get("file_scope", [])),
        floor + 1,
        max_floors,
        feedback,
    )
    ledger.checkpoint(
        run_id,
        phase=f"dispatch-floor-{floor}",
        summary=f"revise: {feedback[:120]}",
        next_action=f"execute floor {floor + 1} prompt",
    )
    return FloorResult(DispatchOutcome.REVISE, feedback, next_state)
