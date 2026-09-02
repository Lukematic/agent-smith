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

from dataclasses import dataclass

from smith.skill_catalog import Recommendation, Skill, SkillCatalog, _tokens

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
