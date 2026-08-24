"""Enforcement: agents earn completion, they never claim it.

The problem this solves: prose instructions decay. "Always verify before saying
done" competes with every other line in the prompt and loses. Telling the model
harder is a ``PROMPT_PATCH``.

So completion becomes a computed verdict rather than an assertion. A task opens a
run, gates are declared up front from its task class, and each gate closes only
when a real command has been executed and its exit code and output recorded. The
agent calls ``awino gate close``, which either passes or refuses and prints
exactly which gates are unmet.

Evidence is captured by running the command, not by the agent describing it. An
agent cannot forge an exit code it did not produce.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

MAX_ATTEMPTS = 3
OUTPUT_KEEP_CHARS = 4000


class Gate(StrEnum):
    """The checkable obligations a run can carry."""

    PLANNED = "planned"
    RESEARCHED = "researched"
    TESTED = "tested"
    LINTED = "linted"
    REVIEWED = "reviewed"
    VALIDATED = "validated"
    TESTS_NOT_WEAKENED = "tests_not_weakened"
    SCOPE_RESPECTED = "scope_respected"
    LESSON_RECORDED = "lesson_recorded"


class TaskClass(StrEnum):
    """What kind of work this is. Determines which gates apply."""

    QUESTION = "question"
    CODE_CHANGE = "code-change"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    AUTHORING = "authoring"
    RESEARCH = "research"


class PlanDecision(StrEnum):
    """A durable human decision about the run's plan."""

    APPROVED = "approved"
    HELD = "held"
    REJECTED = "rejected"


# Which gates each task class must satisfy. This table is the contract: it is
# data, so it can be tested, diffed, and reviewed, unlike a paragraph of prose.
CONTRACTS: dict[TaskClass, tuple[Gate, ...]] = {
    TaskClass.QUESTION: (),
    TaskClass.RESEARCH: (Gate.RESEARCHED,),
    TaskClass.CODE_CHANGE: (
        Gate.PLANNED,
        Gate.TESTED,
        Gate.LINTED,
        Gate.TESTS_NOT_WEAKENED,
        Gate.SCOPE_RESPECTED,
    ),
    TaskClass.BUGFIX: (
        Gate.RESEARCHED,
        Gate.TESTED,
        Gate.LINTED,
        Gate.TESTS_NOT_WEAKENED,
        Gate.LESSON_RECORDED,
    ),
    TaskClass.REFACTOR: (
        Gate.PLANNED,
        Gate.TESTED,
        Gate.LINTED,
        Gate.TESTS_NOT_WEAKENED,
        Gate.SCOPE_RESPECTED,
        Gate.REVIEWED,
    ),
    TaskClass.AUTHORING: (Gate.PLANNED, Gate.VALIDATED, Gate.LESSON_RECORDED),
}

# A bugfix that never reproduced the bug did not diagnose it, it guessed.
DIAGNOSTIC_GATES: frozenset[Gate] = frozenset({Gate.RESEARCHED})


@dataclass(frozen=True)
class Evidence:
    """Proof that a gate was satisfied by an executed command."""

    gate: str
    command: str
    exit_code: int
    output_hash: str
    output_head: str
    duration_ms: int
    recorded_at: str
    attempt: int = 1

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class PlanDecisionRecord:
    """A decision bound to the exact plan bytes and declared file scope."""

    decision: str
    plan_path: str
    plan_sha256: str
    approved_scope: list[str]
    approver: str
    reason: str
    decided_at: str


@dataclass(frozen=True)
class Checkpoint:
    """Durable continuation state, including any human decision boundary."""

    checkpoint_id: str
    phase: str
    summary: str
    next_action: str
    pending_decision: str | None
    options: list[str]
    selected_decision: str | None
    decided_by: str | None
    created_at: str
    decided_at: str | None


@dataclass(frozen=True)
class SkillEvent:
    """A durable record of a skill being loaded or used."""

    name: str
    state: str
    reason: str
    recorded_at: str


@dataclass
class Run:
    """One unit of work with a declared contract and an evidence ledger."""

    run_id: str
    task_class: str
    objective: str
    opened_at: str
    required: list[str] = field(default_factory=list)
    file_scope: list[str] = field(default_factory=list)
    skills_loaded: list[str] = field(default_factory=list)
    schema_version: int = 2
    plan_path: str | None = None
    issue_id: str | None = None
    issue_started_at: str | None = None
    plan_decisions: list[PlanDecisionRecord] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    skill_events: list[SkillEvent] = field(default_factory=list)
    escalated_gates: list[str] = field(default_factory=list)
    closed_at: str | None = None
    verdict: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Run:
        """Load both legacy flat runs and the current nested schema."""
        values = dict(data)
        values.setdefault("schema_version", 1)
        values.setdefault("required", [])
        values.setdefault("file_scope", [])
        values.setdefault("skills_loaded", [])
        values.setdefault("plan_path", None)
        values.setdefault("issue_id", None)
        values.setdefault("issue_started_at", None)
        values["plan_decisions"] = [
            PlanDecisionRecord(**item) for item in values.get("plan_decisions", [])
        ]
        values["checkpoints"] = [Checkpoint(**item) for item in values.get("checkpoints", [])]
        values["skill_events"] = [SkillEvent(**item) for item in values.get("skill_events", [])]
        values.setdefault("escalated_gates", [])
        values.setdefault("closed_at", None)
        values.setdefault("verdict", None)
        return cls(**values)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CurrentInspection:
    """Whether CURRENT identifies active work or an unusable pointer."""

    status: str
    run_id: str | None
    run: Run | None


class LedgerError(RuntimeError):
    """Raised when the ledger is asked to do something the contract forbids."""


class Ledger:
    """Append-only run store for one project.

    Takes the *state root* directly rather than deriving it, because where state
    belongs is a workspace-layout decision and only ``Workspace`` knows it. A
    ledger that guessed its own location would recreate the nesting bug it exists
    to avoid.

    The ledger belongs to the repository being worked on, never to A.W.I.N.O. home. A
    shared A.W.I.N.O. install that accumulated every project's runs would make
    attribution impossible and leak one project's state into another.
    """

    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.base = state_root / "run"

    # ── locations ────────────────────────────────────────────────────────────
    def run_dir(self, run_id: str) -> Path:
        return self.base / run_id

    def _meta(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.json"

    def _evidence(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "evidence.jsonl"

    def _current(self) -> Path:
        return self.base / "CURRENT"

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(
        self,
        task_class: TaskClass,
        objective: str,
        *,
        file_scope: list[str] | None = None,
        extra_gates: list[Gate] | None = None,
        plan_path: Path | None = None,
        issue_id: str | None = None,
    ) -> Run:
        run_id = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        required = list(CONTRACTS[task_class]) + list(extra_gates or [])
        run = Run(
            run_id=run_id,
            task_class=str(task_class),
            objective=objective,
            opened_at=datetime.now(UTC).isoformat(),
            required=[str(g) for g in dict.fromkeys(required)],
            file_scope=file_scope or [],
            plan_path=str(plan_path) if plan_path is not None else None,
            issue_id=issue_id,
        )
        self.run_dir(run_id).mkdir(parents=True, exist_ok=True)
        self._meta(run_id).write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
        self._evidence(run_id).touch()
        self._current().write_text(run_id, encoding="utf-8")
        return run

    def current_id(self) -> str | None:
        if not self._current().is_file():
            return None
        value = self._current().read_text(encoding="utf-8").strip()
        return value or None

    def load(self, run_id: str) -> Run:
        meta = self._meta(run_id)
        if not meta.is_file():
            raise LedgerError(f"no such run {run_id!r}")
        return Run.from_dict(json.loads(meta.read_text(encoding="utf-8")))

    def save(self, run: Run) -> None:
        self._meta(run.run_id).write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")

    def inspect_current(self) -> CurrentInspection:
        """Inspect CURRENT without mistaking a closed or missing run for active work."""
        run_id = self.current_id()
        if run_id is None:
            return CurrentInspection("none", None, None)
        try:
            run = self.load(run_id)
        except LedgerError:
            return CurrentInspection("broken", run_id, None)
        status = "active" if run.closed_at is None else "stale"
        return CurrentInspection(status, run_id, run)

    def evidence(self, run_id: str) -> list[Evidence]:
        path = self._evidence(run_id)
        if not path.is_file():
            return []
        out: list[Evidence] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(Evidence(**json.loads(line)))
        return out

    def append(self, run_id: str, item: Evidence) -> None:
        with self._evidence(run_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(item)) + "\n")

    # ── plan and continuation state ─────────────────────────────────────────
    def decide_plan(
        self,
        run_id: str,
        decision: PlanDecision,
        approver: str,
        reason: str = "",
    ) -> PlanDecisionRecord:
        run = self.load(run_id)
        if run.plan_path is None:
            raise LedgerError("run has no plan path")
        plan = Path(run.plan_path)
        if not plan.is_file():
            raise LedgerError(f"plan does not exist: {plan}")
        item = PlanDecisionRecord(
            decision=str(decision),
            plan_path=run.plan_path,
            plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest(),
            approved_scope=list(run.file_scope),
            approver=approver,
            reason=reason,
            decided_at=datetime.now(UTC).isoformat(),
        )
        run.plan_decisions.append(item)
        self.save(run)
        if decision == PlanDecision.APPROVED:
            note = f"approved {run.plan_path} {item.plan_sha256}"
            prior = [e for e in self.evidence(run_id) if e.gate == str(Gate.PLANNED)]
            self.append(
                run_id,
                Evidence(
                    gate=str(Gate.PLANNED),
                    command=f"ATTEST {note}",
                    exit_code=0,
                    output_hash=hashlib.sha256(note.encode("utf-8")).hexdigest()[:12],
                    output_head=note[:OUTPUT_KEEP_CHARS],
                    duration_ms=0,
                    recorded_at=datetime.now(UTC).isoformat(),
                    attempt=len(prior) + 1,
                ),
            )
        return item

    def approve_plan(self, run_id: str, approver: str, reason: str = "") -> PlanDecisionRecord:
        return self.decide_plan(run_id, PlanDecision.APPROVED, approver, reason)

    def validate_plan(self, run_id: str) -> list[str]:
        """Return every reason the latest plan decision is not currently valid."""
        run = self.load(run_id)
        if run.plan_path is None:
            return ["run has no plan path"]
        if not run.plan_decisions:
            return ["plan has no decision"]
        decision = run.plan_decisions[-1]
        if decision.decision != PlanDecision.APPROVED:
            return [f"plan is {decision.decision}"]
        plan = Path(run.plan_path)
        if not plan.is_file():
            return ["approved plan no longer exists"]
        problems: list[str] = []
        current_hash = hashlib.sha256(plan.read_bytes()).hexdigest()
        if current_hash != decision.plan_sha256:
            problems.append("approved plan hash no longer matches")
        if run.file_scope != decision.approved_scope:
            problems.append("approved scope no longer matches")
        if run.plan_path != decision.plan_path:
            problems.append("approved plan path no longer matches")
        return problems

    def checkpoint(
        self,
        run_id: str,
        phase: str,
        summary: str,
        next_action: str,
        *,
        pending_decision: str | None = None,
        options: list[str] | None = None,
    ) -> Checkpoint:
        run = self.load(run_id)
        if pending_decision is not None and not options:
            raise LedgerError("a pending decision requires at least one option")
        if any(
            item.pending_decision is not None and item.selected_decision is None
            for item in run.checkpoints
        ):
            raise LedgerError("resolve the pending decision before another checkpoint")
        item = Checkpoint(
            checkpoint_id=uuid.uuid4().hex[:12],
            phase=phase,
            summary=summary,
            next_action=next_action,
            pending_decision=pending_decision,
            options=list(options or []),
            selected_decision=None,
            decided_by=None,
            created_at=datetime.now(UTC).isoformat(),
            decided_at=None,
        )
        run.checkpoints.append(item)
        self.save(run)
        return item

    def resolve_checkpoint(self, run_id: str, selection: str, decided_by: str) -> Checkpoint:
        run = self.load(run_id)
        index = next(
            (
                index
                for index in range(len(run.checkpoints) - 1, -1, -1)
                if run.checkpoints[index].pending_decision is not None
                and run.checkpoints[index].selected_decision is None
            ),
            None,
        )
        if index is None:
            raise LedgerError("run has no pending decision")
        pending = run.checkpoints[index]
        if selection not in pending.options:
            raise LedgerError(f"{selection!r} is not a declared option")
        resolved = replace(
            pending,
            selected_decision=selection,
            decided_by=decided_by,
            decided_at=datetime.now(UTC).isoformat(),
        )
        run.checkpoints[index] = resolved
        self.save(run)
        return resolved

    # ── the part that cannot be faked ────────────────────────────────────────
    def record(self, run_id: str, gate: Gate, command: str, cwd: Path | None = None) -> Evidence:
        """Execute ``command`` and record its real result against ``gate``.

        The agent supplies the command. This function supplies the exit code. That
        asymmetry is the whole mechanism: a model can claim a test passed, but it
        cannot produce a zero exit code from a failing suite.
        """
        run = self.load(run_id)
        if run.schema_version >= 2 and run.plan_path is not None and gate != Gate.PLANNED:
            plan_problems = self.validate_plan(run_id)
            if plan_problems:
                raise LedgerError(f"PLAN_INVALID: {'; '.join(plan_problems)}")
        prior = [e for e in self.evidence(run_id) if e.gate == str(gate)]
        failures = [
            item for item in prior if not item.passed and not item.command.startswith("ATTEST ")
        ]
        if len(failures) >= MAX_ATTEMPTS:
            raise LedgerError(
                f"THREE_STRIKES on {gate}. Open a new run; this run cannot execute another attempt."
            )
        attempt = len(prior) + 1

        started = time.monotonic()
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        output = (completed.stdout or "") + (completed.stderr or "")

        item = Evidence(
            gate=str(gate),
            command=command,
            exit_code=completed.returncode,
            output_hash=hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()[:12],
            output_head=output[:OUTPUT_KEEP_CHARS],
            duration_ms=duration_ms,
            recorded_at=datetime.now(UTC).isoformat(),
            attempt=attempt,
        )
        self.append(run_id, item)
        if not item.passed and len(failures) + 1 >= MAX_ATTEMPTS:
            run = self.load(run_id)
            if str(gate) not in run.escalated_gates:
                run.escalated_gates.append(str(gate))
                self.save(run)
        return item

    def attest(self, run_id: str, gate: Gate, note: str) -> Evidence:
        """Record a gate that has no command, such as a plan document existing.

        Attestations are marked with exit code 0 but a synthetic command so a
        reader can always tell executed proof from asserted proof.
        """
        run = self.load(run_id)
        if run.schema_version >= 2 and run.plan_path is not None and gate != Gate.PLANNED:
            plan_problems = self.validate_plan(run_id)
            if plan_problems:
                raise LedgerError(f"PLAN_INVALID: {'; '.join(plan_problems)}")
        if run.schema_version >= 2 and run.plan_path is not None and gate == Gate.PLANNED:
            raise LedgerError("planned evidence must be created by approve_plan")
        prior = [e for e in self.evidence(run_id) if e.gate == str(gate)]
        item = Evidence(
            gate=str(gate),
            command=f"ATTEST {note}",
            exit_code=0,
            output_hash=hashlib.sha256(note.encode("utf-8")).hexdigest()[:12],
            output_head=note[:OUTPUT_KEEP_CHARS],
            duration_ms=0,
            recorded_at=datetime.now(UTC).isoformat(),
            attempt=len(prior) + 1,
        )
        self.append(run_id, item)
        return item

    def note_skill(
        self, run_id: str, skill: str, *, state: str = "loaded", reason: str = ""
    ) -> Run:
        if state not in {"loaded", "used", "recommended"}:
            raise LedgerError(f"invalid skill state {state!r}")
        run = self.load(run_id)
        if state in {"loaded", "used"} and skill not in run.skills_loaded:
            run.skills_loaded.append(skill)
        item = SkillEvent(
            name=skill,
            state=state,
            reason=reason,
            recorded_at=datetime.now(UTC).isoformat(),
        )
        if not any(
            event.name == skill and event.state == state and event.reason == reason
            for event in run.skill_events
        ):
            run.skill_events.append(item)
        self.save(run)
        return run


@dataclass
class Verdict:
    """Computed completion state. Never asserted by the agent."""

    run_id: str
    task_class: str
    satisfied: list[str]
    missing: list[str]
    failing: list[str]
    attempts_exceeded: list[str]
    attested_only: list[str]

    @property
    def can_close(self) -> bool:
        return not (self.missing or self.failing or self.attempts_exceeded)

    @property
    def blocked_reason(self) -> str:
        if self.attempts_exceeded:
            return (
                f"THREE_STRIKES on {', '.join(self.attempts_exceeded)}. "
                "Stop and escalate with what was tried."
            )
        if self.failing:
            return f"GATE_FAILING {', '.join(self.failing)} recorded a nonzero exit code."
        if self.missing:
            return f"GATE_MISSING {', '.join(self.missing)} has no recorded evidence."
        return ""


def adjudicate(run: Run, evidence: list[Evidence]) -> Verdict:
    """Compute whether a run may close, from the ledger alone."""
    latest: dict[str, Evidence] = {}
    failed_executions: dict[str, int] = {}
    for item in evidence:
        if not item.passed and not item.command.startswith("ATTEST "):
            failed_executions[item.gate] = failed_executions.get(item.gate, 0) + 1
        prior = latest.get(item.gate)
        if prior is None or item.attempt >= prior.attempt:
            latest[item.gate] = item

    satisfied: list[str] = []
    missing: list[str] = []
    failing: list[str] = []
    exceeded: list[str] = []
    attested: list[str] = []

    for gate in run.required:
        item = latest.get(gate)
        if item is None:
            missing.append(gate)
            continue
        if not item.passed:
            failing.append(gate)
            # A gate retried past the ceiling is not a gate to keep hammering.
            if failed_executions.get(gate, 0) >= MAX_ATTEMPTS:
                exceeded.append(gate)
            continue
        satisfied.append(gate)
        if item.command.startswith("ATTEST "):
            attested.append(gate)

    return Verdict(
        run_id=run.run_id,
        task_class=run.task_class,
        satisfied=satisfied,
        missing=missing,
        failing=failing,
        attempts_exceeded=exceeded,
        attested_only=attested,
    )


# ── independent checks that do not trust the agent ───────────────────────────

WEAKENING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^\-(?!\-\-).*\bassert\b", "an assertion was deleted"),
    (r"^\+.*@pytest\.mark\.(skip|xfail)", "a test was skipped or marked xfail"),
    (r"^\+.*\bpytest\.skip\b", "a test was skipped at runtime"),
    (r"^\+.*\.skip\(", "a test was skipped"),
    (r"^\+.*\bit\.skip\b|^\+.*\bdescribe\.skip\b", "a test was skipped"),
    (r"^\-.*\bdef test_", "a test function was removed"),
)


def detect_test_weakening(diff: str) -> list[str]:
    """Find edits that make tests pass by lowering the bar.

    "Never modify a test to make it pass" is the single most-ignored instruction
    in agent work, because following it is invisible and breaking it is fast. This
    reads the diff instead of asking.
    """
    # Only test files matter here: production asserts are legitimately edited.
    findings: list[str] = []
    current_file = ""
    in_test_file = False
    for line in diff.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            current_file = line[4:].strip()
            in_test_file = bool(
                re.search(r"(^|/)(tests?|spec)/|_test\.|test_|\.test\.|\.spec\.", current_file)
            )
            continue
        if not in_test_file:
            continue
        for pattern, why in WEAKENING_PATTERNS:
            if re.search(pattern, line):
                findings.append(f"{current_file}: {why} -> {line.strip()[:80]}")
                break
    return findings


def detect_scope_violations(changed: list[str], scope: list[str]) -> list[str]:
    """Files touched that were never in the declared scope."""
    if not scope:
        return []
    allowed = {s.replace("\\", "/").lstrip("./") for s in scope}
    violations: list[str] = []
    for path in changed:
        normalised = path.replace("\\", "/").lstrip("./")
        if normalised in allowed:
            continue
        if any(
            normalised.startswith(a.rstrip("*").rstrip("/") + "/")
            for a in allowed
            if a.endswith(("*", "/"))
        ):
            continue
        violations.append(normalised)
    return violations
