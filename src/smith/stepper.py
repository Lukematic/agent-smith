"""`awino step`: the node actions. Each node does exactly one thing using an
existing command's internals, returns the observation string, and the edge
table decides what is next. Nothing here chooses; it observes.

Kept apart from machine.py so the table stays pure and testable without the
ledger, and apart from cli/ so the actions can be driven by tests directly.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from smith import dispatch, health, machine, playbook, provision, recall
from smith.enforce import CONTRACTS, MAX_ATTEMPTS, Gate, Ledger, TaskClass
from smith.machine import Machine, Node
from smith.paths import SmithPaths
from smith.skill_catalog import SkillCatalog


@dataclass
class StepContext:
    state_root: Path
    project: Path
    home: Path
    paths: SmithPaths
    ledger: Ledger
    catalog: SkillCatalog
    confirmed_budget: bool = False
    answer: str | None = None
    verify: str | None = None
    scope: list[str] | None = None
    lines: list[str] | None = None

    def say(self, text: str) -> None:
        if self.lines is None:
            self.lines = []
        self.lines.append(text)


Action = Callable[[Machine, StepContext], str]


def _locate(m: Machine, ctx: StepContext) -> str:
    """Where are we: health, provisioning gaps, relevant lessons, the mission's
    state, and the stance the human's words call for. All reads."""
    from smith import heilmeier, stance

    results = health.run_all(ctx.paths, fast=True)
    failing = [r for r in results if r.blocking]
    steps = provision.plan(ctx.project, ctx.state_root)
    auto = [s for s in steps if not s.needs_question]
    for hit in recall.recall_lessons(ctx.home / "memory" / "lessons.md", m.request or "")[:2]:
        ctx.say(f"RECALL  {hit[:120]}")

    detected = stance.detect(m.request or "")
    current = stance.load_default(ctx.project)
    if detected is not None and detected.name != current:
        m.stance = detected.name
        ctx.say(f"STANCE  -> {detected.name} ({detected.trigger_description})")
    else:
        m.stance = current

    cat = heilmeier.load(ctx.state_root)
    answered = sum(1 for q in heilmeier.QUESTIONS if cat.answers.get(q.key, "").strip())
    objective = cat.answers.get("objective", "").strip()
    if objective:
        ctx.say(
            f"MISSION  {objective[:100]}  ({answered}/8 answered, {len(cat.exam_commands())} exam(s))"
        )
    else:
        ctx.say("MISSION  unanswered - the plan has nothing to serve; awino mission --heilmeier")

    if failing:
        ctx.say(f"HEALTH  {len(failing)} failing: {', '.join(r.name for r in failing)}")
    if auto:
        ctx.say(f"MISSING  {', '.join(s.kind.value for s in auto)}")
        return "missing"
    return "healthy"


def _provision(_m: Machine, ctx: StepContext) -> str:
    steps = [s for s in provision.plan(ctx.project, ctx.state_root) if not s.needs_question]
    actions = provision.apply_steps(ctx.project, steps, ask=lambda _q: False)
    for a in actions:
        ctx.say(f"{a.outcome:<9} {a.kind.value}  {a.detail}")
    return "provisioned" if actions else "declined"


def _route(m: Machine, ctx: StepContext) -> str:
    decision = dispatch.decide(m.request, ctx.catalog)
    if decision.confidence == "high" and decision.skill is not None:
        m.skill = decision.skill.name
        ctx.say(f"FLOOR  {m.skill}  ({decision.rationale})")
        # The trip is the intent: persist it so session-start and RESUME carry
        # it across processes and compaction, exactly as the elevator did.
        playbook.save_intent(ctx.state_root, m.request, m.skill, "awino best")
        return "high"
    ctx.say(f"QUESTION  {decision.question or decision.rationale}")
    return decision.confidence  # "ambiguous" | "none"


def _question(m: Machine, ctx: StepContext) -> str:
    if ctx.answer:
        m.request = ctx.answer
        return "answered"
    ctx.say('WAITING  answer with: awino best --answer "<clearer request>"')
    return "waiting"


def _ladder(m: Machine, ctx: StepContext) -> str:
    from smith import heilmeier, ladder, recall

    choice = ladder.choose(m.request, m.skill or "", ctx.verify, ctx.scope or [])
    m.loop, m.why = choice.loop, choice.why
    ctx.say(f"LOOP  {choice.loop}  ({choice.why}; stance={m.stance or 'advisor'})")

    # Does this work serve the mission? Token overlap with the exams, advisory:
    # a request no exam would notice finishing is exactly the "37 untied seeds"
    # insight, surfaced at the moment it can still change the plan.
    cat = heilmeier.load(ctx.state_root)
    exams = cat.answers.get("exams", "")
    if exams:
        words = set(recall._tokens(m.request))
        if not words & recall._tokens(exams):
            ctx.say(
                "MISSION  no exam mentions this work; finishing it would not move any mission exam"
            )
    return choice.loop


def _answer(m: Machine, ctx: StepContext) -> str:
    ctx.say(f"ANSWER  load skill {m.skill} and reply directly; no run needed")
    return "done"


def _budget(m: Machine, ctx: StepContext) -> str:
    floors = 1 if m.loop == "floor" else MAX_ATTEMPTS
    per = 2 if m.loop == "graph" else 1
    ctx.say(f"BUDGET  loop={m.loop}  floors<={floors}  subprocesses<={floors * per}")
    if not ctx.confirmed_budget:
        ctx.say("WAITING  approve with: awino best --confirm-budget")
        return "waiting"
    return "confirmed"


def _open(m: Machine, ctx: StepContext) -> str:
    task_class = TaskClass(playbook._CLASS_FOR_SKILL.get(m.skill or "", "code-change"))
    if Gate.PLANNED in CONTRACTS[task_class]:
        ctx.say(
            f'PLAN_REQUIRED  {task_class} needs an approved plan: awino gate open {task_class} "..." --plan <file> --loop {m.loop}'
        )
        return "plan-required"
    run = ctx.ledger.open(task_class, m.request, file_scope=ctx.scope or [], loop=m.loop)
    m.run_id = run.run_id
    ctx.say(f"RUN {run.run_id}  class={task_class}  loop={m.loop}")
    return "opened"


def _work(m: Machine, ctx: StepContext) -> str:
    verify = ctx.verify
    if not verify:
        found = provision.discover_verification(ctx.project)
        if found is None:
            ctx.say('REFUSED  no verification command found; awino best --verify "<check>"')
            return "waiting"
        verify, source = found
        ctx.say(f"VERIFY  {verify} (from {source})")
    state = dispatch.open_floor(
        ctx.ledger,
        m.run_id or "",
        m.request,
        ctx.catalog,
        ctx.home,
        verify,
        file_scope=ctx.scope or ["(unscoped)"],
        max_floors=1 if m.loop == "floor" else MAX_ATTEMPTS,
    )
    ctx.say(f"FLOOR_OPEN  floor={state.floor}/{state.max_floors}  skill={state.skill}")
    ctx.say(f"PROMPT  {state.prompt_path}")
    return "floor-open"


def _execute(_m: Machine, ctx: StepContext) -> str:
    """The harness does the work here. The first visit prints the prompt and
    waits; the human (or the agent playing worker) says `--answer done` when
    the prompt has been executed, and the machine moves on to verify it."""
    if ctx.answer == "done":
        return "executed"
    ctx.say(
        "EXECUTE  run the prompt in this or any agent environment, then: awino best --answer done"
    )
    return "waiting"


def _verify(m: Machine, ctx: StepContext) -> str:
    result = dispatch.close_floor(ctx.ledger, m.run_id or "", ctx.project)
    ctx.say(f"{result.outcome.value.upper()}  {result.detail[:120]}")
    if result.outcome is dispatch.DispatchOutcome.COMPLETE:
        return "verified-graph" if m.loop == "graph" else "verified"
    if result.outcome is dispatch.DispatchOutcome.REVISE:
        if result.next_state:
            ctx.say(f"PROMPT  {result.next_state.prompt_path}")
        return "revise"
    return "max-iterations"


def _review(m: Machine, ctx: StepContext) -> str:
    """The graph's real value with no login: a reviewer floor is a floor whose
    role is mechanically read-only, run by whatever harness is present.

    First tick opens it and waits for the harness to write the verdict; the
    next tick closes it (independent re-check: SHIP/REVISE/BLOCKED)."""
    pending = ctx.ledger.latest_artifact(m.run_id or "", "dispatch-pending")
    if pending is not None and pending.payload.get("role") == "reviewer":
        result = dispatch.close_floor(ctx.ledger, m.run_id or "", ctx.project)
        ctx.say(f"{result.outcome.value.upper()}  {result.detail[:160]}")
        if result.outcome is dispatch.DispatchOutcome.COMPLETE:
            return "ship"
        if result.outcome is dispatch.DispatchOutcome.REVISE:
            if result.next_state:
                ctx.say(f"PROMPT  {result.next_state.prompt_path}")
            return "revise"
        return "blocked"

    verify = ctx.verify
    if not verify:
        found = provision.discover_verification(ctx.project)
        if found is None:
            ctx.say(
                'REFUSED  no verification command for the worker floor a REVISE would open; awino best --verify "<check>"'
            )
            return "waiting"
        verify = found[0]
    state = dispatch.open_floor(
        ctx.ledger,
        m.run_id or "",
        m.request,
        ctx.catalog,
        ctx.home,
        verify,
        file_scope=ctx.scope or [],
        max_floors=MAX_ATTEMPTS,
        role="reviewer",
        project=ctx.project,
    )
    ctx.say(f"REVIEW_OPEN  floor={state.floor}/{state.max_floors}")
    ctx.say(f"PROMPT  {state.prompt_path}")
    ctx.say(f"VERDICT_FILE  {state.verdict_path}")
    ctx.say(
        "EXECUTE  the reviewer prompt in this or any agent environment, then: awino best --answer done"
    )
    return "waiting"


def _gates(m: Machine, ctx: StepContext) -> str:
    """Record every gate that has a mechanical command, then wait only on the
    ones that need a human (plan approval, lesson text)."""
    run = ctx.ledger.load(m.run_id or "")
    commands = _gate_commands(ctx)
    for gate in run.required:
        have = {e.gate for e in ctx.ledger.evidence(run.run_id) if e.passed}
        if gate in have:
            continue
        if gate == "tests_not_weakened":
            try:
                from smith.enforce import detect_test_weakening

                diff = subprocess.run(
                    ["git", "diff", "HEAD", "--"],
                    cwd=ctx.project,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                ).stdout
                findings = detect_test_weakening(diff)
                if not findings:
                    ctx.ledger.attest(
                        run.run_id, Gate.TESTS_NOT_WEAKENED, "no deleted assertions or added skips"
                    )
                    ctx.say("PASS  tests_not_weakened  (diff analysed)")
                else:
                    ctx.say(f"FAIL  tests_not_weakened  {'; '.join(findings)}")
            except Exception as exc:
                ctx.say(f"GATE_ERROR  tests_not_weakened  {exc}")
            continue

        if gate not in commands:
            continue
        try:
            ev = ctx.ledger.record(run.run_id, Gate(gate), commands[gate], cwd=ctx.project)
            ctx.say(f"{'PASS' if ev.passed else 'FAIL'}  {gate}  {commands[gate][:60]}")
        except Exception as exc:  # three-strikes or plan invalid: report, do not hide
            ctx.say(f"GATE_ERROR  {gate}  {exc}")
    have = {e.gate for e in ctx.ledger.evidence(run.run_id) if e.passed}
    failed = {e.gate for e in ctx.ledger.evidence(run.run_id) if not e.passed} - have
    if failed:
        ctx.say(f"GATES  failing: {', '.join(sorted(failed))}")
        return "fail" if m.floor < MAX_ATTEMPTS else "exhausted"
    missing = [g for g in run.required if g not in have]
    if missing:
        ctx.say(
            f"GATES  need a human: {', '.join(missing)}  (awino gate record <gate> --cmd ..., then: awino best)"
        )
        return "waiting"
    return "hold"


def _gate_commands(ctx: StepContext) -> dict[str, str]:
    """The project's own commands for the gates that have one."""
    out: dict[str, str] = {}
    found = provision.discover_verification(ctx.project)
    if found:
        out["tested"] = found[0]
        out["researched"] = found[0]
    if (ctx.project / "pyproject.toml").is_file():
        out["linted"] = f'"{sys.executable}" -m ruff check . || ruff check .'
    return out


def _close(m: Machine, ctx: StepContext) -> str:
    """Close for real: the ledger adjudicates, marks complete, and the task-close
    order fires - walkthrough, grill questions, mission refresh, intent cleared.
    Printing "run gate close" was a suggestion; a machine node is an action."""
    from smith.enforce import adjudicate

    run = ctx.ledger.load(m.run_id or "")
    verdict = adjudicate(run, ctx.ledger.evidence(run.run_id))
    if not verdict.can_close:
        ctx.say(f"CLOSE  refused: {verdict.blocked_reason}")
        return "waiting"
    if run.issue_id:
        ctx.say(f"CLOSE  linked seed {run.issue_id}: run 'awino work-close {run.issue_id}' first")
        return "waiting"
    ctx.ledger.mark_complete(run.run_id)
    ctx.say(f"COMPLETE  {len(verdict.satisfied)} gate(s) satisfied  run={run.run_id}")
    for line in playbook.run_event(
        "task-close", ctx.state_root, ctx.project, ledger=ctx.ledger, open_seeds=[]
    ):
        ctx.say(line)
    return "closed"


def _stop(_m: Machine, ctx: StepContext) -> str:
    ctx.say("STOP  human decision: awino best --answer continue | close | drop")
    if ctx.answer in ("continue", "close", "drop"):
        return ctx.answer
    return "waiting"


ACTIONS: dict[Node, Action] = {
    Node.IDLE: lambda _m, _c: "start",
    Node.LOCATE: _locate,
    Node.PROVISION: _provision,
    Node.ROUTE: _route,
    Node.QUESTION: _question,
    Node.LADDER: _ladder,
    Node.ANSWER: _answer,
    Node.BUDGET: _budget,
    Node.OPEN: _open,
    Node.WORK: _work,
    Node.EXECUTE: _execute,
    Node.VERIFY: _verify,
    Node.REVIEW: _review,
    Node.GATES: _gates,
    Node.CLOSE: _close,
    Node.STOP: _stop,
    Node.DONE: lambda _m, _c: "waiting",
}


def step(ctx: StepContext, request: str | None = None) -> tuple[Machine, list[str]]:
    """One tick: read node, act once, observe, advance (or wait), persist."""
    m = machine.load(ctx.state_root)
    ctx.lines = []
    if request:
        m = Machine(node=Node.LOCATE, request=request)
    before = m.node
    obs = ACTIONS[m.node](m, ctx)
    if obs == "waiting" or obs == "done":
        machine.save(ctx.state_root, m)
        ctx.say(f"NODE  {before.value}  ({obs})")
        return m, ctx.lines
    m = machine.advance(m, obs)
    machine.save(ctx.state_root, m)
    ctx.say(f"NODE  {before.value} -> {m.node.value}  ({obs})")
    return m, ctx.lines


# Nodes where the machine must stop and let a human speak. Everything else is
# an observation the machine can take on its own.
HUMAN_NODES: frozenset[Node] = frozenset({Node.BUDGET, Node.QUESTION, Node.STOP, Node.EXECUTE})


def run(
    ctx: StepContext, request: str | None = None, *, max_ticks: int = 40
) -> tuple[Machine, list[str]]:
    """Walk the machine until it needs a human or is done.

    `step` is one square; `run` is the whole sitting. It stops at BUDGET
    (approve cost), QUESTION (clarify), STOP (decide), EXECUTE (the harness
    does the work) and DONE - never anywhere else, so the human is asked only
    when the machine genuinely cannot proceed. The tick cap exists so a
    table edge that ping-pongs cannot hang: it prints TICK_CAP and returns.
    """
    all_lines: list[str] = []
    m = machine.load(ctx.state_root)
    if request is None and m.node is Node.IDLE:
        return m, ['no open trip - say what you want: awino best "<request>"']
    for _ in range(max_ticks):
        m, lines = step(ctx, request)
        request = None
        all_lines += lines
        last = lines[-1] if lines else ""
        if last.endswith("(waiting)") or last.endswith("(done)") or m.node is Node.DONE:
            return m, all_lines
        if m.node in HUMAN_NODES:
            # Enter the human node so its action prints what it needs (the
            # cost, the question, the prompt path), then stop there.
            m, lines = step(ctx)
            all_lines += lines
            return m, all_lines
    all_lines.append(f"TICK_CAP  {max_ticks} ticks without a human decision; node={m.node.value}")
    return m, all_lines
