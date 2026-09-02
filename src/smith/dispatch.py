"""Dispatch routing: match a plain-language request to exactly one canonical
skill, deterministically, or state why it cannot.

This is step 1 of the elevator operator's trip (match), kept strictly separate
from steps 3-7 (dispatch, wait, verify, route, record) in ``smith.dispatch_loop``.
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

from collections.abc import Callable
from dataclasses import dataclass

from smith.enforce import Ledger
from smith.health import Health, Result, run_all
from smith.paths import SmithPaths
from smith.skill_catalog import Recommendation, Skill, SkillCatalog, _tokens

HealthCheck = Callable[..., list[Result]]

# Two recommendations within this many points are indistinguishable enough that
# picking one would be a guess, not a match.
_AMBIGUITY_MARGIN = 3


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
