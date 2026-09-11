"""owns: awino loop run rpi|ralph|delegate, awino loop next, awino loop status, awino loop approve, awino loop back, awino loop answer, awino loop default, awino loop think, awino loop explain, awino loop probe-answer, awino loop suggest, awino loop suggest-answer

The loop drivers. The machine drives phases; the model thinks inside them.

RPI (research -> pair-plan -> plan -> implement, from skills/awino-rpi/SKILL.md),
Ralph (attempt -> verify -> retry, from skills/awino-ralph/SKILL.md), and
Delegate (decompose -> assign -> execute -> controller-verify, from
skills/awino-delegate/SKILL.md) all fail when the agent drifts, so the driver
owns the sequencing: it hands the model the phase prompt, waits for the
artifact, machine-checks the artifact, and only then advances. Where the driver
hands off to prose -- the model doing the thinking -- the code says so in
comments. Nothing here pretends to do the model's thinking; it only verifies
the shape of what came back.

Loop state persists as JSON under the project's state root
(<state_root>/loops/<id>.json) so it survives restarts. The ledger's
loop-event trail (<state_root>/loops.jsonl) is the append-only audit trail;
the driver's JSON state stays the working state.

On the graph/floor machinery (src/awino/graph.py): the delegate driver does
not use it. The graph owns worker-reviewer rounds with spawned harnesses; the
delegate loop owns file-ownership checks and claim re-verification with plain
subprocess calls. Bolting the loop onto the graph's spawn contract would mean
reimplementing the graph inside the driver, so the driver stays self-contained
and says so here instead of pretending the machinery fits.
"""

from __future__ import annotations

import abc
import json
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import yaml

from awino import heilmeier, seeds, skill_receipts, working_memory
from awino.enforce import Ledger, LoopEvent
from awino.paths import AwinoPaths, project_state_dir

MAX_ATTEMPTS = 3
RESEARCH_MIN_CHARS = 200
RALPH_ATTEMPT_MIN_CHARS = 50
VERIFY_TIMEOUT_SECS = 300
VERIFY_TAIL_CHARS = 2000
FILE_LINE_RE = re.compile(r"\S+:\d+")

PHASE_ORDER = ("research", "pair-plan", "plan", "implement")

# Phases that produce a machine-checkable artifact file. The implement phase
# verifies a handoff to the gate ledger, not an artifact, so it emits no
# artifact_validated / artifact_rejected events.
ARTIFACT_PHASES = ("research", "pair-plan", "plan")

# Required plan headings (lowercase). Each maps to accepted synonyms; a heading
# matches when it contains a synonym case-insensitively. "decisions" is only
# required when the loop went through pair-planning (pair_answers non-empty):
# older plans without a pairing phase still validate.
PLAN_SECTIONS: dict[str, tuple[str, ...]] = {
    "phases": ("phase", "phases"),
    "scope": ("scope",),
    "tests": ("test", "tests", "testing"),
    "rollback": ("rollback", "roll back"),
    "acceptance criteria": ("acceptance", "acceptance criteria", "success criteria"),
    "decisions": ("decision", "decisions"),
}

# Required pairing-brief sections (lowercase, matched by containment like PLAN_SECTIONS).
PAIRING_SECTIONS = ("sub-problems", "candidate approaches", "questions")

# Required research sections (lowercase, matched by containment like
# PLAN_SECTIONS). First principles, enforced: the artifact must show its work
# -- a problem breakdown, assumptions challenged (named explicitly), and
# angles considered -- before any solution. The driver checks the shape of
# the work, not its quality.
#
# The applicability check is the lawyer move, run before solving anything:
# state the problem as given, then ask "is this the actual problem?" --
# like the lawyer who doesn't defend the charge but asks whether the charge
# applies at all. Stated problem vs. reframed problem, with the evidence for
# the reframe -- and the user's confirmation recorded. Planning cannot
# proceed on a problem the user hasn't confirmed is the right one.
RESEARCH_SECTIONS: dict[str, tuple[str, ...]] = {
    "problem breakdown": ("problem breakdown", "breakdown"),
    "assumptions challenged": ("assumptions challenged", "assumptions"),
    "angles considered": ("angles considered", "angles"),
    "applicability check": ("applicability check", "the lawyer move", "applicability"),
}
# Headings that read as a proposed solution. Research documents what exists;
# the required first-principles sections must come before any of these --
# an artifact that jumps straight to a solution fails with the missing part
# named.
_SOLUTION_HEADING_RE = re.compile(r"(?i)\b(solution|proposal|proposed|recommendation)\b")
# An explicitly named assumption: a list item, or prose using the word.
_ASSUMPTION_ITEM_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+\S")
# The applicability check (lawyer move) content rules: the section must name
# the problem as given, the reframe (or that the stated problem stands), the
# evidence for it, and the user's recorded confirmation.
_STATED_PROBLEM_RE = re.compile(
    r"(?i)\b(stated problem|problem as given|as asked)\b"
)
_REFRAMED_PROBLEM_RE = re.compile(
    r"(?i)\b(reframed problem|the real problem|reframe|actual problem)\b"
)
_STANDS_CONFIRMED_RE = re.compile(
    r"(?i)\b(the stated problem stands|no reframe|confirmed as stated|stands confirmed)\b"
)
_EVIDENCE_RE = re.compile(r"(?i)\bevidence\b")
_USER_CONFIRMATION_RE = re.compile(
    r"(?i)\b(user confirmed|confirmed by|confirmation:)\b"
)

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)

# A trade-off marker is a proxy, not a judgment: the driver checks the shape
# of deliberation (did the brief spell out costs), not its quality.
_TRADEOFF_RE = re.compile(r"trade-?off|pro:|con:", re.IGNORECASE)
# A level-of-effort marker is a proxy, not a judgment: the driver checks the
# shape of deliberation (did the brief say what each approach costs), not its
# quality. "effort: low" or "level of effort: two days" both count.
_EFFORT_RE = re.compile(r"(?im)^\s*(?:level of )?effort\s*:\s*(.+?)\s*$")
# The Honda marker: the approach that delivers exactly what was asked, no
# more, is labeled the default recommendation. Bigger alternates stay labeled
# recommendations -- options, never the plan.
_DEFAULT_RECOMMENDATION_RE = re.compile(r"(?i)\bdefault\s+recommendation\b")
# A plan decision that chooses an approach must record whether it followed
# the default recommendation or overrode it, with a reason.
_FOLLOW_DEFAULT_RE = re.compile(r"(?i)\bfollow(?:ed|ing)? the default recommendation\b")
_OVERRIDE_DEFAULT_RE = re.compile(r"(?i)\boverr(?:ode|iding|idden) the default recommendation\b")
# Pairing questions are one per line in Qn: format.
_QUESTION_RE = re.compile(r"(?m)^\s*(Q\d+)\s*:\s*(.+?)\s*$")
_APPROACH_HEADING_RE = re.compile(r"(?m)^#{3,6}\s+(.+?)\s*$")

LOOP_KINDS = ("rpi", "ralph", "delegate")


def kind_of(loop_id: str) -> str:
    """Driver kind from the loop id prefix: ids are '<kind>-<stamp>-<rand>'."""
    prefix = loop_id.split("-", 1)[0]
    if prefix not in LOOP_KINDS:
        raise LoopError(f"unknown loop kind {prefix!r} in loop id {loop_id!r}")
    return prefix


# ── mission alignment ────────────────────────────────────────────────────────
# After an artifact validates, the driver checks it against the project's own
# mission -- cheaply and deterministically, with no model calls:
#   - mission source: `.awino/MISSION.md` when present, else the `goals:` list
#     in `.awino/project.yaml`. Goal texts are the level-2+ headings and list
#     items of the mission doc (the H1 is the project name, not a goal).
#   - keywords: lowercase alphanumeric tokens of length >= 5 from the goal
#     texts, minus MISSION_STOPWORDS. Short tokens ("ship", "fast") and
#     ubiquitous ones ("project", "mission", "goals") would match everything
#     or nothing meaningful, so they are excluded.
#   - the artifact is aligned when any keyword appears in it as a
#     case-insensitive substring. One mention is enough: the check asks "does
#     this serve a stated goal", not "does it serve every goal".

MISSION_MIN_TOKEN = 5
MISSION_STOPWORDS = frozenset(
    {
        "about", "above", "after", "again", "against", "among", "because",
        "before", "being", "below", "between", "could", "doing", "during",
        "every", "first", "from", "goals", "having", "mission", "other",
        "project", "should", "their", "there", "these", "those", "through",
        "under", "until", "where", "which", "while", "with", "within",
        "would",
    }
)

_MISSION_HEADING_RE = re.compile(r"(?m)^#{2,6}\s+(.+?)\s*$")
_MISSION_ITEM_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _mission_goal_texts(project_root: Path) -> list[str]:
    """Goal texts from the project's own mission sources, or [] when neither
    exists. Empty means 'no mission on file': the driver skips the check with
    no event and no noise."""
    mission_md = project_state_dir(project_root) / "MISSION.md"
    if mission_md.is_file():
        try:
            text = mission_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        goals = _MISSION_HEADING_RE.findall(text) + _MISSION_ITEM_RE.findall(text)
        return [goal.strip() for goal in goals if goal.strip()]
    project_yaml = project_state_dir(project_root) / "project.yaml"
    if project_yaml.is_file():
        try:
            raw = project_yaml.read_text(encoding="utf-8")
        except OSError:
            return []
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError:
            return []
        if isinstance(data, dict) and isinstance(data.get("goals"), list):
            return [
                _goal_item_text(item)
                for item in data["goals"]
                if _goal_item_text(item)
            ]
    return []


def mission_goal_texts(project_root: Path) -> list[str]:
    """Stated mission goals for the project, in order (MISSION.md headings
    and list items first, project.yaml `goals:` as fallback).

    Shared helper for the buddy scaffold and the drift check: the goals the
    human has already stated, never invented ones. Empty when nothing is
    stated.
    """
    return _mission_goal_texts(project_root)


def _goal_item_text(item: object) -> str:
    """One goals: list item as text. Strings pass through; mappings use their
    text/title/goal field rather than their repr."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("text", "title", "goal"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def mission_goal_statement(project_root: Path) -> str:
    """The mission goal statement a loop outcome is measured against.

    The first goal text from the project's own mission sources
    (``.awino/MISSION.md``, else the ``goals:`` list in
    ``.awino/project.yaml``) -- the same sources the mission-alignment check
    measures artifacts against. Returns ``"unstated"`` when no mission source
    names a goal: A.W.I.N.O. never invents a mission, so neither does the
    outcome verdict.
    """
    goals = _mission_goal_texts(project_root)
    return goals[0] if goals else "unstated"


def _mission_keywords(goal_texts: list[str]) -> set[str]:
    keywords: set[str] = set()
    for text in goal_texts:
        for token in _TOKEN_RE.findall(text.lower()):
            if len(token) >= MISSION_MIN_TOKEN and token not in MISSION_STOPWORDS:
                keywords.add(token)
    return keywords


def _goal_hit(goal: str, artifact_text: str) -> bool:
    """True when the artifact mentions the goal -- same keyword matching as
    the alignment check, so the 'unaddressed' list agrees with the pass/fail."""
    keywords = _mission_keywords([goal])
    return any(keyword in artifact_text for keyword in keywords)


def mission_success_criteria(project_root: Path) -> list[str]:
    """The mission's success criteria: one measurable statement per exam line.

    These are what loop artifacts and outcome verdicts are judged against --
    not keyword drift, the criteria themselves. Empty when the mission has
    none on file: A.W.I.N.O. never invents criteria.
    """
    return heilmeier.success_criteria(
        heilmeier.load(project_state_dir(project_root))
    )


def evaluate_success_criteria(
    criteria: list[str], artifact_text: str
) -> list[tuple[str, str]]:
    """Judge each success criterion against an artifact: met / unmet / unjudgeable.

    Deterministic keyword matching, the same heuristic as the drift check: a
    criterion is met when the artifact mentions its keywords, unmet when it
    does not, unjudgeable when the criterion yields no keywords to match on.
    The driver checks the shape of the judgment, not its quality -- the final
    call on met vs unmet stays human, at the outcome verdict.
    """
    judged: list[tuple[str, str]] = []
    lowered = artifact_text.lower()
    for criterion in criteria:
        keywords = _mission_keywords([criterion])
        if not keywords:
            judged.append((criterion, "unjudgeable"))
        elif any(keyword in lowered for keyword in keywords):
            judged.append((criterion, "met"))
        else:
            judged.append((criterion, "unmet"))
    return judged


class LoopError(RuntimeError):
    """Anything that stops the loop from advancing."""


class ApprovalRequired(LoopError):
    """Plan validated but no human approval recorded."""


class PairingIncomplete(LoopError):
    """The pairing brief validated but questions are still unanswered."""

    def __init__(self, unanswered: list[str]) -> None:
        self.unanswered = unanswered
        super().__init__(
            "cannot advance from pair-plan: unanswered questions: "
            + ", ".join(unanswered)
            + '; answer with `awino loop answer --question Q1 --answer "..."` '
            + 'or declare a default with `awino loop default --question Q2 --reason "..."`'
        )


class ProblemUnconfirmed(LoopError):
    """Research validated but the human has not confirmed the problem.

    The lawyer move: before anything is solved, the driver asks whether the
    charge applies at all -- stated problem vs. reframed problem, with the
    evidence. Planning cannot proceed on a problem the user hasn't confirmed
    is the right one. Carries the driver's question (stated vs. reframed)
    so the CLI can put it to the user directly.
    """

    def __init__(self, question: str) -> None:
        self.question = question
        super().__init__(
            "cannot advance from research: the problem is not confirmed -- " + question
        )


class LoopLocked(LoopError):
    """A phase failed validation three times; a human must intervene."""


class ComprehensionRequired(LoopError):
    """The human has not demonstrated understanding of the plan.

    Comprehension (explain it back in your own words, answer the probes)
    is required BEFORE plan approval -- comfortable, understanding, reasons
    recorded. A plan approved without understanding is a rubber stamp.
    Carries the exactly-what-is-missing list and the teach-back lines (the
    concept re-explained, then asked back).
    """

    def __init__(self, missing: list[str], teach_back: list[str]) -> None:
        self.missing = missing
        self.teach_back = teach_back
        super().__init__(
            "comprehension check incomplete: " + "; ".join(missing)
        )


class ReceiptBlocked(LoopError):
    """Advancement blocked: a required skill has no valid receipt.

    The receipt names the skill and the problem (missing, stale, or an
    output artifact that fails its own validation). The driver writes
    receipts when the artifact validates -- rerun the phase's check and
    the driver re-attests; nothing else may write one.
    """

    def __init__(self, phase: str, problems: list[str]) -> None:
        self.phase = phase
        self.problems = problems
        super().__init__(
            "cannot advance from '" + phase + "': " + "; ".join(problems)
        )


def slugify(text: str, max_words: int = 6) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return "-".join(slug.split("-")[:max_words]) or "task"


# Phase-name-aware extraction: the skill document's phase headings are the
# contract, not their ordinals. Inserting pair-planning as Phase 2 renumbered
# plan and implement, and only this mapping had to change.
RPI_PHASE_HEADINGS = {
    "research": "Phase 1",
    "pair-plan": "Phase 2 — Pair-planning",
    "plan": "Phase 3",
    "implement": "Phase 4",
}


def _section_end(text: str, start: int) -> int:
    """End index of the section starting at `start`: the next top-level `## `
    heading, ignoring `## ` lines inside fenced code blocks (the skill docs
    embed markdown templates in fences)."""
    in_fence = False
    for match in re.finditer(r"(?m)^(?:```.*|## .+)$", text[start:]):
        line = match.group(0)
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            return start + match.start()
    return len(text)


def phase_prompt_text(skill_md: Path, phase: str) -> str:
    """Extract the phase's prompt block from the RPI skill document.

    Docs become the script, not the sequencer: the prompt the model sees is the
    skill's own phase text. If the skill document is missing the section, this
    raises instead of improvising a substitute prompt.
    """
    try:
        heading = RPI_PHASE_HEADINGS[phase]
    except KeyError:
        raise LoopError(f"unknown RPI phase {phase!r}") from None
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoopError(f"cannot read RPI skill document at {skill_md}: {exc}") from exc
    start = re.search(rf"^## {re.escape(heading)}\b.*$", text, re.MULTILINE)
    if start is None:
        raise LoopError(
            f"{skill_md} has no '## {heading}' section; "
            "refusing to invent a prompt where the docs should be"
        )
    return text[start.start() : _section_end(text, start.end())].strip()


def skill_section(skill_md: Path | None, heading: str, doc_name: str) -> str:
    """Extract a '## <heading>' block from a skill document.

    Used for the Ralph and Delegate skills, whose sections are named for the
    job ("## The loop", "## Step 1 ...", ...) rather than numbered phases.
    The mapping from driver phase to skill heading is fixed and documented at
    each call site: the phase-to-heading mapping is driver logic, but every
    word of the prompt still comes from the skill document. If the heading is
    absent this raises instead of improvising a prompt.
    """
    if skill_md is None:
        raise LoopError(f"no {doc_name} skill document configured")
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoopError(
            f"cannot read {doc_name} skill document at {skill_md}: {exc}"
        ) from exc
    start = re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
    if start is None:
        raise LoopError(
            f"{skill_md} has no '## {heading}' section; "
            "refusing to invent a prompt where the docs should be"
        )
    return text[start.start() : _section_end(text, start.end())].strip()


def _tail(text: str, limit: int = VERIFY_TAIL_CHARS) -> str:
    """The tail of command output: what fits in a ledger detail."""
    text = text or ""
    return text[-limit:] if len(text) > limit else text


def _qid_key(qid: str) -> int:
    """Sort Q1, Q2, Q10 numerically rather than lexicographically."""
    return int(qid[1:])


@dataclass
class LoopState:
    id: str
    task: str
    topic: str
    phase: str  # one of the driver's phase_order, or "done"
    attempts: dict[str, int] = field(default_factory=dict)
    approvals: list[dict] = field(default_factory=list)
    locked: bool = False
    created_at: str = ""
    seed_id: str | None = None
    research_artifact: str = ""
    pairing_artifact: str = ""
    pair_answers: dict = field(default_factory=dict)
    plan_artifact: str = ""
    gate_run_id: str | None = None
    handoff: dict | None = None
    # Hash of the mission's objective + success criteria at loop start. At
    # loop close the driver compares the live hash: changed criteria mean the
    # mission moved under the work, so the human updates the mission first
    # rather than the verdict judging against stale criteria.
    criteria_hash: str | None = None
    # Ralph
    ralph_artifact: str = ""
    check_command: str = ""
    verify_history: list[dict] = field(default_factory=list)
    # Delegate
    decompose_artifact: str = ""
    execute_artifact: str = ""
    # Critical thinking (awino think modes): runs recorded on the loop, an
    # explicit human waiver, and the comprehension check ("execute when
    # comfortable and understanding").
    thinking_runs: list[dict] = field(default_factory=list)
    thinking_waiver: dict | None = None
    comprehension: dict = field(default_factory=dict)
    # The lawyer move: the problem the user confirmed is the actual problem
    # ({"verdict": "confirmed"|"reframed", "solve": <text>, "by": ..., "at": ...}).
    # Set by `awino loop confirm-problem`; research cannot advance without it.
    problem_confirmation: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LoopState:
        return cls(**data)


class Phase(abc.ABC):
    """One loop phase: a prompt for the model plus a machine-checkable validator.

    The prompt text is prose for the model; validate() is the machine's half.
    """

    name: str

    @abc.abstractmethod
    def prompt_block(
        self, driver: LoopDriver, state: LoopState | None = None
    ) -> str:
        """The text shown to the model when this phase starts."""

    @abc.abstractmethod
    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        """Return the list of exactly-what-is-missing items; empty means pass."""


class ResearchPhase(Phase):
    name = "research"

    def prompt_block(
        self, driver: LoopDriver, _state: LoopState | None = None
    ) -> str:
        return phase_prompt_text(driver.skill_md, "research")

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        path = driver.project_root / state.research_artifact
        rel = state.research_artifact
        if not path.is_file():
            return [f"research artifact missing: {rel} -- write it, then run `awino loop next`"]
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) < RESEARCH_MIN_CHARS:
            return [
                f"research artifact too short: {len(text)} chars "
                f"(minimum {RESEARCH_MIN_CHARS})"
            ]
        if not FILE_LINE_RE.search(text):
            return [
                "research artifact contains no file:line references "
                "(e.g. 'src/awino/loops.py:42')"
            ]
        return _validate_research_sections(text)


def _validate_research_sections(text: str) -> list[str]:
    """First-principles shape check: problem breakdown, assumptions
    challenged (named explicitly), and angles considered -- before any
    solution. Each failure names the missing part, never just "incomplete".
    """
    headings = [h.lower() for h in _HEADING_RE.findall(text)]
    solution_idx: int | None = None
    for i, heading in enumerate(headings):
        if _SOLUTION_HEADING_RE.search(heading):
            solution_idx = i
            break
    missing: list[str] = []
    for section, synonyms in RESEARCH_SECTIONS.items():
        idx = next(
            (i for i, h in enumerate(headings) if any(s in h for s in synonyms)),
            None,
        )
        if idx is None:
            missing.append(
                f"research artifact missing required section: '{section}' -- "
                "show the first-principles work before any solution"
            )
        elif solution_idx is not None and idx > solution_idx:
            missing.append(
                f"research artifact section '{section}' comes after a proposed "
                "solution -- the first-principles work must come first"
            )
    if "assumptions challenged" not in "".join(missing):
        body = _section_text(text, RESEARCH_SECTIONS["assumptions challenged"])
        if not _ASSUMPTION_ITEM_RE.search(body) and "assum" not in body.lower():
            missing.append(
                "research artifact section 'assumptions challenged' names no "
                "assumption explicitly: list each assumption you started with "
                "and what the code actually showed"
            )
    # The lawyer move: the section must state the problem as given, answer
    # "is this the actual problem?" (reframe with evidence, or confirm the
    # stated problem stands), and record the user's confirmation. Each
    # failure names the missing part.
    if "applicability check" not in "".join(missing):
        body = _section_text(text, RESEARCH_SECTIONS["applicability check"])
        if not _STATED_PROBLEM_RE.search(body):
            missing.append(
                "research artifact section 'applicability check' names no stated "
                "problem: state the problem as given before asking whether it "
                "is the actual problem"
            )
        if not _REFRAMED_PROBLEM_RE.search(body) and not _STANDS_CONFIRMED_RE.search(
            body
        ):
            missing.append(
                "research artifact section 'applicability check' neither reframes "
                "the problem nor confirms the stated one stands: 'is this the "
                "actual problem?' must be answered, with evidence"
            )
        if not _EVIDENCE_RE.search(body):
            missing.append(
                "research artifact section 'applicability check' cites no evidence: "
                "the reframe (or the confirmation) must rest on evidence, not "
                "a hunch"
            )
        if not _USER_CONFIRMATION_RE.search(body):
            missing.append(
                "research artifact section 'applicability check' records no user "
                "confirmation: planning cannot proceed on a problem the user "
                "hasn't confirmed -- run `awino loop confirm-problem` and paste "
                "the confirmation line it prints"
            )
    return missing


class PairPlanPhase(Phase):
    name = "pair-plan"

    def prompt_block(
        self, driver: LoopDriver, _state: LoopState | None = None
    ) -> str:
        return phase_prompt_text(driver.skill_md, "pair-plan")

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        path = driver.project_root / state.pairing_artifact
        rel = state.pairing_artifact
        if not path.is_file():
            return [
                f"pairing brief missing: {rel} -- write it, then run `awino loop next`"
            ]
        text = path.read_text(encoding="utf-8", errors="replace")
        headings = [h.lower() for h in _HEADING_RE.findall(text)]
        missing: list[str] = []
        for section in PAIRING_SECTIONS:
            if not any(section in h for h in headings):
                missing.append(f"pairing brief missing required section: '{section}'")
        approaches_text = _section_text(text, ("candidate approaches",))
        approach_names = _APPROACH_HEADING_RE.findall(approaches_text)
        if len(approach_names) < 2:
            missing.append(
                f"candidate approaches: found {len(approach_names)}, need at least 2 "
                "(each as a '###' subheading)"
            )
        else:
            defaults: list[str] = []
            for name, body in _split_approach_blocks(approaches_text):
                if not _TRADEOFF_RE.search(body):
                    missing.append(
                        f"approach '{name}' has no trade-off marker: add 'trade-off', "
                        "'pro:' or 'con:' spelling out what it costs"
                    )
                if not _EFFORT_RE.search(body):
                    missing.append(
                        f"approach '{name}' has no level-of-effort marker: add "
                        "'effort: <estimate>' so the human can compare cost"
                    )
                if _DEFAULT_RECOMMENDATION_RE.search(body):
                    defaults.append(name)
            if not defaults:
                missing.append(
                    "no approach is marked as the default recommendation: mark "
                    "the one that delivers exactly what was asked -- no more -- "
                    "with 'default recommendation' (the Honda, not the Bugatti)"
                )
            elif len(defaults) > 1:
                missing.append(
                    "multiple approaches marked as the default recommendation "
                    f"({', '.join(defaults)}): exactly one approach may be the default"
                )
        questions_text = _section_text(text, ("questions",))
        if not _QUESTION_RE.findall(questions_text):
            missing.append(
                "pairing brief has no questions in 'Qn:' format "
                "(e.g. 'Q1: which approach?')"
            )
        # Required skills: the plan declares which skill(s) each phase runs
        # under, so the receipt gate knows what to require. The validator
        # rejects a brief with no declaration, names any phase missing one,
        # and rejects invented skill names.
        skills_text = _section_text(text, ("required skills",))
        if not skills_text.strip():
            missing.append(
                "pairing brief missing required section: 'required skills' -- "
                "declare the required skill(s) per phase, e.g. "
                "'- research: awino-rpi'"
            )
        else:
            declared = skill_receipts.parse_required_skills(skills_text)
            for phase_name in driver.artifact_phases:
                if phase_name not in declared:
                    missing.append(
                        "pairing brief has no required-skills declaration for "
                        f"phase '{phase_name}'"
                    )
            for phase_name, skills in declared.items():
                if phase_name not in driver.phase_order:
                    missing.append(
                        "pairing brief declares required skills for unknown "
                        f"phase '{phase_name}'"
                    )
                for skill in skills:
                    if not driver.skill_known(skill):
                        missing.append(
                            f"pairing brief declares unknown skill '{skill}' for "
                            f"phase '{phase_name}': no such skill in the "
                            "project or bundled skills/"
                        )
        return missing


def _split_approach_blocks(section_text: str) -> list[tuple[str, str]]:
    """Split a candidate-approaches section into (name, body) per '###' heading.

    Heuristic, documented: an approach is a level-3+ subheading; its body is
    everything up to the next subheading. The driver checks the shape of
    deliberation, not its quality.
    """
    blocks: list[tuple[str, str]] = []
    for part in re.split(r"(?m)^#{3,6}\s+", section_text)[1:]:
        name, _, body = part.partition("\n")
        blocks.append((name.strip(), body))
    return blocks


class PlanPhase(Phase):
    name = "plan"

    def prompt_block(
        self, driver: LoopDriver, state: LoopState | None = None
    ) -> str:
        base = phase_prompt_text(driver.skill_md, "plan")
        injected = driver.pairing_decisions_block(state) if state is not None else ""
        return injected + base if injected else base

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        path = driver.project_root / state.plan_artifact
        rel = state.plan_artifact
        if not path.is_file():
            return [f"plan artifact missing: {rel} -- write it, then run `awino loop next`"]
        text = path.read_text(encoding="utf-8", errors="replace")
        headings = [h.lower() for h in _HEADING_RE.findall(text)]
        missing: list[str] = []
        for section, synonyms in PLAN_SECTIONS.items():
            # The decisions section is required only when the loop went
            # through pair-planning: older plans without a pairing phase
            # still validate.
            if section == "decisions" and not state.pair_answers:
                continue
            if not any(any(s in h for s in synonyms) for h in headings):
                missing.append(f"plan missing required section: '{section}'")
        scope_text = _section_text(text, ("scope",))
        for scope_path in _scope_paths(scope_text):
            if not (driver.project_root / scope_path).exists():
                missing.append(f"scope path does not exist in repo: '{scope_path}'")
        if any("decision" in h for h in headings):
            missing.extend(_validate_decision_trace(text, driver, state))
        # Once comprehension work exists in loop state, the plan's decisions
        # section must record it: explanation summary, probes asked and
        # answered, suggestions made and accepted/rejected with reasons.
        comp = state.comprehension or {}
        if comp.get("explanation") or comp.get("probes") or comp.get("suggestions"):
            missing.extend(_validate_comprehension_record(text, state))
        return missing


def _validate_decision_trace(
    plan_text: str, driver: LoopDriver, state: LoopState
) -> list[str]:
    """Every plan decision traces to a pairing question or a declared default.

    Decision entries are bullets, numbered items, ### blocks, or markdown
    table rows (the skill's plan template uses a table). A decision traces
    when it cites a Q id asked in the pairing brief; otherwise it must be
    marked `default:` with a reason. Untraced decisions are guesses the
    pairing phase exists to prevent.

    Honda first: a decision that chooses a candidate approach must also say
    whether it followed the default recommendation or overrode it, with a
    reason -- the plan records which was chosen and why.
    """
    entries = _decision_entries(_decisions_section_text(plan_text))
    if not entries:
        return [
            "decisions section has no decision entries: record one per pairing "
            "question (e.g. '- Q1 -> approach A because ...')"
        ]
    brief_qids = [qid for qid, _ in driver.pairing_questions(state)]
    known = ", ".join(brief_qids) if brief_qids else "(none recorded)"
    recorded_modes = {run.get("mode") for run in state.thinking_runs}
    approach_names = [name for name, _, _ in driver.pairing_approaches(state)]
    missing: list[str] = []
    for entry in entries:
        head = entry.splitlines()[0][:60]
        refs = re.findall(r"\bQ\d+\b", entry)
        unknown = [ref for ref in refs if ref not in brief_qids]
        if unknown:
            missing.append(
                f"decision '{head}' references unknown question '{unknown[0]}' "
                f"(questions asked: {known})"
            )
            continue
        # A decision may cite a recorded thinking-mode run
        # (e.g. "per devil (thinking:devil)"): it traces to the run the same
        # way a Q id traces to a pairing answer.
        thinking_refs = _THINKING_CITE_RE.findall(entry)
        if thinking_refs:
            unknown_modes = [m for m in thinking_refs if m not in recorded_modes]
            if unknown_modes:
                missing.append(
                    f"decision '{head}' cites thinking run "
                    f"'{unknown_modes[0]}' with no recorded run: record one "
                    f"with `awino loop think --mode {unknown_modes[0]} "
                    f"--record <file> --id {state.id}`"
                )
            continue
        mentioned = [n for n in approach_names if n.lower() in entry.lower()]
        if mentioned:
            marker = _FOLLOW_DEFAULT_RE.search(entry) or _OVERRIDE_DEFAULT_RE.search(
                entry
            )
            if marker is None:
                missing.append(
                    f"decision '{head}' chooses an approach ('{mentioned[0]}') "
                    "but does not say whether it followed or overrode the "
                    "default recommendation: add 'followed the default "
                    "recommendation because ...' or 'overrode the default "
                    "recommendation because ...'"
                )
            elif len(entry[marker.end() :].strip()) < 3:
                missing.append(
                    f"decision '{head}' notes the default recommendation but "
                    "gives no reason: say why it was followed or overridden"
                )
            continue
        if refs:
            continue  # traced to a recorded pairing question
        default = re.search(r"default\s*:\s*(.+)", entry, re.IGNORECASE | re.DOTALL)
        if default and default.group(1).strip():
            continue  # explicitly defaulted with a reason
        if default:
            missing.append(
                f"decision '{head}' is marked default but gives no reason"
            )
        else:
            missing.append(
                f"decision '{head}' does not trace to any recorded question "
                "(cite Q1, Q2, ... or mark 'default:' with a reason)"
            )
    return missing


_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
_ENTRY_START_RE = re.compile(r"^(?:#{3,6}\s+|[-*+]\s+|\d+[.)]\s+)")


def _decision_entries(section_text: str) -> list[str]:
    """One entry per decision: bullet, numbered item, ### block, or table row.

    The skill's plan template records decisions as a markdown table, so table
    rows count as entries -- except the separator row and a header row naming
    the columns, which describe the table rather than decide anything.
    """
    entries: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            entries.append(text)
        current.clear()

    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif _TABLE_SEP_RE.match(stripped):
            flush()  # separator row: not a decision
        elif _TABLE_ROW_RE.match(stripped):
            flush()
            cells = [cell.strip().lower() for cell in stripped.strip("|").split("|")]
            if all(cell in ("question", "answer", "rationale", "") for cell in cells):
                continue  # header row: not a decision
            entries.append(stripped)
        elif _ENTRY_START_RE.match(stripped):
            flush()
            current.append(stripped)
        else:
            current.append(stripped)  # continuation of the current entry
    flush()
    return entries


# ── critical thinking: comprehension helpers ─────────────────────────────
# The plan's decisions section carries a "comprehension check" subsection
# once comprehension work exists in state. Probe generation and decision
# tracing ignore that subsection (it records the check; it is not itself a
# decision), while the plan validator requires it to be complete.

_COMPREHENSION_HEADING_RE = re.compile(r"(?im)^#{1,6}\s+comprehension check\b.*$")


def _strip_comprehension_subsection(section_text: str) -> str:
    """Remove the comprehension-check subsection from decisions text."""
    match = _COMPREHENSION_HEADING_RE.search(section_text)
    if not match:
        return section_text
    return section_text[: match.start()]


def _decisions_section_text(plan_text: str) -> str:
    """The plan's decisions section, minus any comprehension-check record."""
    return _strip_comprehension_subsection(_section_text(plan_text, ("decisions",)))


_THINKING_CITE_RE = re.compile(r"\bthinking:([a-z][a-z-]*)\b")


def _references_decision(explanation: str, head: str) -> bool:
    """Whether the explanation references a decision by name.

    Deterministic: any four consecutive words of the decision's head
    appearing in the explanation counts as a reference. Short heads match
    on the whole head.
    """
    words = [w.strip(".,:;!?()[]{}\"'").lower() for w in head.split()]
    words = [w for w in words if w]
    low = re.sub(r"\s+", " ", explanation.lower())
    if len(words) < 4:
        return " ".join(words) in low if words else False
    return any(
        " ".join(words[i : i + 4]) in low for i in range(len(words) - 3)
    )


_CRITERION_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on",
        "is", "are", "be", "by", "as", "at", "it", "this", "that", "from",
        "will", "must", "should", "when", "into", "over", "under", "all",
    }
)


def _criterion_covered(criterion: str, plan_text: str) -> bool:
    """Whether a mission success criterion is addressed in the plan.

    Deterministic word-overlap heuristic: at least half of the criterion's
    significant words (5+ letters, not stopwords) must appear in the plan.
    """
    words = [
        w
        for w in re.findall(r"[a-z]{5,}", criterion.lower())
        if w not in _CRITERION_STOPWORDS
    ]
    if not words:
        return True
    low = plan_text.lower()
    hits = sum(1 for w in words if w in low)
    return hits / len(words) >= 0.5


def _validate_comprehension_record(plan_text: str, state: LoopState) -> list[str]:
    """The decisions section must record the comprehension check.

    Once explanation/probes/suggestions exist in loop state, the plan's
    decisions section carries a "comprehension check" subsection naming them.
    Each failure names the missing part, never just "incomplete".
    """
    comp = state.comprehension or {}
    block = _section_text(plan_text, ("comprehension check", "comprehension"))
    if not block.strip():
        return [
            "plan has comprehension work recorded in loop state but the "
            "decisions section has no 'comprehension check' subsection -- "
            f"paste the block shown by `awino loop status --id {state.id}`"
        ]
    missing: list[str] = []
    low = block.lower()
    if comp.get("explanation") and "explanation" not in low:
        missing.append(
            "comprehension check subsection does not summarize the explanation"
        )
    for qid in (comp.get("probes") or {}):
        if qid.lower() not in low:
            missing.append(
                f"comprehension check subsection does not record probe {qid}"
            )
    for sid in (comp.get("suggestions") or {}):
        if sid.lower() not in low:
            missing.append(
                "comprehension check subsection does not record suggestion "
                f"{sid} (accepted/rejected with reason)"
            )
    return missing


@dataclass(frozen=True)
class PlanSuggestion:
    """One driver-made suggestion on the plan: goals clarity, a missing
    objective, or an alternative worth considering (Honda-first with effort
    labels). `changes_plan` marks suggestions whose acceptance revises the
    plan -- accepting one invalidates the plan approval and the comprehension
    records, so the revised plan re-validates."""

    id: str
    kind: str  # "objective" | "alternative" | "clarity"
    text: str
    changes_plan: bool


class ImplementPhase(Phase):
    name = "implement"

    def prompt_block(
        self, driver: LoopDriver, _state: LoopState | None = None
    ) -> str:
        return phase_prompt_text(driver.skill_md, "implement")

    def validate(self, driver: LoopDriver, _state: LoopState) -> list[str]:
        # Boundary honesty: the driver does NOT reimplement gate close. The
        # gate ledger owns completion; this phase only verifies the handoff
        # point exists -- an open ledger run tagged --loop rpi whose closable
        # state the machine can query. Everything after that (gate
        # record/review/close) is the ledger's job, reached via `awino gate`.
        if driver.open_rpi_run is None:
            return [
                "implement handoff cannot be verified: no ledger query configured "
                "(this driver needs an open_rpi_run callback)"
            ]
        try:
            run_id = driver.open_rpi_run()
        except Exception as exc:  # the ledger's state must be queryable, not assumed
            return [f"implement handoff cannot be verified: ledger unreadable: {exc}"]
        if run_id is None:
            return [
                "no open gate run with --loop rpi; open one with: "
                'awino gate open <task-class> "<objective>" --loop rpi'
            ]
        return []


def _section_text(markdown: str, synonyms: tuple[str, ...]) -> str:
    """Text under the first heading matching any synonym, up to the next
    heading of the same or higher level. Sub-headings (e.g. the `### <worker>`
    blocks under `## Assignments`) stay inside the section."""
    matches = list(_HEADING_RE.finditer(markdown))
    for i, match in enumerate(matches):
        heading = match.group(1).lower()
        if any(s in heading for s in synonyms):
            this_level = len(re.match(r"^#+", match.group(0)).group(0))  # type: ignore[union-attr]
            end = len(markdown)
            for nxt in matches[i + 1 :]:
                nxt_level = len(re.match(r"^#+", nxt.group(0)).group(0))  # type: ignore[union-attr]
                if nxt_level <= this_level:
                    end = nxt.start()
                    break
            return markdown[match.end() : end]
    return ""


def _scope_paths(section_text: str) -> list[str]:
    """Repo-relative paths a plan claims are in scope.

    Backtick-quoted paths are deliberate; bare tokens only count when they
    contain a slash, which keeps prose like 'e.g.' out of the check.
    """
    paths: set[str] = set()
    for match in re.finditer(r"`([^`]+)`", section_text):
        token = match.group(1).strip()
        if token and re.fullmatch(r"[\w.\-/+]+", token):
            paths.add(token)
    for line in section_text.splitlines():
        for match in re.finditer(r"(?:^|[\s(])([\w.\-]*\/[\w.\-/]*)", line):
            paths.add(match.group(1).strip())
    return sorted(paths)

# ── Ralph phases ─────────────────────────────────────────────────────────────
# The ralph skill's sections are named for the job, not numbered phases; the
# mapping phase -> skill heading below is the only hand-maintained part.
# Because the skill has no separate headings for attempt and retry, both
# phases read "The loop" and rely on driver-injected context (attempt number,
# prior failure evidence) to differentiate them.


class RalphAttemptPhase(Phase):
    name = "attempt"

    def prompt_block(
        self, driver: LoopDriver, state: LoopState | None = None
    ) -> str:
        base = skill_section(driver.skill_md, "The loop", "awino-ralph")
        injected: list[str] = []
        if state is not None:
            injected.append("## Attempt context (injected by the driver)\n\n")
            injected.append(
                f"Attempt number: {len(state.verify_history) + 1} "
                f"of {MAX_ATTEMPTS}. "
            )
            prior = [h for h in state.verify_history if h.get("outcome") == "failed"]
            if prior:
                injected.append(
                    "\nPrior verification failures (the attempts that produced "
                    "them were wrong -- fix what the evidence shows, do not "
                    "repeat the same approach):\n"
                )
                for h in prior:
                    injected.append(
                        f"\n- attempt {h.get('attempt')}, command `{h.get('command')}` "
                        f"exited {h.get('exit_code')}:\n"
                        f"  {_tail(h.get('output_tail') or '')}\n"
                    )
        return "".join(injected) + base

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        path = driver.project_root / state.ralph_artifact
        rel = state.ralph_artifact
        if not path.is_file():
            return [
                f"attempt artifact missing: {rel} -- write it, then run `awino loop next`"
            ]
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) < RALPH_ATTEMPT_MIN_CHARS:
            return [
                f"attempt artifact too short: {len(text)} chars "
                f"(minimum {RALPH_ATTEMPT_MIN_CHARS})"
            ]
        return []


class RalphVerifyPhase(Phase):
    name = "verify"

    def prompt_block(
        self, driver: LoopDriver, _state: LoopState | None = None
    ) -> str:
        base = skill_section(driver.skill_md, "Verification is the skill", "awino-ralph")
        injected = (
            "## Verification context (injected by the driver)\n\n"
            "You are about to run the check command stored on this loop. "
            "It is ground truth: it may confirm the attempt (exit 0) or "
            "reject it (nonzero exit). Either outcome is allowed.\n"
        )
        return injected + base

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        """Run the stored check command and record what it actually said.

        The attempt's self-report never enters the loop state: only the
        command's exit code and output do. The returned list is empty on
        pass; on fail it carries the failure evidence for the operator and
        the retry attempt prompt.
        """
        if not state.check_command:
            return ["no check command stored on this loop"]
        try:
            completed = subprocess.run(
                state.check_command,
                shell=True,
                cwd=driver.project_root,
                capture_output=True,
                text=True,
                timeout=VERIFY_TIMEOUT_SECS,
            )
            outcome = "passed" if completed.returncode == 0 else "failed"
            error = ""
        except subprocess.TimeoutExpired:
            completed = None
            outcome = "failed"
            error = f"check command timed out after {VERIFY_TIMEOUT_SECS}s"
        except OSError as exc:
            completed = None
            outcome = "failed"
            error = f"could not run check command: {exc}"
        stdout = completed.stdout if completed is not None else ""
        stderr = completed.stderr if completed is not None else ""
        record = {
            "attempt": len(state.verify_history) + 1,
            "command": state.check_command,
            "outcome": outcome,
            "exit_code": completed.returncode if completed is not None else None,
            "output_tail": _tail((stdout or "") + (stderr or "")),
            "error": error,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        state.verify_history.append(record)
        driver.save(state)
        driver._emit(
            state,
            "verify_passed" if outcome == "passed" else "verify_failed",
            detail=(
                error
                if error
                else f"`{state.check_command}` exited {record['exit_code']}; "
                f"tail: {_tail(record['output_tail'], 500)}"
            ),
        )
        if outcome == "passed":
            return []
        evidence = record["output_tail"] or error or "no output"
        return [
            f"verification failed: `{state.check_command}` "
            f"exited {record['exit_code'] if record['exit_code'] is not None else 'unknown'}",
            f"evidence (tail):\n{evidence}",
        ]


class RalphRetryPhase(Phase):
    name = "retry"

    def prompt_block(
        self, driver: LoopDriver, state: LoopState | None = None
    ) -> str:
        base = skill_section(driver.skill_md, "The loop", "awino-ralph")
        injected = "## Retry context (injected by the driver)\n\n"
        if state is not None:
            failures = [
                h for h in state.verify_history if h.get("outcome") == "failed"
            ]
            if failures:
                injected += (
                    "The previous attempt was rejected by verification. "
                    "Its failure evidence:\n"
                )
                for h in failures[-2:]:
                    injected += (
                        f"\n- attempt {h.get('attempt')}, `{h.get('command')}` "
                        f"exited {h.get('exit_code')}:\n"
                        f"  {_tail(h.get('output_tail') or '')}\n"
                    )
            injected += (
                "\nWrite a corrected attempt artifact. Do not re-explain what "
                "you already tried; address what the evidence above shows "
                "was wrong.\n"
            )
        return injected + base

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        # A retry that wrote nothing new is a retry in name only. The driver
        # requires the artifact to have grown since the last attempt -- crude,
        # but a retry that says nothing new never fixes anything.
        path = driver.project_root / state.ralph_artifact
        if not path.is_file():
            return ["retry produced no attempt artifact"]
        text = path.read_text(encoding="utf-8", errors="replace")
        if state.verify_history:
            baseline = state.verify_history[-1].get("artifact_chars")
            if baseline is not None and len(text) <= baseline:
                return [
                    "retry attempt artifact unchanged since the failed attempt "
                    f"({len(text)} chars, was {baseline}); write the corrected "
                    "attempt before running `awino loop next`"
                ]
            # Record the size the next retry compares against.
            state.verify_history[-1]["artifact_chars"] = len(text)
            driver.save(state)
        return []


# ── Delegate phases ──────────────────────────────────────────────────────────
# Same hand-maintained mapping: phase -> skill heading. The delegate skill's
# sections are "Step N" headings; the mapping below names which step each
# driver phase reads.


def _parse_workers(section_text: str) -> dict[str, dict]:
    """Worker assignments from the decompose artifact's assignment section.

    Each worker is a '### <name>' block. `files:` claims ownership, one
    repo-relative path per line; anything else is free-form context for the
    workers. Shape, not substance: the driver checks ownership is
    non-overlapping and every claim refers to a real path -- the assign
    phase -- while the content of the assignment is the model's work.
    """
    workers: dict[str, dict] = {}
    for part in re.split(r"(?m)^#{3,6}\s+", section_text)[1:]:
        name, _, body = part.partition("\n")
        name = name.strip()
        if not name:
            continue
        workers[name] = {"files": _files_list(body), "body": body}
    return workers


def _files_list(body: str) -> list[str]:
    """Repo-relative paths under a 'files:' (or 'files ::') line.

    One path per line, comment-free; a line is skipped when it starts with a
    bullet, a quote, or a 'file' keyword, which keeps prose like 'files are
    in src/' out of the ownership check. Paths are normalized so
    './src/x.py' and 'src/x.py' compare equal.
    """
    files: list[str] = []
    in_files = False
    for line in body.splitlines():
        stripped = line.strip()
        if re.match(r"(?i)^files\s*:?", stripped):
            in_files = True
            continue
        if not in_files:
            continue
        if not stripped or stripped.startswith("#"):
            continue
        first = stripped.split()[0].strip("`'\"")
        if first.lower() in ("file", "files") or first.startswith(("-", "*", "+")):
            continue
        if not re.fullmatch(r"[\w.\-/+]+", first):
            continue
        files.append(_normalize_path(first))
    return files


def _normalize_path(path: str) -> str:
    """Repo-root-relative normalization: './src/x.py' and 'src/x.py' are one claim."""
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _parse_exec_sections(section_text: str) -> list[dict]:
    """Worker result sections from the execute artifact: one '### <worker>'
    block each, carrying a 'done:' claim, an optional 'check:' command, and
    an optional 'output:' file path."""
    sections: list[dict] = []
    for part in re.split(r"(?m)^#{3,6}\s+", section_text)[1:]:
        name, _, body = part.partition("\n")
        name = name.strip()
        if not name:
            continue
        done = re.search(r"(?im)^done\s*:\s*(.+?)\s*$", body)
        check = re.search(r"(?im)^check\s*:\s*(.+?)\s*$", body)
        output = re.search(r"(?im)^output\s*:\s*(.+?)\s*$", body)
        sections.append(
            {
                "worker": name,
                "done": done.group(1).strip() if done else "",
                "check": check.group(1).strip() if check else "",
                "output": output.group(1).strip("`'\" ") if output else "",
            }
        )
    return sections


class DelegateDecomposePhase(Phase):
    name = "decompose"

    def prompt_block(
        self, driver: LoopDriver, _state: LoopState | None = None
    ) -> str:
        return skill_section(
            driver.skill_md, "Step 1 \u2014 Decompose and check ownership", "awino-delegate"
        )

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        path = driver.project_root / state.decompose_artifact
        rel = state.decompose_artifact
        if not path.is_file():
            return [
                f"decompose artifact missing: {rel} -- write it, then run `awino loop next`"
            ]
        text = path.read_text(encoding="utf-8", errors="replace")
        headings = [h.lower() for h in _HEADING_RE.findall(text)]
        missing: list[str] = []
        if not any("assign" in h for h in headings):
            missing.append(
                "decompose artifact has no '## Assignments' section "
                "(one '### <worker>' block per worker, each with 'files:')"
            )
        workers = _parse_workers(
            _section_text(text, ("assign",))
        )
        if not workers:
            missing.append(
                "decompose artifact names no workers: add '### <worker>' blocks "
                "under '## Assignments', each listing its 'files:'"
            )
        for name, info in workers.items():
            if not info["files"]:
                missing.append(
                    f"worker '{name}' claims no files: add 'files:' ownership"
                )
        return missing


class DelegateAssignPhase(Phase):
    name = "assign"

    def prompt_block(
        self, driver: LoopDriver, _state: LoopState | None = None
    ) -> str:
        base = skill_section(
            driver.skill_md, "Step 3 \u2014 Write self-contained assignments", "awino-delegate"
        )
        injected = (
            "## Assignment context (injected by the driver)\n\n"
            "The driver machine-checks the decompose artifact before any "
            "worker starts: overlapping file ownership is rejected here, at "
            "the cheapest possible moment.\n"
        )
        return injected + base

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        """Machine-check: no two workers may claim the same file, and every
        claimed file must exist. Overlap is a merge conflict in writing, so
        it fails here -- before any work starts -- rather than at review."""
        path = driver.project_root / state.decompose_artifact
        text = path.read_text(encoding="utf-8", errors="replace")
        workers = _parse_workers(
            _section_text(text, ("assign",))
        )
        missing: list[str] = []
        claims: dict[str, str] = {}
        for name in sorted(workers):
            for claimed in workers[name]["files"]:
                if claimed in claims:
                    other = claims[claimed]
                    pair = " and ".join(sorted([name, other]))
                    missing.append(
                        f"{pair} both claim {claimed}: split ownership so "
                        "each file has exactly one owner, then run `awino loop next`"
                    )
                else:
                    claims[claimed] = name
        for claimed in sorted(claims):
            if not (driver.project_root / claimed).exists():
                missing.append(
                    f"claimed path does not exist in repo: '{claimed}' "
                    f"(claimed by {claims[claimed]})"
                )
        return missing


class DelegateExecutePhase(Phase):
    name = "execute"

    def prompt_block(
        self, driver: LoopDriver, _state: LoopState | None = None
    ) -> str:
        return skill_section(
            driver.skill_md, "Step 5 \u2014 Coordinate, do not implement", "awino-delegate"
        )

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        path = driver.project_root / state.execute_artifact
        rel = state.execute_artifact
        if not path.is_file():
            return [
                f"execute artifact missing: {rel} -- write it, then run `awino loop next`"
            ]
        text = path.read_text(encoding="utf-8", errors="replace")
        headings = [h.lower() for h in _HEADING_RE.findall(text)]
        missing: list[str] = []
        if not any("result" in h for h in headings):
            missing.append(
                "execute artifact has no '## Results' section "
                "(one '### <worker>' block per worker)"
            )
        for section in _parse_exec_sections(_section_text(text, ("result",))):
            if not section["done"]:
                missing.append(
                    f"worker '{section['worker']}' has no 'done:' claim: "
                    "record what the worker completed"
                )
        return missing


class DelegateVerifyPhase(Phase):
    name = "controller-verify"

    def prompt_block(
        self, driver: LoopDriver, _state: LoopState | None = None
    ) -> str:
        return skill_section(
            driver.skill_md, "Step 6 \u2014 Verify and synthesize", "awino-delegate"
        )

    def validate(self, driver: LoopDriver, state: LoopState) -> list[str]:
        """Re-verify every worker's done claim: run the declared check
        command, or require the declared output file to exist and be
        non-empty. The claim is what the worker said; this is what the
        machine checks. A false 'done' fails and names the worker."""
        path = driver.project_root / state.execute_artifact
        text = path.read_text(encoding="utf-8", errors="replace")
        missing: list[str] = []
        for section in _parse_exec_sections(_section_text(text, ("result",))):
            worker = section["worker"]
            if not section["done"]:
                missing.append(f"worker '{worker}' has no 'done:' claim to verify")
                continue
            evidence = self._reverify(driver, section)
            if evidence is not None:
                missing.append(
                    f"worker '{worker}' claimed done but verification failed: {evidence}"
                )
        return missing

    def _reverify(self, driver: LoopDriver, section: dict) -> str | None:
        """None when the claim holds; the failure evidence otherwise."""
        if section["check"]:
            try:
                completed = subprocess.run(
                    section["check"],
                    shell=True,
                    cwd=driver.project_root,
                    capture_output=True,
                    text=True,
                    timeout=VERIFY_TIMEOUT_SECS,
                )
            except subprocess.TimeoutExpired:
                return f"check `{section['check']}` timed out after {VERIFY_TIMEOUT_SECS}s"
            except OSError as exc:
                return f"check `{section['check']}` could not run: {exc}"
            if completed.returncode != 0:
                return (
                    f"check `{section['check']}` exited {completed.returncode}: "
                    f"{_tail((completed.stdout or '') + (completed.stderr or ''))}"
                )
            return None
        if section["output"]:
            out_path = driver.project_root / _normalize_path(section["output"])
            if not out_path.is_file():
                return f"declared output file '{section['output']}' does not exist"
            try:
                if out_path.stat().st_size == 0:
                    return f"declared output file '{section['output']}' is empty"
            except OSError as exc:
                return f"declared output file '{section['output']}' unreadable: {exc}"
            return None
        return "no 'check:' command and no 'output:' file declared -- nothing to verify"

# ── drivers ──────────────────────────────────────────────────────────────────


class LoopDriver(abc.ABC):
    """Sequencer for one loop kind. Subclasses declare their phase order; this
    class owns state persistence, the three-strikes cap, and approval recording."""

    loop_kind = "base"
    phase_order: tuple[str, ...] = PHASE_ORDER
    artifact_phases: tuple[str, ...] = ARTIFACT_PHASES

    # ── plain-language narration ──
    # The human must always understand what is happening, why, what is needed
    # from them, and what happens next. These are data, not logic; the CLI
    # renders them as WHY / PHASES / YOU / NEXT lines alongside the
    # machine-readable output. Nothing here changes any asserted line.
    loop_purpose = ""
    phase_blurbs: ClassVar[dict[str, str]] = {}
    human_roles: ClassVar[dict[str, str]] = {}
    next_commands: ClassVar[dict[str, str]] = {}
    check_whys: ClassVar[dict[str, str]] = {}
    # Note printed when a phase has no artifact of its own.
    no_artifact_note = "(none -- machine check only)"
    # Appended to done-loop messages, e.g. RPI's "the gate ledger owns what
    # remains". Empty when nothing owns what remains.
    done_note = ""

    def __init__(
        self,
        project_root: Path,
        loops_dir: Path,
        skill_md: Path | None = None,
        open_rpi_run=None,
        ledger: Ledger | None = None,
        state_root: Path | None = None,
    ) -> None:
        self.project_root = project_root
        self.loops_dir = loops_dir
        self.skill_md = skill_md
        # Callback returning the id of an open ledger run with --loop rpi, or
        # None. Injected so tests can fake the ledger; the CLI wires the real
        # one. The model does the implementing; the machine only checks the
        # handoff point exists.
        self.open_rpi_run = open_rpi_run
        # The ledger's loop-event trail (<state_root>/loops.jsonl) is the
        # audit trail; the driver's JSON state stays the working state.
        # None means no trail (older tests, or callers without a ledger).
        self.ledger = ledger
        # Project state root for working memory (checklist, decisions). None
        # means the memory hooks are no-ops: driver-only tests and callers
        # without project state stay deterministic and file-free.
        self.state_root = state_root
        # Set by check() after each first-validation of an artifact: the
        # mission goals the artifact did not address, or None. The CLI prints
        # these as the human's drift notice. Transient; reset on every check().
        self.last_drift: list[str] | None = None
        # Set by check() alongside last_drift: the success criteria judged
        # against the just-validated artifact as (criterion, met|unmet|
        # unjudgeable), or None when the mission has no criteria on file.
        # The CLI prints these as the named criteria check result.
        self.last_criteria: list[tuple[str, str]] | None = None
        # Set by _complete()/escalate(): the human-facing seed note, or "".
        self.completion_seed_note: str = ""

    @abc.abstractmethod
    def phases(self) -> list[Phase]:
        ...

    def _phase(self, name: str) -> Phase:
        for phase in self.phases():
            if phase.name == name:
                return phase
        raise LoopError(f"unknown phase {name!r}")

    # ── state ────────────────────────────────────────────────────────────────

    def _path(self, loop_id: str) -> Path:
        return self.loops_dir / f"{loop_id}.json"

    def save(self, state: LoopState) -> None:
        self.loops_dir.mkdir(parents=True, exist_ok=True)
        self._path(state.id).write_text(
            json.dumps(state.to_dict(), indent=2), encoding="utf-8"
        )
        (self.loops_dir / "current").write_text(state.id, encoding="utf-8")

    def load(self, loop_id: str) -> LoopState:
        path = self._path(loop_id)
        if not path.is_file():
            raise LoopError(f"no loop {loop_id!r}")
        return LoopState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def current_id(self) -> str | None:
        marker = self.loops_dir / "current"
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip() or None
        return None

    # ── ledger audit trail ─────────────────────────────────────────────────

    def _emit(
        self, state: LoopState, kind: str, phase: str | None = None, detail: str = ""
    ) -> None:
        """Record one loop event. A no-op when the driver has no ledger, so
        driver-only tests stay deterministic and event-free."""
        if self.ledger is None:
            return
        self.ledger.record_loop_event(
            LoopEvent(
                loop_id=state.id,
                loop_kind=self.loop_kind,
                phase=phase if phase is not None else state.phase,
                kind=kind,
                at=datetime.now(UTC).isoformat(),
                detail=detail,
            )
        )

    # ── working memory ───────────────────────────────────────────────────
    # The checklist is the now: every phase boundary updates it, and `awino
    # best` shows the compact brief at session start. Decisions feed
    # decisions.md with their why. Both are no-ops when the driver has no
    # state_root, so driver-only tests stay file-free.

    def _checklist(self) -> working_memory.Checklist | None:
        if self.state_root is None:
            return None
        return working_memory.Checklist(self.state_root)

    def _decisions(self) -> working_memory.Decisions | None:
        if self.state_root is None:
            return None
        return working_memory.Decisions(self.state_root)

    # ── skill receipts ───────────────────────────────────────────────────────────
    # Skill usage as a gated, checkable step. The driver writes a receipt
    # when a phase's artifact validates (the honest write point); the
    # advance gate requires a valid receipt for every required skill before
    # the loop leaves the phase. See awino/skill_receipts.py for the format.

    def _receipts_root(self) -> Path:
        """Where receipts live. Falls back to the derived state dir when
        the driver was built without one -- driver-only tests stay
        file-free, and the production CLI always passes state_root."""
        if self.state_root is not None:
            return self.state_root
        return project_state_dir(self.project_root)

    def _skills_dir(self) -> Path:
        if self.skill_md is not None:
            return self.skill_md.parent.parent
        return AwinoPaths.discover().skills

    def _skill_doc(self, name: str) -> Path | None:
        """The SKILL.md that `name` resolves to: project skills first.

        A pairing brief may declare a skill the project added under
        <project>/skills/, not only the bundled set the driver's own skill
        document came from. Returns None for invented names.
        """
        cleaned = name.strip()
        if not cleaned:
            return None
        for root in (self.project_root / "skills", self._skills_dir()):
            doc = root / cleaned / "SKILL.md"
            if doc.is_file():
                return doc
        return None

    def skill_known(self, name: str) -> bool:
        """Whether `name` is a real skill: honesty for declared skill names."""
        return self._skill_doc(name) is not None

    def _skill_version(self, skill: str) -> str:
        """Pin which skill text the phase ran under (project-first)."""
        doc = self._skill_doc(skill)
        if doc is None:
            return "unknown"
        return skill_receipts.skill_version(doc.parent.parent, skill)

    def required_skills(self, _state: LoopState, phase_name: str) -> list[str]:
        """Skills that must hold valid receipts before advancing FROM the phase.

        Only artifact phases need receipts: a receipt attests an artifact's
        production, and machine-check phases (verify, assign, implement, ...)
        produce none. The base default is the loop kind's own skill -- the
        skill document the phase prompt was extracted from; RPI consults the
        pairing brief's per-phase declaration first.
        """
        if phase_name not in self.artifact_phases:
            return []
        return [f"awino-{self.loop_kind}"]

    def _live_criteria_hash(self) -> str:
        return heilmeier.criteria_hash(
            heilmeier.load(project_state_dir(self.project_root))
        )

    def _phase_inputs_hash(
        self, state: LoopState, phase_name: str, criteria_hash: str
    ) -> str:
        """The receipt's inputs_hash: exactly the phase's consumed inputs --
        artifact path, live mission criteria hash, seed id. Canonical JSON
        with sorted keys. A handwritten receipt alone proves nothing because
        this hash and the artifact's own validation must match at gate time."""
        return skill_receipts.inputs_hash(
            artifact_path=self.phase_artifact(state, phase_name) or "",
            criteria_hash=criteria_hash,
            seed_id=state.seed_id,
        )

    def validate_receipt(
        self, state: LoopState, phase_name: str, skill: str
    ) -> str | None:
        """None when the skill's receipt for the phase is valid; the exact
        problem otherwise. Existence, then inputs_hash against the phase's
        current actual inputs, then the receipt's declared output artifact
        equals the phase's artifact, exists, and -- unless it is byte-identical
        to what validated when the receipt was written -- passes its own
        validation. Each failure names the skill and the problem."""
        receipt = skill_receipts.read_receipt(
            self._receipts_root(), loop_id=state.id, phase=phase_name, skill=skill
        )
        if receipt is None:
            if skill_receipts.receipt_exists(
                self._receipts_root(),
                loop_id=state.id,
                phase=phase_name,
                skill=skill,
            ):
                return (
                    f"skill receipt for skill '{skill}' (phase '{phase_name}') "
                    "is malformed: the receipt file exists but does not parse; "
                    "delete it and re-run `awino loop check` on the valid artifact"
                )
            return (
                f"no skill receipt for skill '{skill}' (phase '{phase_name}'): "
                "the phase completed without one"
            )
        expected = self._phase_inputs_hash(
            state, phase_name, self._live_criteria_hash()
        )
        if receipt.inputs_hash != expected:
            return (
                f"stale skill receipt for skill '{skill}' (phase '{phase_name}'): "
                "inputs changed since the receipt was written "
                f"(receipt inputs_hash={receipt.inputs_hash[:12]}...)"
            )
        artifact_rel = self.phase_artifact(state, phase_name)
        if receipt.output_artifact != artifact_rel:
            return (
                f"skill receipt for skill '{skill}' (phase '{phase_name}') "
                f"points at '{receipt.output_artifact}', not the phase's "
                f"output artifact '{artifact_rel}'"
            )
        artifact = self.project_root / receipt.output_artifact
        if not artifact.is_file():
            return (
                f"skill receipt for skill '{skill}' (phase '{phase_name}') "
                f"points at a missing output artifact: '{receipt.output_artifact}'"
            )
        if (
            receipt.artifact_hash is not None
            and skill_receipts.file_sha256(artifact) == receipt.artifact_hash
        ):
            # Byte-identical to the artifact that validated when the receipt
            # was written: re-running the validator is unnecessary, and some
            # validators (ralph retry) are not idempotent.
            return None
        problems = self._phase(phase_name).validate(self, state)
        if problems:
            return (
                f"skill receipt for skill '{skill}' (phase '{phase_name}') "
                f"points at output artifact '{receipt.output_artifact}' that "
                f"fails its own validation: {problems[0]}"
            )
        return None

    def receipt_problems(self, state: LoopState) -> list[str]:
        """Receipt problems blocking advance FROM the current phase."""
        return [
            problem
            for skill in self.required_skills(state, state.phase)
            if (problem := self.validate_receipt(state, state.phase, skill))
            is not None
        ]

    def skill_statuses(self, state: LoopState, phase_name: str) -> dict[str, str]:
        """received|missing|invalid per required skill, for the checklist."""
        statuses: dict[str, str] = {}
        for skill in self.required_skills(state, phase_name):
            problem = self.validate_receipt(state, phase_name, skill)
            if problem is None:
                statuses[skill] = "received"
            elif problem.startswith("no skill receipt"):
                statuses[skill] = "missing"
            else:
                # Stale, malformed, or pointing at a bad artifact: invalid.
                statuses[skill] = "invalid"
        return statuses

    def _ensure_phase_receipts(self, state: LoopState, criteria_hash: str) -> None:
        """Write (or refresh) receipts for the current phase's required skills.

        Called from check() when the phase's artifact validates: the honest
        write point. A receipt is refreshed when the phase's actual inputs
        moved since it was written (stale inputs_hash) or the artifact's
        bytes changed (stale artifact_hash); an unchanged receipt stands
        and no new ledger event fires. Fires one `skill_receipt` ledger
        event per written receipt.
        """
        if state.phase not in self.artifact_phases:
            return
        expected = self._phase_inputs_hash(state, state.phase, criteria_hash)
        artifact_rel = self.phase_artifact(state, state.phase) or ""
        artifact_hash = skill_receipts.file_sha256(
            self.project_root / artifact_rel
        )
        statuses: dict[str, str] = {}
        for skill in self.required_skills(state, state.phase):
            current = skill_receipts.read_receipt(
                self._receipts_root(),
                loop_id=state.id,
                phase=state.phase,
                skill=skill,
            )
            if (
                current is not None
                and current.inputs_hash == expected
                and current.artifact_hash == artifact_hash
            ):
                statuses[skill] = "received"
                continue
            receipt = skill_receipts.write_receipt(
                self._receipts_root(),
                loop_id=state.id,
                skill=skill,
                version=self._skill_version(skill),
                phase=state.phase,
                inputs_hash=expected,
                output_artifact=artifact_rel,
                artifact_hash=artifact_hash,
            )
            self._emit(
                state,
                "skill_receipt",
                detail=(
                    f"skill={skill} phase={state.phase} artifact={artifact_rel} "
                    f"inputs_hash={receipt.inputs_hash[:12]}..."
                ),
            )
            statuses[skill] = "received"
        checklist = self._checklist()
        if checklist is not None and statuses:
            checklist.note_skill_status(state.id, state.phase, statuses)


    # ── lifecycle ────────────────────────────────────────────────────────────

    def new(
        self, task: str, topic: str | None = None, seed_id: str | None = None
    ) -> LoopState:
        """Create loop state and fix the expected artifact paths.

        The model writes the artifacts; the driver only names where they must
        land, and prints the paths when the phase starts.
        """
        topic = topic or slugify(task)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
        loop_id = f"{self.loop_kind}-{stamp}-{uuid.uuid4().hex[:8]}"
        state = LoopState(
            id=loop_id,
            task=task,
            topic=topic,
            phase=self.phase_order[0],
            attempts=dict.fromkeys(self.phase_order, 0),
            approvals=[],
            locked=False,
            created_at=datetime.now(UTC).isoformat(),
        )
        if seed_id is not None:
            state.seed_id = self._resolve_seed(seed_id)
        self._declare_artifacts(state, stamp, topic)
        state.criteria_hash = heilmeier.criteria_hash(
            heilmeier.load(project_state_dir(self.project_root))
        )
        self.save(state)
        detail = f"task: {task}"
        if state.seed_id is not None:
            detail += f"; seed: {state.seed_id}"
        self._emit(state, "loop_started", detail=detail)
        self._emit(state, "phase_started", detail="initial phase")
        checklist = self._checklist()
        if checklist is not None:
            checklist.note_loop_created(state.id, self.loop_kind, task, state.phase)
        return state

    @abc.abstractmethod
    def _declare_artifacts(self, state: LoopState, stamp: str, topic: str) -> None:
        """Assign this loop kind's artifact paths. The base has none to
        declare; each concrete driver fills in its own."""

    def _resolve_seed(self, seed_id: str) -> str:
        """Validate a --seed id against the tracker and return it.

        Refusals are explicit: seeds unavailable (no usable tracker), unknown
        id, or already-closed id. The human re-runs without --seed rather
        than the driver silently dropping the linkage.
        """
        tracker = seeds.Seeds(self.project_root)
        try:
            tracker_state, message = tracker.state()
        except Exception as exc:
            raise LoopError(
                f"seeds unavailable: {exc}; run without --seed"
            ) from exc
        if tracker_state != seeds.SeedsState.READY:
            raise LoopError(f"seeds unavailable: {message}; run without --seed")
        try:
            seed = tracker.show(seed_id)
        except Exception as exc:
            raise LoopError(
                f"seeds unavailable: {exc}; run without --seed"
            ) from exc
        if seed is None:
            raise LoopError(
                f"unknown seed {seed_id!r}; run without --seed "
                "or with a valid seed id"
            )
        if not seed.open:
            raise LoopError(
                f"seed {seed_id!r} is already closed; "
                "run without --seed or with an open seed id"
            )
        return seed_id

    def validate_current(self, state: LoopState) -> list[str]:
        if state.phase == "done":
            return []
        return self._phase(state.phase).validate(self, state)

    def check(self, state: LoopState) -> list[str]:
        """Validate the current phase and record the outcome in the trail.

        Returns the exactly-what-is-missing list; empty means pass. Artifact
        events fire only for phases that produce artifacts. After an artifact
        validates for the first time, the mission check runs: drift is
        flagged, never blocking.
        """
        self.last_drift = None
        self.last_criteria = None
        missing = self.validate_current(state)
        if missing:
            self.record_failure(state, missing)
        elif state.phase in self.artifact_phases and self.record_artifact_validated(state):
            self.last_drift = self._check_mission_alignment(state)
            self.last_criteria = self._check_success_criteria(state)
        if not missing:
            # Phase-boundary mission revisit: the work stands, so record the
            # live criteria hash. `loop close` compares this against the live
            # hash and prompts instead of judging stale criteria.
            live_criteria = heilmeier.criteria_hash(
                heilmeier.load(project_state_dir(self.project_root))
            )
            # The honest write point for skill receipts: the artifact just
            # validated, so attest it -- write receipts when missing, refresh
            # them when the phase's actual inputs moved since the last one.
            self._ensure_phase_receipts(state, live_criteria)
            state.criteria_hash = live_criteria
            self.save(state)
        return missing

    def artifact_path(self, _state: LoopState) -> str | None:
        """The artifact this loop's current phase produces, or None when the
        phase is a machine check (verify, assign, controller-verify,
        implement)."""
        return None

    def phase_artifact(self, _state: LoopState, _phase_name: str) -> str | None:
        """The artifact file a named phase produces, or None. Drivers
        override; the base driver has no artifacts."""
        return None

    def has_validated_artifacts(self, state: LoopState) -> bool:
        """Whether any artifact on this loop was validated against criteria.

        Reads the loop's own trail. Without a validated artifact nothing was
        ever judged against the recorded criteria hash, so there is no stale
        judgment to guard at close -- the verdict's live evaluation is the
        first examination.
        """
        if self.ledger is None:
            return False
        return any(
            event.kind == "artifact_validated"
            for event in self.ledger.loop_events(state.id)
        )

    def judged_artifact(self, state: LoopState) -> str | None:
        """The artifact the outcome verdict is judged against: the last
        artifact-producing phase's file that exists on disk.

        For RPI that is the plan; for Ralph the attempt; for Delegate the
        execute artifact. The verdict measures the work's latest state, not
        whichever phase happens to be current.
        """
        for phase_name in reversed(self.phase_order):
            if phase_name not in self.artifact_phases:
                continue
            rel = self.phase_artifact(state, phase_name)
            if rel and (self.project_root / rel).is_file():
                return rel
        return None

    def prompt_block(
        self, state: LoopState, phase_name: str | None = None
    ) -> str:
        name = phase_name or state.phase
        return self._phase(name).prompt_block(self, state)

    def record_artifact_validated(self, state: LoopState) -> bool:
        """Emit artifact_validated for a phase that produced a valid artifact.

        Emits once per phase entry: re-checking an already-validated artifact
        (e.g. the `next` after `approve`) records no new fact. A rejection or
        a `back` re-entry re-arms it, since the artifact may have changed.
        Returns True when it emitted.
        """
        if self._phase_already_validated(state):
            return False
        artifact = self.artifact_path(state)
        self._emit(
            state,
            "artifact_validated",
            detail=f"{state.phase} artifact passed validation: {artifact}",
        )
        return True

    def _phase_already_validated(self, state: LoopState) -> bool:
        """Whether the current phase entry already has a validated artifact.

        Reads the loop's own trail: the phase counts as validated when its
        latest phase-scoped outcome is artifact_validated. A rejection or a
        re-entry resets it.
        """
        if self.ledger is None:
            return False
        validated = False
        for event in self.ledger.loop_events(state.id):
            if event.phase != state.phase:
                continue
            if event.kind in ("phase_started", "phase_reentered", "artifact_rejected"):
                validated = False
            elif event.kind == "artifact_validated":
                validated = True
        return validated

    def record_failure(
        self, state: LoopState, missing: list[str] | None = None
    ) -> None:
        """Count a failed validation; the third failure locks the loop.

        A phase that cannot pass three times is not converging -- escalate to
        a human instead of looping forever. The rejection is recorded in the
        ledger trail with the specific reasons in detail.
        """
        state.attempts[state.phase] = state.attempts.get(state.phase, 0) + 1
        if state.attempts[state.phase] >= MAX_ATTEMPTS:
            state.locked = True
        if state.phase in self.artifact_phases:
            detail = (
                "; ".join(missing)
                if missing
                else "validation failed (reasons not passed to record_failure)"
            )
            self._emit(state, "artifact_rejected", detail=detail)
        self.save(state)
        if state.locked:
            checklist = self._checklist()
            if checklist is not None:
                checklist.note_blocked(
                    state.id,
                    f"phase '{state.phase}' failed validation "
                    f"{state.attempts[state.phase]}/{MAX_ATTEMPTS} times; "
                    "a human must intervene",
                )

    def approve_plan(self, state: LoopState, by: str, reason: str, waive_reason: str | None = None) -> None:
        # Ledger-enforced minimum bar: a plan cannot be approved until at
        # least one critical-thinking mode has run on it, or the human
        # explicitly waives it. Forgetting is impossible -- the gate asks
        # every time approval is attempted.
        if not state.thinking_runs and state.thinking_waiver is None:
            if waive_reason is None or not waive_reason.strip():
                raise ApprovalRequired(
                    "critical thinking required before plan approval: run one "
                    "mode and record it "
                    f"(`awino loop think --mode premortem --record <file> "
                    f"--id {state.id}`), or waive explicitly with "
                    '`awino loop approve --waive-thinking '
                    '--waive-reason "..."`'
                )
            self.waive_thinking(state, by, waive_reason.strip())
        # "Execute when comfortable and understanding": approval is a human
        # judgment that the plan is right, and judgment requires
        # understanding. The comprehension check (explain it back in your
        # own words, answer the probes) must complete BEFORE approval -- a
        # plan approved without understanding is a rubber stamp. Forgetting
        # is impossible: the gate asks every time approval is attempted.
        missing = self.comprehension_missing(state)
        if missing:
            raise ComprehensionRequired(missing, self._teach_back_lines(state))
        state.approvals.append(
            {
                "phase": "plan",
                "by": by,
                "reason": reason,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        self._emit(
            state,
            "approval_granted",
            phase="plan",
            detail=f"by={by}" + (f" reason={reason}" if reason else ""),
        )
        self.save(state)
        decisions = self._decisions()
        if decisions is not None:
            decisions.record(
                decision=f"approved {self.loop_kind} loop {state.id} plan",
                why=reason.strip() or working_memory.WHY_MISSING,
                source=f"loop approve --by {by} for loop {state.id}",
                key=f"{state.id}:approval",
            )

    def plan_approved(self, state: LoopState) -> bool:
        return any(a.get("phase") == "plan" for a in state.approvals)

    # ── critical thinking ──
    # The minimum bar lives on the base driver so every approval path
    # enforces it: a plan is approved only after a thinking-mode run or an
    # explicit human waiver, both recorded in the ledger trail.

    def thinking_satisfied(self, state: LoopState) -> bool:
        """The ledger-enforced minimum bar: at least one thinking-mode run
        on this loop, or an explicit human waiver."""
        return bool(state.thinking_runs) or state.thinking_waiver is not None

    def record_thinking_run(
        self, state: LoopState, mode: str, by: str, memory_id: str
    ) -> dict:
        """Record a thinking-mode run as a loop step: state, ledger event,
        and the working-memory id the run's insights landed under."""
        run = {
            "mode": mode,
            "by": by,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "memory_id": memory_id,
        }
        state.thinking_runs.append(run)
        self._emit(
            state,
            "thinking_run",
            detail=f"mode={mode} by={by} memory={memory_id}",
        )
        self.save(state)
        return run

    def waive_thinking(self, state: LoopState, by: str, reason: str) -> dict:
        """Record the human's explicit waiver as a conscious decision: the
        ledger event names the reason, and decisions.md records the why."""
        waiver = {
            "by": by,
            "reason": reason.strip(),
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        state.thinking_waiver = waiver
        self._emit(
            state, "thinking_waived", detail=f"by={by} reason={reason.strip()}"
        )
        self.save(state)
        decisions = self._decisions()
        if decisions is not None:
            decisions.record(
                decision=f"waived critical thinking for loop {state.id} plan",
                why=reason.strip() or working_memory.WHY_MISSING,
                source=f"loop approve --waive-thinking --by {by} for loop {state.id}",
                key=f"{state.id}:thinking-waiver",
            )
        return waiver

    def check_before_advance(self, _state: LoopState) -> bool:
        """Whether the CLI should run check() before advance(). Ralph's
        verify phase returns False: its check command runs inside advance()
        because the outcome routes the transition (retry vs done vs
        escalate) instead of counting as an artifact validation failure."""
        return True

    def advance(self, state: LoopState) -> str:
        """Move to the next phase. The current phase must already validate
        (the CLI checks first); this enforces the human gates, the skill
        receipt gate, and transitions.

        Raises ApprovalRequired/PairingIncomplete/ProblemUnconfirmed/
        ComprehensionRequired when a human gate is unsatisfied, ReceiptBlocked
        when a required skill has no valid receipt, and LoopLocked when the
        loop is locked.
        """
        if state.locked:
            raise LoopLocked(
                f"loop {state.id} is locked after {MAX_ATTEMPTS} failed validations "
                "of a phase; a human must intervene"
            )
        if state.phase == "done":
            raise LoopError("loop is already done")
        self._check_advance_allowed(state)
        receipt_problems = self.receipt_problems(state)
        if receipt_problems:
            checklist = self._checklist()
            if checklist is not None:
                checklist.note_skill_status(
                    state.id, state.phase, self.skill_statuses(state, state.phase)
                )
            raise ReceiptBlocked(state.phase, receipt_problems)
        nxt = self._next_phase(state)
        if nxt is None:
            self._complete(state)
            checklist = self._checklist()
            if checklist is not None:
                checklist.note_done(state.id, "all phases complete")
            return state.phase
        previous = state.phase
        state.phase = nxt
        self._emit(state, "phase_started", detail=f"advanced from '{previous}'")
        self.save(state)
        checklist = self._checklist()
        if checklist is not None:
            checklist.note_phase(state.id, previous, nxt)
        return state.phase

    @abc.abstractmethod
    def _check_advance_allowed(self, state: LoopState) -> None:
        """Driver-specific advance gates. RPI adds the approval and pairing
        gates; Ralph and Delegate have none."""

    def _next_phase(self, state: LoopState) -> str | None:
        """The phase after a successful advance, or None to complete the
        loop. Linear by default; Ralph overrides because a passed
        verification ends the loop and a retry cycles back to verify."""
        idx = self.phase_order.index(state.phase)
        if idx + 1 >= len(self.phase_order):
            return None
        return self.phase_order[idx + 1]

    def _complete(self, state: LoopState) -> str:
        """Close the loop from its final phase. Drivers override for their
        own terminal semantics (gate handoff, seed closure, escalation)."""
        state.phase = "done"
        self.save(state)
        self._emit(state, "loop_closed", detail="all phases complete")
        self._maybe_close_seed(state)
        return "done"

    def reenter_phase(
        self, state: LoopState, phase: str, reason: str = ""
    ) -> LoopState:
        """Re-enter an earlier phase: it becomes current with a fresh attempt count.

        The phase's artifact file is kept on disk but must re-validate on the
        next `awino loop next`. Re-entry is a human intervention, so it also
        clears a three-strikes lock. Approvals are left untouched
        (RpiDriver additionally clears the problem confirmation when
        re-entering research, since the confirmation attested the old
        research).
        """
        if phase not in self.phase_order:
            raise LoopError(
                f"unknown phase {phase!r}; one of {', '.join(self.phase_order)}"
            )
        if state.phase == "done":
            if self.done_note:
                raise LoopError(
                    f"loop is done; {self.done_note} -- start a new loop instead"
                )
            raise LoopError("loop is done; start a new loop instead")
        target = self.phase_order.index(phase)
        current = self.phase_order.index(state.phase)
        if target >= current:
            raise LoopError(
                f"cannot go back to {phase!r}: the current phase is "
                f"{state.phase!r}; `back` only re-enters earlier phases"
            )
        previous = state.phase
        state.phase = phase
        state.attempts[phase] = 0
        state.locked = False
        note = reason.strip() or "(no reason given)"
        self._emit(
            state,
            "phase_reentered",
            detail=f"re-entered {phase!r} from {previous!r}: {note}",
        )
        self._emit(state, "phase_started", detail=f"re-entry of {phase!r}")
        self.save(state)
        checklist = self._checklist()
        if checklist is not None:
            checklist.note_unblocked(
                state.id, f"re-entered {phase!r} from {previous!r}: {note}"
            )
        return state

    def reopen_phase(
        self, state: LoopState, phase: str, reason: str = ""
    ) -> LoopState:
        """Re-open a DONE loop at an earlier phase so a receiptless skill step
        can honestly re-run.

        The dedicated path for buddy --fix on a completed loop whose phase
        finished without a valid skill receipt: the only honest fix is to
        re-run the phase's skill step so check() can write a fresh receipt.
        This method never writes a receipt itself. The loop's recorded
        outcome (verdict, loop_closed event) stays in the ledger as history;
        re-opening a done loop is the operator's explicit choice, named in
        the reason. Like reenter_phase, it clears a three-strikes lock: the
        re-entry is a human intervention.
        """
        if phase not in self.phase_order:
            raise LoopError(
                f"unknown phase {phase!r}; one of {', '.join(self.phase_order)}"
            )
        if state.phase != "done":
            raise LoopError(
                f"loop is not done (phase {state.phase!r}); "
                "`back` re-enters earlier phases of an active loop"
            )
        if phase == "done":
            raise LoopError("cannot re-open a done loop at 'done'")
        note = reason.strip() or "(no reason given)"
        state.phase = phase
        state.attempts[phase] = 0
        state.locked = False
        self._emit(
            state,
            "phase_reentered",
            detail=f"re-opened done loop at {phase!r}: {note}",
        )
        self._emit(state, "phase_started", detail=f"re-entry of {phase!r}")
        self.save(state)
        checklist = self._checklist()
        if checklist is not None:
            checklist.note_unblocked(
                state.id, f"re-opened at {phase!r}: {note}", phase=phase
            )
        return state

    # ── mission alignment ────────────────────────────────────────────────
    def _check_mission_alignment(self, state: LoopState) -> list[str] | None:
        """Flag (never block) when an artifact ignores the project's mission.

        The driver may not silently pass drift -- the event makes it visible
        -- but mission judgment remains human: the flag names the unaddressed
        goals for the operator and advancement proceeds. No mission source on
        file means no event and no output. Returns the unaddressed goal texts
        for the operator's notice, or None when there is no drift.
        """
        goal_texts = _mission_goal_texts(self.project_root)
        if not goal_texts:
            return None
        artifact = self.artifact_path(state)
        if not artifact:
            return None
        path = self.project_root / artifact
        if not path.is_file():
            return None
        keywords = _mission_keywords(goal_texts)
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if any(keyword in text for keyword in keywords):
            return None
        unaddressed = [goal for goal in goal_texts if not _goal_hit(goal, text)]
        self._emit(
            state,
            "mission_drift_flagged",
            detail="; ".join(unaddressed),
        )
        return unaddressed

    def _check_success_criteria(
        self, state: LoopState
    ) -> list[tuple[str, str]] | None:
        """Evaluate the just-validated artifact against the mission's success
        criteria -- not keyword drift, the criteria themselves.

        A named check result, never a blocker: the ledger gets a
        `success_criteria_evaluated` event recording which criteria the
        artifact met, missed, or could not be judged against, and the CLI
        prints the judgment for the operator. No criteria on file means no
        event and no output: A.W.I.N.O. never invents criteria to judge by.
        """
        criteria = mission_success_criteria(self.project_root)
        if not criteria:
            return None
        artifact = self.artifact_path(state)
        if not artifact:
            return None
        path = self.project_root / artifact
        if not path.is_file():
            return None
        judged = evaluate_success_criteria(
            criteria, path.read_text(encoding="utf-8", errors="replace")
        )
        self._emit(
            state,
            "success_criteria_evaluated",
            detail="; ".join(f"{status}: {criterion}" for criterion, status in judged),
        )
        return judged

    # ── pair-planning hooks (neutral defaults; RPI overrides) ─────────────
    def pairing_questions(self, _state: LoopState) -> list[tuple[str, str]]:
        return []

    def pairing_approaches(self, _state: LoopState) -> list[tuple[str, str, str]]:
        """(name, effort, role) per candidate approach from the pairing brief.

        Role is "default" for the approach marked as the default
        recommendation (the Honda: exactly what was asked), "alternate" for
        the rest. The base driver has no pairing brief, so it reports none.
        """
        return []

    def unanswered_questions(self, _state: LoopState) -> list[str]:
        return []

    def pairing_decisions_block(self, _state: LoopState | None) -> str:
        return ""

    def describe_pairing(self, _state: LoopState) -> list[str]:
        return []

    # ── seeds ────────────────────────────────────────────────────────────
    def _maybe_close_seed(self, state: LoopState) -> bool:
        """Close the attached seed on a successful terminal loop, with an
        auditable reason naming the loop and its key verification evidence.
        Failure, lock, and escalation never close a seed -- an unverified
        loop must not retire the work it was tracking. Returns True when the
        seed was closed."""
        if not state.seed_id:
            return False
        tracker = seeds.Seeds(self.project_root)
        reason = (
            f"{self.loop_kind} loop {state.id} completed: "
            f"{self._seed_close_evidence(state)}"
        )
        try:
            result = tracker.close(state.seed_id, reason)
        except Exception as exc:
            # Seed closure is a courtesy on success, not a second gate: the
            # loop is complete either way. Report the failure in the
            # completion note so the operator knows the seed is still open.
            self.completion_seed_note = (
                f"seed {state.seed_id} NOT closed ({exc}); close it by hand"
            )
            return False
        if not getattr(result, "ok", True):
            self.completion_seed_note = (
                f"seed {state.seed_id} NOT closed "
                f"({getattr(result, 'detail', 'close failed')}); close it by hand"
            )
            return False
        self.completion_seed_note = (
            f"seed {state.seed_id} closed: {self._seed_close_evidence(state)}"
        )
        return True

    def _seed_close_evidence(self, _state: LoopState) -> str:
        return "all phases complete"

    def seed_open_note(self, state: LoopState) -> str:
        """Exact human action when a loop ends without closing its seed."""
        return (
            f"seed {state.seed_id} remains open: it closes only on successful "
            "verification. Complete the work, re-verify, or close the seed by "
            "hand when the work is truly done."
        )

    # ── narration ─────────────────────────────────────────────────────────
    def status_next(self, state: LoopState) -> str:
        """The one concrete best action for `loop status`'s NEXT line."""
        if state.locked:
            return self.lock_next(state)
        if state.phase == "done":
            return "loop complete"
        return self.next_commands.get(
            state.phase, f"awino loop next --id {state.id}"
        ).format(id=state.id)

    def lock_next(self, _state: LoopState) -> str:
        return (
            "fix the artifact by hand, then re-run; or abandon with "
            "`awino loop back`"
        )

    def approval_line(self, _state: LoopState) -> str:
        return "approval: n/a (no approval gate for this loop kind)"

    def completion_lines(self, _state: LoopState) -> list[str]:
        """Human-readable lines printed when `next` completes the loop."""
        lines = ["COMPLETE  loop complete"]
        if self.completion_seed_note:
            lines.append(f"SEED  {self.completion_seed_note}")
        return lines

class RpiDriver(LoopDriver):
    """Research -> pair-plan -> plan -> implement, with human approval gating
    implement. Pair-planning records human decisions before planning; the plan
    prompt carries those decisions and the plan validator traces each of them."""

    loop_kind = "rpi"
    phase_order = PHASE_ORDER
    artifact_phases = ARTIFACT_PHASES
    no_artifact_note = "(none -- this phase hands off to the gate ledger)"
    done_note = "the gate ledger owns what remains"

    loop_purpose = (
        "keep a big change honest: research what is, pair with a human on "
        "what could be, plan the change, then implement against the plan"
    )
    phase_blurbs: ClassVar[dict[str, str]] = {
        "research": "read the code and write down what is true, with file:line evidence",
        "pair-plan": "decompose the work, propose approaches with trade-offs, and ask the human the questions only they can answer",
        "plan": "write the plan tracing every decision to a pairing answer; a human approves it",
        "implement": "do the plan in a fresh session, then hand the work to the gate ledger",
    }
    human_roles: ClassVar[dict[str, str]] = {
        "research": "write the research artifact, then run `awino loop next`",
        "pair-plan": "write the pairing brief, then answer each question with `awino loop answer` (or declare a default with `awino loop default`)",
        "plan": "write the plan, then run `awino loop approve` when it is right",
        "implement": "do the work, open a gate run with --loop rpi, then run `awino loop next`",
    }
    next_commands: ClassVar[dict[str, str]] = {
        "research": "write the research artifact, then run `awino loop next --id {id}`",
        "pair-plan": "answer each open question: `awino loop answer --question Q1 --answer \"...\" --id {id}`",
        "plan": "run `awino loop approve --by NAME --reason ... --id {id}` once the plan is right",
        "implement": "open the gate run, then run `awino loop next --id {id}`",
    }
    check_whys: ClassVar[dict[str, str]] = {
        "research": "research without file:line evidence is vibes; the check asks for receipts",
        "pair-plan": "a brief without trade-offs, effort markers, or questions is a plan wearing a disguise",
        "plan": "an untraced decision is a guess the pairing phase exists to prevent",
    }

    def phases(self) -> list[Phase]:
        return [ResearchPhase(), PairPlanPhase(), PlanPhase(), ImplementPhase()]

    def _declare_artifacts(self, state: LoopState, stamp: str, topic: str) -> None:
        state.research_artifact = f"thoughts/research/{stamp}-{topic}.md"
        state.pairing_artifact = f"thoughts/pairing/{stamp}-{topic}.md"
        state.plan_artifact = f"thoughts/plans/{stamp}-{topic}.md"

    def declared_skills(self, state: LoopState) -> dict[str, list[str]]:
        """The pairing brief's per-phase required-skills declaration.

        Empty when the pairing brief is absent or unreadable (pair-planning
        skipped or not yet written): the loop-kind default skill governs
        those phases instead.
        """
        if not state.pairing_artifact:
            return {}
        path = self.project_root / state.pairing_artifact
        if not path.is_file():
            return {}
        return skill_receipts.parse_required_skills(
            _section_text(path.read_text(encoding="utf-8", errors="replace"),
                          ("required skills",))
        )

    def required_skills(self, state: LoopState, phase_name: str) -> list[str]:
        """The pairing brief's declaration wins when present; otherwise the
        loop kind's own skill -- the skill document the phase prompt was
        extracted from."""
        if phase_name not in self.artifact_phases:
            return []
        declared = self.declared_skills(state).get(phase_name)
        if declared:
            return declared
        return [f"awino-{self.loop_kind}"]

    def artifact_path(self, state: LoopState) -> str | None:
        return self.phase_artifact(state, state.phase)

    def phase_artifact(self, state: LoopState, phase_name: str) -> str | None:
        return {
            "research": state.research_artifact,
            "pair-plan": state.pairing_artifact,
            "plan": state.plan_artifact,
        }.get(phase_name)

    def _pairing_brief_exists(self, state: LoopState) -> bool:
        """Pair-planning is engaged by writing the pairing brief. If the
        operator advances from research without one, the pair-plan phase is
        vacuous (no questions to answer) and the loop proceeds directly to
        plan, preserving the research -> plan -> implement path for loops
        that don't use pair-planning."""
        if not state.pairing_artifact:
            return False
        return (self.project_root / state.pairing_artifact).is_file()

    def _next_phase(self, state: LoopState) -> str | None:
        nxt = super()._next_phase(state)
        if nxt == "pair-plan" and not self._pairing_brief_exists(state):
            return "plan"
        return nxt

    # ── pair-planning ──
    def pairing_questions(self, state: LoopState) -> list[tuple[str, str]]:
        """The Qn questions asked in the pairing brief, in numeric order."""
        if not state.pairing_artifact:
            return []
        path = self.project_root / state.pairing_artifact
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        questions = _QUESTION_RE.findall(_section_text(text, ("questions",)))
        return sorted(
            ((qid, question) for qid, question in questions),
            key=lambda item: _qid_key(item[0]),
        )

    def pairing_approaches(self, state: LoopState) -> list[tuple[str, str, str]]:
        """(name, effort, role) per candidate approach in the pairing brief.

        Role is "default" for the approach marked as the default
        recommendation -- the Honda, the one that delivers exactly what was
        asked -- and "alternate" for the rest. Alternates are labeled
        recommendations with effort estimates: options, never the plan.
        """
        if not state.pairing_artifact:
            return []
        path = self.project_root / state.pairing_artifact
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        approaches: list[tuple[str, str, str]] = []
        for name, body in _split_approach_blocks(
            _section_text(text, ("candidate approaches",))
        ):
            effort = _EFFORT_RE.search(body)
            role = (
                "default"
                if _DEFAULT_RECOMMENDATION_RE.search(body)
                else "alternate"
            )
            approaches.append(
                (name, effort.group(1).strip() if effort else "unstated", role)
            )
        return approaches

    def unanswered_questions(self, state: LoopState) -> list[str]:
        asked = [qid for qid, _ in self.pairing_questions(state)]
        return [qid for qid in asked if qid not in state.pair_answers]

    # ── the lawyer move: applicability check ─────────────────────────────
    # Before anything is solved, the driver asks whether the charge applies
    # at all: stated problem vs. reframed problem, with the evidence. The
    # research artifact's applicability-check section carries the four parts
    # (validator-checked); the user's answer is recorded here, on state, and
    # research cannot advance without it -- so pair-planning (and the direct
    # research -> plan path) cannot start on an unconfirmed problem.

    def _research_text(self, state: LoopState) -> str:
        if not state.research_artifact:
            return ""
        path = self.project_root / state.research_artifact
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _applicability_lines(self, state: LoopState) -> list[str]:
        body = _section_text(
            self._research_text(state), RESEARCH_SECTIONS["applicability check"]
        )
        return [ln.strip() for ln in body.splitlines() if ln.strip()]

    def _problem_after_marker(
        self, state: LoopState, marker: re.Pattern[str]
    ) -> str | None:
        """The first non-empty line after a marker line in the
        applicability-check section: the stated (or reframed) problem as the
        research wrote it."""
        lines = self._applicability_lines(state)
        for i, line in enumerate(lines):
            if marker.search(line) and i + 1 < len(lines):
                return re.sub(r"^[-*+#>\s]+", "", lines[i + 1]).strip() or None
        return None

    def stated_problem(self, state: LoopState) -> str | None:
        """The problem as given, from the applicability-check section."""
        return self._problem_after_marker(state, _STATED_PROBLEM_RE)

    def reframed_problem(self, state: LoopState) -> str | None:
        """The reframed problem, when the research names one."""
        return self._problem_after_marker(state, _REFRAMED_PROBLEM_RE)

    def confirm_problem(
        self, state: LoopState, by: str, reframed: str | None = None
    ) -> dict:
        """Record the user's answer to the lawyer move: which problem do we solve?

        Verdict "confirmed": the stated problem stands -- solve is the stated
        problem from the research artifact. Verdict "reframed": the evidence
        says the real problem is the reframe -- solve is the reframed text.
        Emits a problem_confirmed ledger event. Research cannot advance until
        this is recorded; the artifact's applicability-check section must
        also carry the confirmation line (the validator checks).
        """
        if reframed is None:
            stated = self.stated_problem(state)
            if stated is None:
                raise LoopError(
                    "cannot confirm: the research artifact's applicability-check "
                    "section names no stated problem to confirm -- write the "
                    "section first, then confirm"
                )
            verdict, solve = "confirmed", stated
        else:
            reframed = reframed.strip()
            if not reframed:
                raise LoopError(
                    "cannot confirm: --reframed needs the reframed problem text"
                )
            verdict, solve = "reframed", reframed
        confirmation = {
            "verdict": verdict,
            "solve": solve,
            "by": by,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        state.problem_confirmation = confirmation
        self._emit(
            state,
            "problem_confirmed",
            detail=f"verdict={verdict} by={by} solve={solve}",
        )
        self.save(state)
        return confirmation

    def reenter_phase(
        self, state: LoopState, phase: str, reason: str = ""
    ) -> LoopState:
        """Re-entering research clears the problem confirmation.

        The confirmation attested the old research ("the stated problem
        stands" for that artifact). Re-entry means the research is under
        re-examination -- planning on the old attestation would be a stale
        confirmation, so the lawyer move asks again. Forgetting is
        impossible.
        """
        if phase == "research":
            state.problem_confirmation = None
        return super().reenter_phase(state, phase, reason=reason)

    def problem_confirmation_line(self, state: LoopState) -> str:
        """The exact line to paste into the research artifact's
        applicability-check section, recording the user's confirmation."""
        conf = state.problem_confirmation or {}
        word = "reframed" if conf.get("verdict") == "reframed" else "stated"
        return (
            f"User confirmed by {conf.get('by', '?')}: solve the {word} "
            f"problem -- \"{conf.get('solve', '?')}\"."
        )

    def problem_line(self, state: LoopState) -> str:
        """One status line for the research phase: which problem is confirmed."""
        conf = state.problem_confirmation
        if conf is None:
            return (
                "problem: unconfirmed -- planning cannot proceed until you confirm "
                "the problem: `awino loop confirm-problem --reframed \"...\" | "
                f"--confirmed --id {state.id}`"
            )
        word = "reframed" if conf["verdict"] == "reframed" else "stated"
        return (
            f"problem: {conf['verdict']} -- solve the {word} problem: "
            f"{conf['solve']} (by={conf['by']})"
        )

    def problem_question(self, state: LoopState) -> str:
        """The lawyer move put to the user directly: stated vs. reframed,
        with the evidence pointer. This is what the gate asks every time."""
        stated = self.stated_problem(state)
        reframed = self.reframed_problem(state)
        if stated and reframed:
            return (
                f"you asked me to solve '{stated}', but the evidence says the real "
                f"problem is '{reframed}' -- which do we solve? Run "
                f"`awino loop confirm-problem --reframed \"...\"` or "
                f"`awino loop confirm-problem --confirmed --id {state.id}`."
            )
        if stated:
            return (
                f"the research states the problem as '{stated}' but you have not "
                f"confirmed it is the actual problem -- which do we solve? Run "
                f"`awino loop confirm-problem --confirmed --id {state.id}` "
                f"(or `--reframed \"...\"` if the evidence points elsewhere)."
            )
        return (
            "the research names no stated problem yet -- write the applicability "
            "check section, then run `awino loop confirm-problem --reframed \"...\" "
            f"| --confirmed --id {state.id}`."
        )

    def pairing_decisions_block(self, state: LoopState | None) -> str:
        """The driver-injected 'Human decisions so far' section of the plan
        prompt: recorded Q&A plus declared defaults. Marked as injected so
        the model cannot mistake driver content for skill text."""
        if state is None or not state.pair_answers:
            return ""
        lines = [
            "## Human decisions so far (injected by the driver -- these are "
            "recorded human answers, not skill text)\n"
        ]
        asked = dict(self.pairing_questions(state))
        for qid in sorted(state.pair_answers, key=_qid_key):
            record = state.pair_answers[qid]
            question = asked.get(qid, "(question text not found in brief)")
            if record.get("kind") == "default":
                lines.append(
                    f"- {qid}: {question}\n"
                    f"  DEFAULT: {record.get('text', '')}"
                )
            else:
                lines.append(f"- {qid}: {question}\n  ANSWER: {record.get('text', '')}")
        lines.append("")
        return "\n".join(lines)

    def describe_pairing(self, state: LoopState) -> list[str]:
        """Status lines for the pair-plan phase: the Honda presented as the
        default recommendation, alternates as labeled recommendations with
        effort, then what's asked, what's answered, what's still open.

        Calibrated by the user model: a human who repeatedly overrode the
        Honda default (RULE-SCOPE-BIG) sees the bigger recommendations first.
        """
        lines: list[str] = []
        approaches = self.pairing_approaches(state)
        if approaches:
            lines.append(
                "APPROACHES  the default recommendation is the Honda -- exactly "
                "what was asked; alternates are recommendations, never the plan"
            )
            scope = working_memory.UserModel.recommendation_scope(
                working_memory.UserModel.load()
            )
            ordered = list(approaches)
            if scope == "big-first":
                # The human's learned preference, not the philosophy's: the
                # bigger recommendations lead, the Honda stays labeled.
                ordered = [a for a in ordered if a[2] != "default"] + [
                    a for a in ordered if a[2] == "default"
                ]
                lines.append(
                    "OPTIONS  showing bigger recommendations first "
                    "(learned: this human overrides the Honda default)"
                )
            for name, effort, role in ordered:
                if role == "default":
                    lines.append(
                        f"  - {name} [DEFAULT RECOMMENDATION -- delivers exactly "
                        f"what was asked] (effort: {effort})"
                    )
                else:
                    lines.append(
                        f"  - {name} [recommendation] (effort: {effort})"
                    )
        for qid, question in self.pairing_questions(state):
            record = state.pair_answers.get(qid)
            if record is None:
                lines.append(f"OPEN {qid}: {question}")
            elif record.get("kind") == "default":
                lines.append(
                    f"DEFAULT {qid}: {question} -> {record.get('text', '')}"
                )
            else:
                lines.append(f"ANSWERED {qid}: {question} -> {record.get('text', '')}")
        return lines

    def record_pair_answer(
        self,
        state: LoopState,
        qid: str,
        kind: str,
        text: str,
        by: str = "human",
    ) -> dict:
        """Record a human answer or declared default for a pairing question.

        Re-answering overwrites the latest state but the ledger keeps both
        events, so the history of a changed mind is auditable.
        """
        asked = [q for q, _ in self.pairing_questions(state)]
        if qid not in asked:
            raise LoopError(
                f"unknown question {qid!r}: the pairing brief asked "
                f"{', '.join(asked) if asked else '(nothing yet)'}"
            )
        record = {
            "kind": kind,  # "answer" or "default"
            "text": text,
            "reason": text if kind == "default" else "",
            "by": by,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        state.pair_answers[qid] = record
        self.save(state)
        self._emit(
            state,
            "human_answered",
            detail=f"question={qid} kind={kind} by={by}: {text}",
        )
        decisions = self._decisions()
        if decisions is not None:
            asked = dict(self.pairing_questions(state))
            question = asked.get(qid, "(question text not found in brief)")
            kind_word = "DEFAULT" if kind == "default" else "ANSWER"
            decisions.record(
                decision=f"{qid}: {question} -> {kind_word}: {text}",
                why=text,
                source=f"pair-planning {qid} in loop {state.id} (by {by})",
                key=f"{state.id}:{qid}",
            )
        return record

    # ── critical thinking: offers, comprehension, suggestions ──
    # Woven into the loop, not opt-in-only: the driver offers
    # context-relevant modes at each checkpoint, gates plan approval on a
    # thinking run or explicit waiver, and gates plan advancement on the
    # comprehension check ("execute when comfortable and understanding").

    _THINKING_OFFERS: ClassVar[dict[str, tuple[str, str]]] = {
        "research": (
            "assumption-destroyer",
            "want me to surface the five biggest assumptions?",
        ),
        "pair-plan": (
            "devil",
            "want me to devil's-advocate this before you decide?",
        ),
        "plan": (
            "premortem",
            "want a pre-mortem on the chosen approach?",
        ),
    }

    def thinking_offer_for_phase(self, phase: str) -> tuple[str, str] | None:
        """The context-relevant thinking mode offered at a checkpoint: the
        mode name and the plain-language offer. None where no checkpoint
        offers one. The human accepts by running it, or declines by not."""
        return self._THINKING_OFFERS.get(phase)

    def _plan_text(self, state: LoopState) -> str:
        if not state.plan_artifact:
            return ""
        path = self.project_root / state.plan_artifact
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def comprehension_probes(self, state: LoopState) -> list[tuple[str, str]]:
        """2-3 targeted probe questions derived from the plan's decisions
        section and stated risks. Deterministic: the same plan always yields
        the same probes."""
        text = self._plan_text(state)
        if not text:
            return []
        probes: list[tuple[str, str]] = []
        for i, entry in enumerate(
            _decision_entries(_decisions_section_text(text))[:2], 1
        ):
            head = re.sub(r"\s+", " ", entry.splitlines()[0]).strip()[:80]
            probes.append(
                (
                    f"P{i}",
                    f"Decision '{head}': why this choice, and what breaks "
                    "if it is wrong?",
                )
            )
        if len(probes) < 3:
            risks = _decision_entries(_section_text(text, ("risk", "risks")))
            if risks:
                head = re.sub(r"\s+", " ", risks[0].splitlines()[0]).strip()[:80]
                probes.append(
                    (
                        f"P{len(probes) + 1}",
                        f"Risk '{head}': how would you detect it early, "
                        "before it costs you?",
                    )
                )
        return probes

    def probed_decision_heads(self, state: LoopState) -> list[str]:
        """The key decisions the probes cover, for the reference check and
        the teach-back."""
        text = self._plan_text(state)
        if not text:
            return []
        return [
            re.sub(r"\s+", " ", entry.splitlines()[0]).strip()[:80]
            for entry in _decision_entries(_decisions_section_text(text))[:2]
        ]

    def comprehension_missing(self, state: LoopState) -> list[str]:
        """Exactly what is missing before the plan may advance: the human's
        explanation, every probe answered, and the explanation referencing
        the plan's key decisions by name. Empty means the human demonstrated
        understanding -- comfortable, understanding, reasons recorded."""
        probes = self.comprehension_probes(state)
        comp = state.comprehension or {}
        explanation = (comp.get("explanation") or {}).get("text") or ""
        missing: list[str] = []
        if not explanation.strip():
            missing.append(
                "no explanation recorded: write the plan in your own words -- "
                f"`awino loop explain --text \"...\" --id {state.id}`"
            )
        answered = comp.get("probes") or {}
        for qid, question in probes:
            if qid not in answered:
                missing.append(
                    f"probe {qid} unanswered: {question} -- "
                    f"`awino loop probe-answer --question {qid} "
                    f'--answer "..." --id {state.id}`'
                )
        heads = self.probed_decision_heads(state)
        if heads and explanation.strip():
            referenced = sum(
                1 for head in heads if _references_decision(explanation, head)
            )
            required = max(1, (len(heads) + 1) // 2)
            if referenced < required:
                missing.append(
                    f"explanation references {referenced} of {len(heads)} key "
                    f"decisions by name (need at least {required}): name the "
                    "decisions your probes covered"
                )
        return missing

    def record_explanation(
        self, state: LoopState, text: str, by: str = "human"
    ) -> dict:
        """Record the human's explanation of the plan, in their own words."""
        text = text.strip()
        if not text:
            raise LoopError("cannot record an empty explanation")
        comp = state.comprehension or {}
        comp["explanation"] = {
            "text": text,
            "by": by,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        state.comprehension = comp
        self.save(state)
        self._emit(
            state,
            "comprehension_recorded",
            detail=f"explanation recorded by={by} ({len(text)} chars)",
        )
        return comp["explanation"]

    def record_probe_answer(
        self, state: LoopState, qid: str, answer: str, by: str = "human"
    ) -> dict:
        """Record the human's answer to a comprehension probe."""
        asked = [q for q, _ in self.comprehension_probes(state)]
        if qid not in asked:
            raise LoopError(
                f"unknown probe {qid!r}: the plan's probes are "
                f"{', '.join(asked) if asked else '(none yet -- write the plan first)'}"
            )
        comp = state.comprehension or {}
        probes = comp.get("probes") or {}
        probes[qid] = {
            "answer": answer.strip(),
            "by": by,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        comp["probes"] = probes
        state.comprehension = comp
        self.save(state)
        self._emit(
            state, "comprehension_recorded", detail=f"probe {qid} answered by={by}"
        )
        return probes[qid]

    def plan_suggestions(self, state: LoopState) -> list[PlanSuggestion]:
        """Driver-made suggestions on the plan: goals clarity, missing
        objectives (mission criteria the plan never addresses), and
        alternatives worth considering (pairing approaches not chosen,
        Honda-first with effort labels). Deterministic from plan, mission,
        and pairing brief."""
        text = self._plan_text(state)
        if not text:
            return []
        out: list[PlanSuggestion] = []

        def add(kind: str, suggestion_text: str, changes_plan: bool) -> None:
            out.append(
                PlanSuggestion(
                    id=f"S{len(out) + 1}",
                    kind=kind,
                    text=suggestion_text,
                    changes_plan=changes_plan,
                )
            )

        for criterion in mission_success_criteria(self.project_root):
            if not _criterion_covered(criterion, text):
                add(
                    "objective",
                    "Missing objective: the plan never addresses the mission "
                    f"criterion '{criterion}'. Add it, or record why it is "
                    "out of scope.",
                    True,
                )
        decided = _decisions_section_text(text).lower()
        for name, effort, role in self.pairing_approaches(state):
            if role == "default" or name.lower() in decided:
                continue
            add(
                "alternative",
                "Alternative worth considering: "
                f"{name} (effort: {effort}) -- the pairing brief's alternate. "
                "The plan takes another path; record why this one loses.",
                True,
            )
        if len(_section_text(text, ("scope",)).strip()) < 120:
            add(
                "clarity",
                "Goals clarity: the scope section is thin. State the goal in "
                "one sentence so the plan can be judged against it.",
                False,
            )
        return out

    def record_suggestion_decision(
        self,
        state: LoopState,
        sid: str,
        verdict: str,
        reason: str,
        by: str = "human",
    ) -> dict:
        """Record accepted/rejected + reason for a driver suggestion.

        Accepting a plan-changing suggestion revises the plan: the approval
        was for the old plan and the comprehension records described it, so
        both are cleared -- the revised plan re-validates and the gates ask
        again. Forgetting is impossible.
        """
        verdict = verdict.strip().lower()
        if verdict not in ("accepted", "rejected"):
            raise LoopError(
                f"bad verdict {verdict!r}: expected accepted or rejected"
            )
        suggestion = next(
            (s for s in self.plan_suggestions(state) if s.id == sid), None
        )
        if suggestion is None:
            raise LoopError(
                f"unknown suggestion {sid!r}: list them with "
                f"`awino loop suggest --id {state.id}`"
            )
        comp = state.comprehension or {}
        decided = comp.get("suggestions") or {}
        decided[sid] = {
            "verdict": verdict,
            "reason": reason.strip(),
            "by": by,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "text": suggestion.text,
            "changes_plan": suggestion.changes_plan,
        }
        comp["suggestions"] = decided
        state.comprehension = comp
        self._emit(
            state,
            "suggestion_decided",
            detail=f"suggestion={sid} verdict={verdict} by={by}: {reason.strip()}",
        )
        if verdict == "accepted" and suggestion.changes_plan:
            state.approvals = [
                a for a in state.approvals if a.get("phase") != "plan"
            ]
            comp.pop("explanation", None)
            (comp.get("probes") or {}).clear()
            self._emit(
                state,
                "approval_invalidated",
                phase="plan",
                detail=(
                    f"suggestion {sid} accepted and changes the plan: "
                    "approval cleared, comprehension reset -- revise the "
                    "plan, then the gates ask again"
                ),
            )
        self.save(state)
        return decided[sid]

    def comprehension_record_block(self, state: LoopState) -> str:
        """The exact 'comprehension check' subsection to paste at the end of
        the plan's decisions section: explanation summary, probes asked and
        answered, suggestions made and accepted/rejected with reasons."""
        comp = state.comprehension or {}
        lines = ["### Comprehension check"]
        explanation = (comp.get("explanation") or {}).get("text", "")
        if explanation:
            summary = re.sub(r"\s+", " ", explanation).strip()[:200]
            lines.append(f"Explanation (human's own words): {summary}")
        probes = comp.get("probes") or {}
        if probes:
            lines.append("Probes asked and answered:")
            for qid, question in self.comprehension_probes(state):
                record = probes.get(qid)
                if record is None:
                    lines.append(f"{qid}: {question} -> unanswered")
                else:
                    answer = re.sub(r"\s+", " ", record.get("answer", ""))
                    lines.append(
                        f"{qid}: {question} -> answered: {answer[:120]}"
                    )
        suggestions = comp.get("suggestions") or {}
        if suggestions:
            lines.append("Suggestions:")
            for sid in sorted(suggestions):
                record = suggestions[sid]
                lines.append(
                    f"{sid}: {record.get('text', '')[:100]} -> "
                    f"{record.get('verdict')}: {record.get('reason', '')[:120]}"
                )
        lines.append("")
        return "\n".join(lines)

    def describe_comprehension(self, state: LoopState) -> list[str]:
        """Status lines for the plan gate: thinking runs or waiver, probes,
        explanation, and suggestions with their accepted/rejected state."""
        lines: list[str] = []
        if state.thinking_runs:
            modes = ", ".join(r.get("mode", "?") for r in state.thinking_runs)
            lines.append(f"THINKING  ran: {modes}")
        elif state.thinking_waiver is not None:
            waiver = state.thinking_waiver
            lines.append(
                f"THINKING  waived by={waiver.get('by')}: {waiver.get('reason')}"
            )
        else:
            lines.append(
                "THINKING  none recorded: run one mode "
                f"(`awino loop think --mode premortem --record <file> --id {state.id}`) "
                "or waive explicitly at approve"
            )
        for qid, question in self.comprehension_probes(state):
            answered = (state.comprehension.get("probes") or {}).get(qid)
            mark = "ANSWERED" if answered else "PROBE"
            lines.append(f"{mark} {qid}: {question}")
        if (state.comprehension.get("explanation") or {}).get("text"):
            lines.append("EXPLAINED  human explanation recorded")
        else:
            lines.append(
                "EXPLAIN  write the plan in your own words: "
                f'`awino loop explain --text "..." --id {state.id}`'
            )
        for suggestion in self.plan_suggestions(state):
            decided = (state.comprehension.get("suggestions") or {}).get(
                suggestion.id
            )
            if decided:
                lines.append(
                    f"SUGGESTION {suggestion.id} {decided['verdict']}: "
                    f"{suggestion.text[:80]}"
                )
            else:
                lines.append(
                    f"SUGGESTION {suggestion.id} [{suggestion.kind}]: "
                    f"{suggestion.text[:100]}"
                )
        return lines

    def _teach_back_lines(self, state: LoopState) -> list[str]:
        """Entering teach-back: the driver explains the concept in its own
        words, then asks the human to explain it back. The plan does not
        advance."""
        lines = [
            "TEACH_BACK  the plan does not advance until you can explain it back"
        ]
        heads = self.probed_decision_heads(state)
        if heads:
            lines.append(
                "TEACH_BACK  the concept, in the driver's words (not yours):"
            )
            for head in heads:
                lines.append(f"TEACH_BACK    - {head}")
        lines.append(
            "TEACH_BACK  explain it back: "
            f'`awino loop explain --text "..." --id {state.id}`'
        )
        for qid, question in self.comprehension_probes(state):
            lines.append(f"TEACH_BACK  probe {qid}: {question}")
        return lines

    def _check_advance_allowed(self, state: LoopState) -> None:
        # The lawyer move: research advances only on a problem the user
        # confirmed is the actual problem. Pair-planning -- and the direct
        # research -> plan path when no pairing brief exists -- cannot start
        # on an unconfirmed problem. Forgetting is impossible: the gate asks
        # the stated-vs-reframed question every time.
        if state.phase == "research" and state.problem_confirmation is None:
            raise ProblemUnconfirmed(self.problem_question(state))
        if state.phase == "plan" and not self.plan_approved(state):
            raise ApprovalRequired(
                "plan is not approved; human approval is required between plan "
                "and implement -- run `awino loop approve --by NAME --reason ...`"
            )
        if state.phase == "pair-plan":
            unanswered = self.unanswered_questions(state)
            if unanswered:
                raise PairingIncomplete(unanswered)
        # "Execute when comfortable and understanding": approval already
        # requires comprehension, so this backstop fires only when the
        # comprehension records were cleared after approval without the
        # approval going with them. Forgetting is impossible: the gate asks
        # every time.
        if state.phase == "plan" and self.plan_approved(state):
            missing = self.comprehension_missing(state)
            if missing:
                raise ComprehensionRequired(missing, self._teach_back_lines(state))

    def status_next(self, state: LoopState) -> str:
        if state.phase == "pair-plan" and not state.locked:
            unanswered = self.unanswered_questions(state)
            if unanswered:
                qid = unanswered[0]
                return (
                    f"`awino loop answer --question {qid} --answer \"...\" "
                    f"--id {state.id}` (or `awino loop default --question {qid} "
                    "--reason \"...\")"
                )
            return f"all questions answered: `awino loop next --id {state.id}`"
        return super().status_next(state)

    def approval_line(self, state: LoopState) -> str:
        latest = state.approvals[-1] if state.approvals else None
        if latest:
            return (
                f"approval: approved by={latest['by']} at={latest['at']} "
                f"reason={latest.get('reason') or '(none)'}"
            )
        return "approval: pending (required between plan and implement)"

    def completion_lines(self, state: LoopState) -> list[str]:
        lines = [
            f"HANDOFF  gate run {state.gate_run_id} now owns completion",
            f"Close the work with: awino gate close --run {state.gate_run_id}",
        ]
        if self.completion_seed_note:
            lines.append(f"SEED  {self.completion_seed_note}")
        return lines

    def _complete(self, state: LoopState) -> str:
        # Handoff, not completion: record which ledger run owns the work
        # from here. The gate ledger's close is the completion authority.
        run_id = self.open_rpi_run() if self.open_rpi_run else None
        state.gate_run_id = run_id
        note = (
            "execution now owned by the gate ledger; the driver stops "
            "here. Close the work with `awino gate close --run "
            f"{run_id}`"
        )
        # The gate evidence flow owns the seed from here: the loop hands the
        # seed id to the handoff detail so the gate knows what it is
        # carrying, but it does NOT close the seed -- `awino gate close`
        # does not close an open seed. The operator must still finish the
        # gate evidence, work-close, and gate close steps.
        detail = {"gate_run_id": run_id, "at": datetime.now(UTC).isoformat()}
        if state.seed_id:
            detail["seed_id"] = state.seed_id
            note += (
                f" Linked seed {state.seed_id} stays open: finish gate "
                f"evidence, run `awino work-close --run {run_id}`, then "
                f"`awino gate close --run {run_id}`; the seed closes with "
                "the gate evidence, not with this loop."
            )
        detail["note"] = note
        state.handoff = detail
        state.phase = "done"
        self._emit(
            state,
            "loop_closed",
            phase="implement",
            detail=f"handoff to gate run {run_id}; close with `awino gate close --run {run_id}`",
        )
        self.save(state)
        self.completion_seed_note = self._rpi_seed_note(state)
        return "done"

    def _rpi_seed_note(self, state: LoopState) -> str:
        if not state.seed_id:
            return ""
        run = state.gate_run_id or "<run>"
        return (
            f"seed {state.seed_id} remains open: implement handed off, but "
            f"the gate evidence flow owns it from here. Finish gate evidence, "
            f"run `awino work-close --run {run}`, then `awino gate close "
            f"--run {run}`; the seed closes with the gate evidence, not with "
            "this loop."
        )


class RalphDriver(LoopDriver):
    """Attempt -> verify -> retry, until the check passes or three verifications
    fail. The check command is ground truth: the attempt's self-report never
    enters the loop state, and failure evidence feeds the next attempt."""

    loop_kind = "ralph"
    phase_order = ("attempt", "verify", "retry")
    artifact_phases = ("attempt", "retry")
    no_artifact_note = "(none -- the driver runs the check command itself)"

    loop_purpose = (
        "get a task truly done: attempt the work, verify with a real command, "
        "retry with the failure evidence until it passes"
    )
    phase_blurbs: ClassVar[dict[str, str]] = {
        "attempt": "write the attempt artifact describing the work",
        "verify": "the driver runs the check command: its exit code is ground truth, not the attempt's self-report",
        "retry": "write a corrected attempt informed by the verification failure evidence",
    }
    human_roles: ClassVar[dict[str, str]] = {
        "attempt": "write the attempt artifact, then run `awino loop next`",
        "verify": "nothing -- the driver runs the check command itself",
        "retry": "write a corrected attempt addressing the failure evidence, then run `awino loop next`",
    }
    next_commands: ClassVar[dict[str, str]] = {
        "attempt": "write the attempt artifact, then run `awino loop next --id {id}`",
        "verify": "run `awino loop next --id {id}` to run the check command",
        "retry": "write the corrected attempt, then run `awino loop next --id {id}`",
    }
    check_whys: ClassVar[dict[str, str]] = {
        "attempt": "an attempt too short to describe the work is not an attempt",
        "verify": "the check command is the whole point: it decides, and its output is the evidence",
        "retry": "a retry that says nothing new never fixes anything",
    }

    def phases(self) -> list[Phase]:
        return [RalphAttemptPhase(), RalphVerifyPhase(), RalphRetryPhase()]

    def new(
        self,
        task: str,
        topic: str | None = None,
        seed_id: str | None = None,
        check: str = "",
    ) -> LoopState:
        if not check:
            raise LoopError('ralph loops need a check command: --check "<command>"')
        state = super().new(task, topic=topic, seed_id=seed_id)
        state.check_command = check
        self.save(state)
        return state

    def _declare_artifacts(self, state: LoopState, stamp: str, topic: str) -> None:
        state.ralph_artifact = f"thoughts/ralph/{stamp}-{topic}.md"

    def _check_advance_allowed(self, _state: LoopState) -> None:
        """Ralph has no advance gates: verify routing happens inside advance()."""

    def artifact_path(self, state: LoopState) -> str | None:
        return self.phase_artifact(state, state.phase)

    def phase_artifact(self, state: LoopState, phase_name: str) -> str | None:
        if phase_name in ("attempt", "retry"):
            return state.ralph_artifact
        return None

    def check_before_advance(self, state: LoopState) -> bool:
        return state.phase != "verify"

    def advance(self, state: LoopState) -> str:
        if state.phase == "verify":
            return self._advance_from_verify(state)
        return super().advance(state)

    def _advance_from_verify(self, state: LoopState) -> str:
        """Run the check command and route on its outcome: passed -> done,
        failed -> retry, third failure -> structured escalation. The check
        runs here (not in the CLI's pre-check) so a failed verification
        routes instead of counting as an artifact validation failure."""
        if state.locked:
            raise LoopLocked(
                f"loop {state.id} is locked after 3 failed verifications; "
                "a human must intervene"
            )
        problems = self.check(state)  # runs the command, records evidence
        if not problems:
            return super().advance(state)  # _next_phase(verify) is None -> done
        failures = sum(
            1 for record in state.verify_history if record.get("outcome") == "failed"
        )
        if failures >= 3:
            self.escalate(state)
            return "done"
        state.phase = "retry"
        state.attempts["retry"] = 0
        state.validation_armed = True
        self._emit(
            state,
            "phase_started",
            detail=f"advanced from 'verify' after failed verification #{failures}",
        )
        self.save(state)
        return "retry"

    def _next_phase(self, state: LoopState) -> str | None:
        # attempt -> verify (linear). A passed verification ends the loop --
        # advance() only gets here when the check passed. A corrected retry
        # goes back to verify.
        if state.phase == "verify":
            return None
        if state.phase == "retry":
            return "verify"
        return super()._next_phase(state)

    def _complete(self, state: LoopState) -> str:
        state.phase = "done"
        self.save(state)
        last = state.verify_history[-1] if state.verify_history else {}
        self._emit(
            state,
            "loop_closed",
            phase="verify",
            detail=(
                f"verification passed: `{state.check_command}` exited "
                f"{last.get('exit_code', 0)}"
            ),
        )
        self._maybe_close_seed(state)
        return "done"

    def escalate(self, state: LoopState) -> str:
        """Three failed verifications: stop and hand a structured report to a
        human. The report says what was tried, what the evidence was each
        time, why the loop stopped, and the exact next human action. The loop
        is done and locked; the seed stays open because the work is not done."""
        state.phase = "done"
        state.locked = True
        self.save(state)
        report = self._escalation_report(state)
        self._emit(state, "loop_closed", phase="verify", detail=report)
        checklist = self._checklist()
        if checklist is not None:
            checklist.note_blocked(
                state.id,
                "escalated after 3 failed verifications; a human must intervene",
            )
        if state.seed_id:
            self.completion_seed_note = self.seed_open_note(state)
        return "done"

    def _escalation_report(self, state: LoopState) -> str:
        lines = [
            f"RALPH ESCALATION -- loop {state.id}",
            f"task: {state.task}",
            f"check command: `{state.check_command}`",
            "",
            "what was tried (and the evidence each time):",
        ]
        for record in state.verify_history:
            exit_code = record.get("exit_code")
            lines.append(
                f"- attempt {record.get('attempt')}: exited "
                f"{exit_code if exit_code is not None else 'unknown'} "
                f"at {record.get('at')}"
            )
            tail = record.get("output_tail") or record.get("error") or "no output"
            lines.append("  evidence:")
            lines.extend(f"    {line}" for line in tail.splitlines())
        lines += [
            "",
            "why it stopped: the check command failed three times; the loop "
            "does not get a fourth attempt on its own.",
            "exact next human action: fix the underlying problem by hand "
            "(the evidence above shows where), then either re-run the check "
            f"command `{state.check_command}` yourself or start a new loop.",
        ]
        if state.seed_id:
            lines.append(self.seed_open_note(state))
        return "\n".join(lines)

    def completion_lines(self, state: LoopState) -> list[str]:
        if state.locked:
            # Escalation: the structured report is the output.
            return self._escalation_report(state).splitlines()
        last = state.verify_history[-1] if state.verify_history else {}
        lines = [
            f"COMPLETE  verification passed: `{state.check_command}` exited "
            f"{last.get('exit_code', 0)}"
        ]
        if self.completion_seed_note:
            lines.append(f"SEED  {self.completion_seed_note}")
        return lines

    def lock_next(self, _state: LoopState) -> str:
        return (
            "the check command failed three times: fix the underlying problem "
            "by hand, then re-run the check command yourself or start a new loop"
        )

    def _seed_close_evidence(self, state: LoopState) -> str:
        last = state.verify_history[-1] if state.verify_history else {}
        return (
            f"verification passed: `{state.check_command}` exited "
            f"{last.get('exit_code', 0)}"
        )


class DelegateDriver(LoopDriver):
    """Decompose -> assign -> execute -> controller-verify. The controller
    decomposes work with per-worker file ownership, machine-checks ownership
    before execution, and re-verifies every done claim with a real check or a
    real output file. A false done fails and names the worker."""

    loop_kind = "delegate"
    phase_order = ("decompose", "assign", "execute", "controller-verify")
    artifact_phases = ("decompose", "execute")
    no_artifact_note = "(none -- this phase is a machine check, not an artifact)"

    loop_purpose = (
        "split work across workers without collisions: decompose with file "
        "ownership, check ownership before anyone starts, re-verify every "
        "done claim with real evidence"
    )
    phase_blurbs: ClassVar[dict[str, str]] = {
        "decompose": "break the task into worker assignments, each with exclusive files: ownership",
        "assign": "the driver machine-checks ownership: no overlaps, every claimed path exists",
        "execute": "workers do the work; record each worker's done claim with its evidence",
        "controller-verify": "the driver re-verifies every done claim: run the declared check, or require the declared output file",
    }
    human_roles: ClassVar[dict[str, str]] = {
        "decompose": "write the decompose artifact with worker assignments and files: ownership",
        "assign": "nothing -- the driver rejects overlapping ownership itself",
        "execute": "run the workers, then write the execute artifact with each worker's done claim",
        "controller-verify": "nothing -- the driver re-verifies every claim itself",
    }
    next_commands: ClassVar[dict[str, str]] = {
        "decompose": "write the decompose artifact, then run `awino loop next --id {id}`",
        "assign": "run `awino loop next --id {id}` (machine-checks ownership)",
        "execute": "write the execute artifact, then run `awino loop next --id {id}`",
        "controller-verify": "run `awino loop next --id {id}` (re-verifies every done claim)",
    }
    check_whys: ClassVar[dict[str, str]] = {
        "decompose": "a worker without exclusive file ownership is a merge conflict in writing",
        "assign": "overlap is cheapest to reject before any work starts",
        "execute": "a worker with no done claim has nothing the controller can verify",
        "controller-verify": "a done claim is what the worker said; the re-verification is what the machine checks",
    }

    def phases(self) -> list[Phase]:
        return [
            DelegateDecomposePhase(),
            DelegateAssignPhase(),
            DelegateExecutePhase(),
            DelegateVerifyPhase(),
        ]

    def _declare_artifacts(self, state: LoopState, stamp: str, topic: str) -> None:
        state.decompose_artifact = f"thoughts/delegate/{stamp}-{topic}-decompose.md"
        state.execute_artifact = f"thoughts/delegate/{stamp}-{topic}-execute.md"

    def _check_advance_allowed(self, _state: LoopState) -> None:
        """Delegate has no advance gates: controller verification is a phase."""

    def artifact_path(self, state: LoopState) -> str | None:
        return self.phase_artifact(state, state.phase)

    def phase_artifact(self, state: LoopState, phase_name: str) -> str | None:
        return {
            "decompose": state.decompose_artifact,
            "execute": state.execute_artifact,
        }.get(phase_name)

    def _complete(self, state: LoopState) -> str:
        state.phase = "done"
        self.save(state)
        self._emit(
            state,
            "loop_closed",
            phase="controller-verify",
            detail="all worker done claims re-verified against real evidence",
        )
        self._maybe_close_seed(state)
        return "done"

    def completion_lines(self, _state: LoopState) -> list[str]:
        lines = ["COMPLETE  all worker done claims re-verified against real evidence"]
        if self.completion_seed_note:
            lines.append(f"SEED  {self.completion_seed_note}")
        return lines

    def _seed_close_evidence(self, _state: LoopState) -> str:
        return "all worker done claims re-verified"
