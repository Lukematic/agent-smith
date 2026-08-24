"""Mental models as decision functions, not documents.

A skill is a procedure you follow. A mental model is a lens that changes a
decision. Encoding the models as prose would produce nine more documents that
never fire; encoding them as functions makes them produce *different plans*.

Four models earn their place here because each one changes a concrete decision:

- **Leverage ladder** decides what you should be authoring at all.
- **Verifier strength** decides how much autonomy is safe. This is the binding
  constraint on any loop.
- **Design as bottleneck** decides where to spend effort once implementation is
  cheap.
- **Pit of success** decides whether a design will hold without vigilance.

Each returns a verdict plus the reasoning that produced it, so a plan can be
argued with rather than merely obeyed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from smith.enforce import CONTRACTS, Evidence, Gate, TaskClass

# ── the leverage ladder ──────────────────────────────────────────────────────


class Rung(IntEnum):
    """Where the human stands relative to the work.

    Ordered because the ladder is ordered: each rung raises the unit of leverage
    and moves the human one step further from the keystroke.
    """

    PROMPT = 1
    CONTEXT = 2
    HARNESS = 3
    LOOP = 4
    FACTORY = 5

    @property
    def authored_artifact(self) -> str:
        return {
            Rung.PROMPT: "a single prompt",
            Rung.CONTEXT: "what the agent sees each turn",
            Rung.HARNESS: "the environment one agent runs in",
            Rung.LOOP: "the system that prompts the agent",
            Rung.FACTORY: "the system that builds software",
        }[self]

    @property
    def human_position(self) -> str:
        return {
            Rung.PROMPT: "inside each turn",
            Rung.CONTEXT: "curating each turn's input",
            Rung.HARNESS: "around a single agent",
            Rung.LOOP: "above the harness",
            Rung.FACTORY: "running the organization",
        }[self]


# Signals that a request is really about a higher rung than its wording suggests.
# The point of detection is that people describe loop problems as prompt problems.
#
# Order matters, and it is not simply "highest rung first". Context is checked
# before harness because "the context window keeps overflowing" contains a
# repetition signal but is unambiguously a context problem: the fix is what the
# agent sees, not the environment it runs in. A greedy harness rule would swallow
# it and send effort to the wrong surface.
RUNG_SIGNALS: tuple[tuple[Rung, re.Pattern[str], str], ...] = (
    (
        Rung.FACTORY,
        re.compile(
            r"\b(every repo|all (our|my) (repos|projects)|org[- ]wide|fleet|whole team)\b", re.I
        ),
        "spans repositories or a team, not one codebase",
    ),
    (
        Rung.LOOP,
        re.compile(
            r"\b(every (day|night|morning|hour)|nightly|on a (timer|schedule)|cron|unattended"
            r"|automatically (find|fix|triage)|without me|while I sleep|in the background)\b",
            re.I,
        ),
        "recurs on a trigger rather than on a human prompt",
    ),
    (
        Rung.CONTEXT,
        re.compile(
            r"\b(context (window|length|limit)|too (long|much) (context|history)|truncat\w+"
            r"|compact\w*|forgets? (mid|halfway|earlier)|lost in the middle|retriev\w+"
            r"|window (overflow|blow)\w*)\b",
            re.I,
        ),
        "concerns what the agent sees",
    ),
    (
        Rung.HARNESS,
        re.compile(
            # Two independent signals, because practitioners phrase this many ways.
            #
            # 1. Repetition: "keeps <verb>ing", "always", "every time". The verb is
            #    open-ended on purpose. An earlier version listed verbs explicitly
            #    and missed "keeps citing", which is the same failure as "keeps
            #    writing" and needs the same structural fix.
            # 2. Instruction-defiance: the prompt says one thing and the agent does
            #    another. That is the definition of a harness problem: the
            #    instruction exists and is not binding, so wording it harder cannot
            #    help.
            r"\b(keeps?|kept)\s+\w+ing\b"
            r"|\bnever (follows|listens|respects|obeys|does)\b"
            r"|\balways (forgets|skips|ignores|fails)\b"
            r"|\bevery (single )?time\b"
            r"|\brepeatedly\b"
            r"|\beven though (the|my|our) (prompt|instruction|rule|spec)"
            r"|\b(prompt|instruction|rule) (says|forbids|requires|tells)\b.*\b(but|yet|anyway|still)\b"
            r"|\b(ignores|violates|disregards) (the|my|our) (prompt|instruction|rule)"
            r"|\bpermissions?\b|\bhooks?\b|\bsandbox\b|\btool access\b",
            re.I,
        ),
        "a repeated behaviour or an unenforced instruction, which is an environment property",
    ),
)


@dataclass(frozen=True)
class RungVerdict:
    stated: Rung
    actual: Rung
    reason: str

    @property
    def misaligned(self) -> bool:
        return self.actual > self.stated

    @property
    def advice(self) -> str:
        if not self.misaligned:
            return (
                f"Author {self.actual.authored_artifact}. You stand {self.actual.human_position}."
            )
        return (
            f"This reads as a {self.stated.name.lower()} problem but behaves like a "
            f"{self.actual.name.lower()} problem: {self.reason}. "
            f"Author {self.actual.authored_artifact} instead of "
            f"{self.stated.authored_artifact}."
        )


def detect_rung(request: str) -> RungVerdict:
    """Infer which rung a request actually lives on.

    The common error is treating a higher-rung problem at a lower rung: rewording
    a prompt to fix an environment defect, or hand-prompting work that recurs
    nightly. Detection exists to surface that mismatch before effort is spent.
    """
    for rung, pattern, reason in RUNG_SIGNALS:
        if pattern.search(request):
            return RungVerdict(stated=Rung.PROMPT, actual=rung, reason=reason)
    return RungVerdict(stated=Rung.PROMPT, actual=Rung.PROMPT, reason="a single bounded turn")


# ── verifier strength: the binding constraint on autonomy ────────────────────


class Autonomy(IntEnum):
    """How far work may run before a human must look at it."""

    SUPERVISED = 0
    CHECKPOINTED = 1
    BOUNDED = 2
    UNATTENDED = 3

    @property
    def meaning(self) -> str:
        return {
            Autonomy.SUPERVISED: "human reviews every step, no loop",
            Autonomy.CHECKPOINTED: "human approves at phase boundaries",
            Autonomy.BOUNDED: "runs a fixed number of iterations, then reports",
            Autonomy.UNATTENDED: "may run on a trigger with no human in the turn",
        }[self]


# Executed evidence proves; attested evidence asserts. Only the former can close
# a loop, because self-grading bias compounds across unverified iterations.
STRONG_GATES: frozenset[Gate] = frozenset({Gate.TESTED, Gate.LINTED, Gate.VALIDATED})
INDEPENDENCE_GATES: frozenset[Gate] = frozenset({Gate.REVIEWED, Gate.TESTS_NOT_WEAKENED})

# Some gates can only ever be attested: a plan document either exists or it does
# not, and no command decides whether it is a good plan. Penalising those
# attestations would cap every task at checkpointed forever, which makes the
# measure useless. Only an attestation standing in for a gate that *could* have
# been executed is a weakness.
INHERENTLY_ATTESTED: frozenset[Gate] = frozenset(
    {Gate.PLANNED, Gate.RESEARCHED, Gate.LESSON_RECORDED}
)


@dataclass
class VerifierVerdict:
    executed: list[str] = field(default_factory=list)
    attested: list[str] = field(default_factory=list)
    weakly_attested: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    has_objective_check: bool = False
    has_independent_check: bool = False
    max_autonomy: Autonomy = Autonomy.SUPERVISED
    reasons: list[str] = field(default_factory=list)

    @property
    def open_loop(self) -> bool:
        """True when a loop would run without a check strong enough to gate it."""
        return not self.has_objective_check


def assess_verifier(task_class: TaskClass, evidence: list[Evidence]) -> VerifierVerdict:
    """Compute how much autonomy the current verification actually supports.

    Loop engineering's claim is that verification, not model capability, bounds
    autonomy. That claim is only actionable if verifier strength is measurable,
    so this reads the ledger rather than asking anyone's opinion.

    A task class with no executable gate at all, such as research, cannot exceed
    supervised autonomy. That is not a defect in the measure: work whose
    correctness only a human can judge is exactly the work that must not loop
    unattended.
    """
    verdict = VerifierVerdict()
    required = set(CONTRACTS[task_class])

    latest: dict[str, Evidence] = {}
    for item in evidence:
        prior = latest.get(item.gate)
        if prior is None or item.attempt >= prior.attempt:
            latest[item.gate] = item

    for gate in sorted(required, key=str):
        item = latest.get(str(gate))
        if item is None or not item.passed:
            verdict.missing.append(str(gate))
        elif item.command.startswith("ATTEST "):
            verdict.attested.append(str(gate))
            if gate not in INHERENTLY_ATTESTED:
                verdict.weakly_attested.append(str(gate))
        else:
            verdict.executed.append(str(gate))

    executed_set = set(verdict.executed)
    verdict.has_objective_check = bool(executed_set & {str(g) for g in STRONG_GATES})
    verdict.has_independent_check = bool(executed_set & {str(g) for g in INDEPENDENCE_GATES})

    if verdict.missing:
        verdict.max_autonomy = Autonomy.SUPERVISED
        verdict.reasons.append(f"gates with no passing evidence: {', '.join(verdict.missing)}")
        return verdict

    if not verdict.has_objective_check:
        verdict.max_autonomy = Autonomy.SUPERVISED
        if not required & STRONG_GATES:
            verdict.reasons.append(
                f"the {task_class} contract has no executable gate, so correctness rests on human "
                "judgement and this work must not loop unattended"
            )
        else:
            verdict.reasons.append(
                "no executed objective check, only attestations. An agent grading its own work "
                "is an OPEN_LOOP: errors compound silently into the next iteration"
            )
        return verdict

    if verdict.weakly_attested:
        verdict.max_autonomy = Autonomy.CHECKPOINTED
        verdict.reasons.append(
            f"{', '.join(verdict.weakly_attested)} was attested where it could have been executed, "
            "so a human should approve at phase boundaries"
        )
        return verdict

    if not verdict.has_independent_check:
        verdict.max_autonomy = Autonomy.BOUNDED
        verdict.reasons.append(
            "every gate executed, but nothing checks the work independently. "
            "Run a fixed iteration count and report rather than looping freely"
        )
        return verdict

    verdict.max_autonomy = Autonomy.UNATTENDED
    verdict.reasons.append(
        "all gates executed including an independent check, so a trigger may drive this"
    )
    return verdict


# ── design as bottleneck ─────────────────────────────────────────────────────


class Constraint(StrEnum):
    """Where the scarce resource sits for a given piece of work."""

    UNDERSTANDING = "understanding"
    DESIGN = "design"
    DECOMPOSITION = "decomposition"
    VERIFICATION = "verification"
    IMPLEMENTATION = "implementation"

    @property
    def spend_effort_on(self) -> str:
        return {
            Constraint.UNDERSTANDING: "research: document what exists before deciding anything",
            Constraint.DESIGN: "the interface and the invariants, before any code",
            Constraint.DECOMPOSITION: "splitting work into disjoint, independently verifiable units",
            Constraint.VERIFICATION: "building the check before the implementation",
            Constraint.IMPLEMENTATION: "just writing it, the design is already settled",
        }[self]


@dataclass(frozen=True)
class ConstraintVerdict:
    constraint: Constraint
    reason: str
    parallelisable: bool

    @property
    def advice(self) -> str:
        note = (
            ""
            if self.parallelisable
            else " Do not fan out yet: parallel work would multiply the wrong design."
        )
        return f"Constraint is {self.constraint}. Spend effort on {self.constraint.spend_effort_on}.{note}"


def locate_constraint(
    *,
    understood: bool,
    interfaces_settled: bool,
    units_disjoint: bool,
    verifier_strong: bool,
) -> ConstraintVerdict:
    """Find the binding constraint, in the only order that makes sense.

    Implementation is the last thing to become scarce. When agents can implement
    in minutes, effort spent there is wasted unless everything upstream is
    settled, so the checks run upstream-first and stop at the first gap.
    """
    if not understood:
        return ConstraintVerdict(
            Constraint.UNDERSTANDING,
            "the current system is not documented, so any plan rests on assumptions",
            parallelisable=False,
        )
    if not interfaces_settled:
        return ConstraintVerdict(
            Constraint.DESIGN,
            "interfaces are unsettled, and every unit built against a moving interface is rework",
            parallelisable=False,
        )
    if not verifier_strong:
        return ConstraintVerdict(
            Constraint.VERIFICATION,
            "there is no objective check, so more output means more unverified output",
            parallelisable=False,
        )
    if not units_disjoint:
        return ConstraintVerdict(
            Constraint.DECOMPOSITION,
            "work units share files, so parallel agents would overwrite each other",
            parallelisable=False,
        )
    return ConstraintVerdict(
        Constraint.IMPLEMENTATION,
        "understanding, interfaces, verification and decomposition are all settled",
        parallelisable=True,
    )


# ── pit of success ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PitVerdict:
    easy_path: str
    correct_path: str
    aligned: bool

    @property
    def advice(self) -> str:
        if self.aligned:
            return "The easy path is the correct path. This design holds without vigilance."
        return (
            f"The easy path ({self.easy_path}) differs from the correct path ({self.correct_path}). "
            "Any rule that depends on choosing the harder path will decay. "
            "Make the correct path the default, or make the easy path impossible."
        )


def audit_pit_of_success(easy_path: str, correct_path: str) -> PitVerdict:
    """Check whether a design relies on someone choosing the harder path.

    This is the test that predicts which instructions will decay. If doing the
    right thing costs more than doing the wrong thing, the instruction is a wish.
    """
    normalise = lambda text: re.sub(r"\s+", " ", text.strip().lower())  # noqa: E731
    return PitVerdict(
        easy_path=easy_path,
        correct_path=correct_path,
        aligned=normalise(easy_path) == normalise(correct_path),
    )


# ── loop anti-patterns ───────────────────────────────────────────────────────


class AntiPattern(StrEnum):
    OPEN_LOOP = "OPEN_LOOP"
    KNOWLEDGE_ROT = "KNOWLEDGE_ROT"
    COGNITIVE_SURRENDER = "COGNITIVE_SURRENDER"


ANTI_PATTERN_FIX: dict[AntiPattern, str] = {
    AntiPattern.OPEN_LOOP: "close the loop with an independent check before any iteration advances",
    AntiPattern.KNOWLEDGE_ROT: "run 'awino update' and reconcile lessons against the refreshed source",
    AntiPattern.COGNITIVE_SURRENDER: "inspect what the loop is doing, not only whether it is running",
}


@dataclass(frozen=True)
class AntiPatternHit:
    pattern: AntiPattern
    evidence: str

    @property
    def fix(self) -> str:
        return ANTI_PATTERN_FIX[self.pattern]


def detect_anti_patterns(
    *,
    verifier: VerifierVerdict,
    knowledge_age_days: int | None,
    stale_after_days: int,
    human_inspected_recently: bool,
) -> list[AntiPatternHit]:
    """Find the three ways a loop rots.

    Each is invisible from inside the loop, which is why they need a check outside
    it: an open loop reports success, stale knowledge reports confidence, and
    cognitive surrender reports nothing at all.
    """
    hits: list[AntiPatternHit] = []
    if verifier.open_loop:
        hits.append(
            AntiPatternHit(
                AntiPattern.OPEN_LOOP,
                "no executed objective check, so the agent is grading its own work",
            )
        )
    if knowledge_age_days is not None and knowledge_age_days >= stale_after_days:
        hits.append(
            AntiPatternHit(
                AntiPattern.KNOWLEDGE_ROT,
                f"codified knowledge is {knowledge_age_days}d old against a {stale_after_days}d policy",
            )
        )
    if not human_inspected_recently and verifier.max_autonomy >= Autonomy.BOUNDED:
        hits.append(
            AntiPatternHit(
                AntiPattern.COGNITIVE_SURRENDER,
                "work is running at bounded autonomy or higher with no recorded human inspection",
            )
        )
    return hits


# ── the composed plan ────────────────────────────────────────────────────────


@dataclass
class Plan:
    """What the models jointly recommend, with the reasoning attached."""

    request: str
    rung: RungVerdict
    constraint: ConstraintVerdict
    verifier: VerifierVerdict
    anti_patterns: list[AntiPatternHit]
    pre_execution: bool = False

    @property
    def next_action(self) -> str:
        if self.rung.misaligned:
            return f"Reframe first. {self.rung.advice}"
        # OPEN_LOOP is expected before implementation: there is no output to
        # verify yet. Treating it as the first action made planning impossible in
        # every new project ("fix verification before you understand the work").
        actionable = [
            hit
            for hit in self.anti_patterns
            if not (self.pre_execution and hit.pattern is AntiPattern.OPEN_LOOP)
        ]
        if actionable:
            first = actionable[0]
            return f"Fix {first.pattern} before proceeding: {first.fix}"
        if not self.constraint.parallelisable:
            return self.constraint.advice
        return (
            f"Proceed at {self.verifier.max_autonomy.name.lower()} autonomy "
            f"({self.verifier.max_autonomy.meaning})."
        )

    @property
    def may_fan_out(self) -> bool:
        """Whether spawning parallel agents is justified yet.

        Fanning out multiplies whatever the design already is. With an unsettled
        design, thirty agents produce thirty variants of the same mistake.
        """
        return (
            self.constraint.parallelisable
            and self.verifier.max_autonomy >= Autonomy.BOUNDED
            and not self.anti_patterns
        )


def build_plan(
    request: str,
    *,
    task_class: TaskClass,
    evidence: list[Evidence],
    understood: bool,
    interfaces_settled: bool,
    units_disjoint: bool,
    knowledge_age_days: int | None = None,
    stale_after_days: int = 14,
    human_inspected_recently: bool = True,
    pre_execution: bool = False,
) -> Plan:
    rung = detect_rung(request)
    verifier = assess_verifier(task_class, evidence)
    constraint = locate_constraint(
        understood=understood,
        interfaces_settled=interfaces_settled,
        units_disjoint=units_disjoint,
        verifier_strong=verifier.has_objective_check,
    )
    anti_patterns = detect_anti_patterns(
        verifier=verifier,
        knowledge_age_days=knowledge_age_days,
        stale_after_days=stale_after_days,
        human_inspected_recently=human_inspected_recently,
    )
    return Plan(
        request=request,
        rung=rung,
        constraint=constraint,
        verifier=verifier,
        anti_patterns=anti_patterns,
        pre_execution=pre_execution,
    )
