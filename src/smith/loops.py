"""owns: awino loop run rpi, awino loop next, awino loop status, awino loop approve

The RPI loop driver. The machine drives phases; the model thinks inside them.

RPI (research -> plan -> implement, from skills/awino-rpi/SKILL.md) fails when
the agent drifts, so the driver owns the sequencing: it hands the model the
phase prompt, waits for the artifact, machine-checks the artifact, and only
then advances. Where the driver hands off to prose -- the model doing the
thinking -- the code says so in comments. Nothing here pretends to do the
model's thinking; it only verifies the shape of what came back.

Loop state persists as JSON under the project's state root
(<state_root>/loops/<id>.json) so it survives restarts.
"""

from __future__ import annotations

import abc
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MAX_ATTEMPTS = 3
RESEARCH_MIN_CHARS = 200
FILE_LINE_RE = re.compile(r"\S+:\d+")

PHASE_ORDER = ("research", "plan", "implement")

# Required plan headings (lowercase). Each maps to accepted synonyms; a heading
# matches when it contains a synonym case-insensitively.
PLAN_SECTIONS: dict[str, tuple[str, ...]] = {
    "phases": ("phase", "phases"),
    "scope": ("scope",),
    "tests": ("test", "tests", "testing"),
    "rollback": ("rollback", "roll back"),
    "acceptance criteria": ("acceptance", "acceptance criteria", "success criteria"),
}

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


class LoopError(RuntimeError):
    """Anything that stops the loop from advancing."""


class ApprovalRequired(LoopError):
    """Plan validated but no human approval recorded."""


class LoopLocked(LoopError):
    """A phase failed validation three times; a human must intervene."""


def slugify(text: str, max_words: int = 6) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return "-".join(slug.split("-")[:max_words]) or "task"


def phase_prompt_text(skill_md: Path, phase: str) -> str:
    """Extract the phase's prompt block from the RPI skill document.

    Docs become the script, not the sequencer: the prompt the model sees is the
    skill's own phase text. If the skill document is missing the section, this
    raises instead of improvising a substitute prompt.
    """
    index = {"research": 1, "plan": 2, "implement": 3}[phase]
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoopError(f"cannot read RPI skill document at {skill_md}: {exc}") from exc
    start = re.search(rf"^## Phase {index}\b.*$", text, re.MULTILINE)
    if start is None:
        raise LoopError(
            f"{skill_md} has no '## Phase {index}' section; "
            "refusing to invent a prompt where the docs should be"
        )
    next_heading = re.search(
        rf"^## (Phase {index + 1}|Reporting)\b.*$", text[start.end() :], re.MULTILINE
    )
    end = start.end() + next_heading.start() if next_heading else len(text)
    return text[start.start() : end].strip()


@dataclass
class LoopState:
    id: str
    task: str
    topic: str
    phase: str  # one of PHASE_ORDER, or "done"
    attempts: dict[str, int] = field(default_factory=dict)
    approvals: list[dict] = field(default_factory=list)
    locked: bool = False
    created_at: str = ""
    research_artifact: str = ""
    plan_artifact: str = ""
    gate_run_id: str | None = None
    handoff: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LoopState":
        return cls(**data)


class Phase(abc.ABC):
    """One RPI phase: a prompt for the model plus a machine-checkable validator.

    The prompt text is prose for the model; validate() is the machine's half.
    """

    name: str

    @abc.abstractmethod
    def prompt_block(self, driver: "LoopDriver") -> str:
        """The text shown to the model when this phase starts."""

    @abc.abstractmethod
    def validate(self, driver: "LoopDriver", state: LoopState) -> list[str]:
        """Return the list of exactly-what-is-missing items; empty means pass."""


class ResearchPhase(Phase):
    name = "research"

    def prompt_block(self, driver: "LoopDriver") -> str:
        return phase_prompt_text(driver.skill_md, "research")

    def validate(self, driver: "LoopDriver", state: LoopState) -> list[str]:
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


class PlanPhase(Phase):
    name = "plan"

    def prompt_block(self, driver: "LoopDriver") -> str:
        return phase_prompt_text(driver.skill_md, "plan")

    def validate(self, driver: "LoopDriver", state: LoopState) -> list[str]:
        path = driver.project_root / state.plan_artifact
        rel = state.plan_artifact
        if not path.is_file():
            return [f"plan artifact missing: {rel} -- write it, then run `awino loop next`"]
        text = path.read_text(encoding="utf-8", errors="replace")
        headings = [h.lower() for h in _HEADING_RE.findall(text)]
        missing: list[str] = []
        for section, synonyms in PLAN_SECTIONS.items():
            if not any(any(s in h for s in synonyms) for h in headings):
                missing.append(f"plan missing required section: '{section}'")
        scope_text = _section_text(text, ("scope",))
        for scope_path in _scope_paths(scope_text):
            if not (driver.project_root / scope_path).exists():
                missing.append(f"scope path does not exist in repo: '{scope_path}'")
        return missing


class ImplementPhase(Phase):
    name = "implement"

    def prompt_block(self, driver: "LoopDriver") -> str:
        return phase_prompt_text(driver.skill_md, "implement")

    def validate(self, driver: "LoopDriver", state: LoopState) -> list[str]:
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
    """Text under the first heading matching any synonym, up to the next heading."""
    matches = list(_HEADING_RE.finditer(markdown))
    for i, match in enumerate(matches):
        heading = match.group(1).lower()
        if any(s in heading for s in synonyms):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
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


class LoopDriver(abc.ABC):
    """Sequencer for one loop kind. Subclasses declare phases; this class owns
    state persistence, the three-strikes cap, and approval recording."""

    loop_kind = "base"

    def __init__(
        self,
        project_root: Path,
        loops_dir: Path,
        skill_md: Path | None = None,
        open_rpi_run=None,
    ) -> None:
        self.project_root = project_root
        self.loops_dir = loops_dir
        self.skill_md = skill_md
        # Callback returning the id of an open ledger run with --loop rpi, or
        # None. Injected so tests can fake the ledger; the CLI wires the real
        # one. The model does the implementing; the machine only checks the
        # handoff point exists.
        self.open_rpi_run = open_rpi_run

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
            raise LoopError(f"no loop {loop_id!r}; create one with `awino loop run rpi`")
        return LoopState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def current_id(self) -> str | None:
        marker = self.loops_dir / "current"
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip() or None
        return None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def new(self, task: str, topic: str | None = None) -> LoopState:
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
            phase="research",
            attempts={name: 0 for name in PHASE_ORDER},
            approvals=[],
            locked=False,
            created_at=datetime.now(UTC).isoformat(),
            research_artifact=f"thoughts/research/{stamp}-{topic}.md",
            plan_artifact=f"thoughts/plans/{stamp}-{topic}.md",
        )
        self.save(state)
        return state

    def validate_current(self, state: LoopState) -> list[str]:
        if state.phase == "done":
            return []
        return self._phase(state.phase).validate(self, state)

    def record_failure(self, state: LoopState) -> None:
        """Count a failed validation; the third failure locks the loop.

        A phase that cannot pass three times is not converging -- escalate to
        a human instead of looping forever.
        """
        state.attempts[state.phase] = state.attempts.get(state.phase, 0) + 1
        if state.attempts[state.phase] >= MAX_ATTEMPTS:
            state.locked = True
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
        self.save(state)

    def plan_approved(self, state: LoopState) -> bool:
        return any(a.get("phase") == "plan" for a in state.approvals)

    def advance(self, state: LoopState) -> str:
        """Move to the next phase. The current phase must already validate.

        Raises ApprovalRequired when leaving plan without a recorded human
        approval, and LoopLocked when the loop is locked.
        """
        if state.locked:
            raise LoopLocked(
                f"loop {state.id} is locked after {MAX_ATTEMPTS} failed validations "
                "of a phase; a human must intervene"
            )
        if state.phase == "done":
            raise LoopError("loop is already done")
        if state.phase == "plan" and not self.plan_approved(state):
            raise ApprovalRequired(
                "plan is not approved; human approval is required between plan "
                "and implement -- run `awino loop approve --by NAME --reason ...`"
            )
        if state.phase == "implement":
            # Handoff, not completion: record which ledger run owns the work
            # from here. The gate ledger's close is the completion authority.
            run_id = self.open_rpi_run() if self.open_rpi_run else None
            state.gate_run_id = run_id
            state.handoff = {
                "gate_run_id": run_id,
                "at": datetime.now(UTC).isoformat(),
                "note": (
                    "execution now owned by the gate ledger; the driver stops "
                    "here. Close the work with `awino gate close --run "
                    f"{run_id}`"
                ),
            }
            state.phase = "done"
        else:
            state.phase = PHASE_ORDER[PHASE_ORDER.index(state.phase) + 1]
        self.save(state)
        return state.phase


class RpiDriver(LoopDriver):
    """Research -> plan -> implement, with human approval gating implement."""

    loop_kind = "rpi"

    def phases(self) -> list[Phase]:
        return [ResearchPhase(), PlanPhase(), ImplementPhase()]
