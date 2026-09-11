"""Stance critic (layer 3): check an agent response against a stance's rules.

Heuristic, not a model. The checks below are deterministic keyword/marker
scans, so a pass means "no known violation found", not "the response is
genuinely in the stance's spirit". Sycophancy in new vocabulary passes this;
that is the documented limit of a keyword critic, and why it is layer 3
(verification of the response) rather than layer 1 (selection of the stance).

The critic also verifies thinking-mode outputs (``awino think <mode>``):
mode names are accepted wherever a stance name is. Each mode's structural
requirements live in ``awino.think``; the critic composes them with the
shared no-validation-phrases check every stance carries.
"""

from __future__ import annotations

from awino import think
from awino.stance import Stance

# Shared across every stance: the stances all forbid sycophantic agreement.
# Matched as case-insensitive substrings; any hit fails "no validation phrases".
BANNED_VALIDATION_PHRASES: tuple[str, ...] = (
    "great question",
    "you're absolutely right",
    "youre absolutely right",
    "excellent point",
    "great point",
    "couldn't agree more",
    "couldnt agree more",
    "so glad you asked",
)

# steel-man: opposing-case markers must come before own-view markers.
_OPPOSING_CASE_MARKERS: tuple[str, ...] = (
    "on the other hand",
    "the strongest case against",
    "steel-man",
    "counterargument",
    "devil's advocate",
    "the opposite view",
)
_OWN_VIEW_MARKERS: tuple[str, ...] = (
    "my view",
    "my take",
    "in my view",
    "my recommendation",
    "i recommend",
    "my position",
)

# advisor: must contain a labeled disagreement marker.
_DISAGREEMENT_MARKERS: tuple[str, ...] = (
    "i disagree",
    "disagree",
    "however,",
    "instead,",
    "the risk",
    "uncomfortable truth",
)


def _first_index(text: str, markers: tuple[str, ...]) -> int | None:
    """Earliest occurrence of any marker in the text, or None."""
    found = [text.find(marker) for marker in markers]
    found = [index for index in found if index >= 0]
    return min(found) if found else None


def _banned_phrase_failures(response_text: str) -> list[str]:
    lowered = response_text.casefold()
    if any(phrase in lowered for phrase in BANNED_VALIDATION_PHRASES):
        return ["no validation phrases"]
    return []


def _verify_steel_man(response_text: str) -> list[str]:
    failures = _banned_phrase_failures(response_text)
    lowered = response_text.casefold()
    opposing = _first_index(lowered, _OPPOSING_CASE_MARKERS)
    own_view = _first_index(lowered, _OWN_VIEW_MARKERS)
    # Keyword ordering heuristic: only fails when the response takes its own
    # view without having made the opposing case first. No own-view marker
    # at all is compliant (the response may just present the opposing case).
    if own_view is not None and (opposing is None or opposing > own_view):
        failures.append("opposing case must precede own view")
    return failures


def _verify_teach_back(response_text: str) -> list[str]:
    failures = _banned_phrase_failures(response_text)
    # Heuristic for "ends asking the human to explain it back": the closing
    # lines (last 400 chars) must contain a question and an invitation
    # addressed at the human ("explain" or "you").
    tail = response_text[-400:].casefold()
    if "?" not in tail or ("explain" not in tail and "you" not in tail):
        failures.append("must end asking the human to explain it back")
    return failures


def _verify_advisor(response_text: str) -> list[str]:
    failures = _banned_phrase_failures(response_text)
    # The advisor stance's first rule is "lead with the uncomfortable truth":
    # the check is heuristic keyword matching for a labeled disagreement, not
    # a judgment of whether the disagreement is real.
    lowered = response_text.casefold()
    if not any(marker in lowered for marker in _DISAGREEMENT_MARKERS):
        failures.append("must contain a labeled disagreement")
    return failures


# Stances with only the shared check: their full rules (tables, examples,
# first-person voice) have no reliable keyword signature, so the critic
# documents that limit and checks only what it can check.
_SHARED_CHECK_ONLY: frozenset[str] = frozenset(
    {"first-principles", "assumption-audit", "research-intake", "expert"}
)


# ── thinking modes ─────────────────────────────────────────────────────
# Structural requirements per mode, composed with the shared check. Modes
# that map onto stances inherit the stance's shape (devil = steel-man's
# opposing-case-first); the genuinely new modes define their own.
#
# - feynman (teach-back): simple explanation + ELI5 + a drawn diagram of the
#   core variables and their relationships + where it breaks + self-check.
#   The teaching side is structural: a missing ELI5 or a diagram section
#   with no boxes-and-arrows fails with the part named.
# - blindspot (assumption-audit): assumptions checked + blind spots, each
#   blind spot saying why it was invisible.
# - devil (steel-man): the opposing case, what to take seriously, and the
#   steel-man ordering rule -- the opposing case must come before any own
#   view.
# - premortem: at least 3 failure reasons, each with warning signs.
# - uncomfortable: the avoided question must be asked (a real question) and
#   answered (a real answer).
# - thought-experiment: scenario + push to the extreme + what it reveals.
# - first-principles (first-principles): facts separated from assumptions,
#   then a rebuild from the facts alone -- the research breakdown in mode
#   form.
# - assumption-destroyer: at least 5 assumptions, each with an inversion AND
#   a reframing.
# - simplify: the minimal variables named (at least 2), an ELI5, a drawn
#   diagram of the variables and their relationships, and a solution that
#   says it uses only those variables.


def _verify_thinking_mode(mode_name: str, response_text: str) -> list[str]:
    """A thinking mode's structural failures; empty means compliant."""
    return think.validate(mode_name, response_text) + _banned_phrase_failures(response_text)


def verify(stance_name: str, response_text: str) -> list[str]:
    """The stance or thinking-mode rules a response fails; empty means compliant.

    Accepts stance names and thinking-mode names (``awino think <mode>``).
    Raises ValueError for an unknown name. Checks are deterministic keyword
    heuristics; see the module docstring for the limits.
    """
    try:
        stance = Stance.by_name(stance_name)
    except ValueError:
        # Not a stance: maybe a thinking mode. A genuinely unknown name
        # names both namespaces, since verify() accepts both.
        try:
            think.by_name(stance_name)
        except ValueError:
            raise ValueError(f"unknown stance or thinking mode: {stance_name}") from None
        return _verify_thinking_mode(stance_name, response_text)
    if stance.name == "steel-man":
        return _verify_steel_man(response_text)
    if stance.name == "teach-back":
        return _verify_teach_back(response_text)
    if stance.name == "advisor":
        return _verify_advisor(response_text)
    if stance.name in _SHARED_CHECK_ONLY:
        # Only the shared no-validation-phrases check: no keyword signature
        # exists for these stances' deeper rules.
        return _banned_phrase_failures(response_text)
    # Safety net: any future stance defaults to the shared check rather than
    # silently passing everything.
    return _banned_phrase_failures(response_text)
