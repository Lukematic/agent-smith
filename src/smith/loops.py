"""owns: awino loop run rpi|ralph|delegate, awino loop next, awino loop status, awino loop approve, awino loop back, awino loop answer, awino loop default

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

On the graph/floor machinery (src/smith/graph.py): the delegate driver does
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

from smith import seeds
from smith.enforce import Ledger, LoopEvent

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

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)

# A trade-off marker is a proxy, not a judgment: the driver checks the shape
# of deliberation (did the brief spell out costs), not its quality.
_TRADEOFF_RE = re.compile(r"trade-?off|pro:|con:", re.IGNORECASE)
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
#   - mission source: `.smith/MISSION.md` when present, else the `goals:` list
#     in `.smith/project.yaml`. Goal texts are the level-2+ headings and list
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
    mission_md = project_root / ".smith" / "MISSION.md"
    if mission_md.is_file():
        try:
            text = mission_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        goals = _MISSION_HEADING_RE.findall(text) + _MISSION_ITEM_RE.findall(text)
        return [goal.strip() for goal in goals if goal.strip()]
    project_yaml = project_root / ".smith" / "project.yaml"
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


class LoopLocked(LoopError):
    """A phase failed validation three times; a human must intervene."""


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
    # Ralph
    ralph_artifact: str = ""
    check_command: str = ""
    verify_history: list[dict] = field(default_factory=list)
    # Delegate
    decompose_artifact: str = ""
    execute_artifact: str = ""

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
                "(e.g. 'src/smith/loops.py:42')"
            ]
        return []


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
            for name, body in _split_approach_blocks(approaches_text):
                if not _TRADEOFF_RE.search(body):
                    missing.append(
                        f"approach '{name}' has no trade-off marker: add 'trade-off', "
                        "'pro:' or 'con:' spelling out what it costs"
                    )
        questions_text = _section_text(text, ("questions",))
        if not _QUESTION_RE.findall(questions_text):
            missing.append(
                "pairing brief has no questions in 'Qn:' format "
                "(e.g. 'Q1: which approach?')"
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
    """
    entries = _decision_entries(_section_text(plan_text, ("decisions",)))
    if not entries:
        return [
            "decisions section has no decision entries: record one per pairing "
            "question (e.g. '- Q1 -> approach A because ...')"
        ]
    brief_qids = [qid for qid, _ in driver.pairing_questions(state)]
    known = ", ".join(brief_qids) if brief_qids else "(none recorded)"
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
        elif refs:
            continue  # traced to a recorded pairing question
        else:
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
        # Set by check() after each first-validation of an artifact: the
        # mission goals the artifact did not address, or None. The CLI prints
        # these as the human's drift notice. Transient; reset on every check().
        self.last_drift: list[str] | None = None
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
        self.save(state)
        detail = f"task: {task}"
        if state.seed_id is not None:
            detail += f"; seed: {state.seed_id}"
        self._emit(state, "loop_started", detail=detail)
        self._emit(state, "phase_started", detail="initial phase")
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
        missing = self.validate_current(state)
        if missing:
            self.record_failure(state, missing)
        elif state.phase in self.artifact_phases and self.record_artifact_validated(state):
            self.last_drift = self._check_mission_alignment(state)
        return missing

    def artifact_path(self, _state: LoopState) -> str | None:
        """The artifact this loop's current phase produces, or None when the
        phase is a machine check (verify, assign, controller-verify,
        implement)."""
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

    def approve_plan(self, state: LoopState, by: str, reason: str) -> None:
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

    def plan_approved(self, state: LoopState) -> bool:
        return any(a.get("phase") == "plan" for a in state.approvals)

    def check_before_advance(self, _state: LoopState) -> bool:
        """Whether the CLI should run check() before advance(). Ralph's
        verify phase returns False: its check command runs inside advance()
        because the outcome routes the transition (retry vs done vs
        escalate) instead of counting as an artifact validation failure."""
        return True

    def advance(self, state: LoopState) -> str:
        """Move to the next phase. The current phase must already validate
        (the CLI checks first); this enforces the human gates and transitions.

        Raises ApprovalRequired/PairingIncomplete when a human gate is
        unsatisfied, and LoopLocked when the loop is locked.
        """
        if state.locked:
            raise LoopLocked(
                f"loop {state.id} is locked after {MAX_ATTEMPTS} failed validations "
                "of a phase; a human must intervene"
            )
        if state.phase == "done":
            raise LoopError("loop is already done")
        self._check_advance_allowed(state)
        nxt = self._next_phase(state)
        if nxt is None:
            return self._complete(state)
        previous = state.phase
        state.phase = nxt
        self._emit(state, "phase_started", detail=f"advanced from '{previous}'")
        self.save(state)
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
        clears a three-strikes lock. Approvals are left untouched.
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

    # ── pair-planning hooks (neutral defaults; RPI overrides) ─────────────
    def pairing_questions(self, _state: LoopState) -> list[tuple[str, str]]:
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
        "pair-plan": "a brief without trade-offs or questions is a plan wearing a disguise",
        "plan": "an untraced decision is a guess the pairing phase exists to prevent",
    }

    def phases(self) -> list[Phase]:
        return [ResearchPhase(), PairPlanPhase(), PlanPhase(), ImplementPhase()]

    def _declare_artifacts(self, state: LoopState, stamp: str, topic: str) -> None:
        state.research_artifact = f"thoughts/research/{stamp}-{topic}.md"
        state.pairing_artifact = f"thoughts/pairing/{stamp}-{topic}.md"
        state.plan_artifact = f"thoughts/plans/{stamp}-{topic}.md"

    def artifact_path(self, state: LoopState) -> str | None:
        return {
            "research": state.research_artifact,
            "pair-plan": state.pairing_artifact,
            "plan": state.plan_artifact,
        }.get(state.phase)

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

    def unanswered_questions(self, state: LoopState) -> list[str]:
        asked = [qid for qid, _ in self.pairing_questions(state)]
        return [qid for qid in asked if qid not in state.pair_answers]

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
        """Status lines for the pair-plan phase: what's asked, what's
        answered, what's still open."""
        lines: list[str] = []
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
        return record

    def _check_advance_allowed(self, state: LoopState) -> None:
        if state.phase == "plan" and not self.plan_approved(state):
            raise ApprovalRequired(
                "plan is not approved; human approval is required between plan "
                "and implement -- run `awino loop approve --by NAME --reason ...`"
            )
        if state.phase == "pair-plan":
            unanswered = self.unanswered_questions(state)
            if unanswered:
                raise PairingIncomplete(unanswered)

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
        if state.phase in ("attempt", "retry"):
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
        return {
            "decompose": state.decompose_artifact,
            "execute": state.execute_artifact,
        }.get(state.phase)

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
