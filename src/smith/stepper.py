"""`awino step`: the node actions. Each node does exactly one thing using an
existing command's internals, returns the observation string, and the edge
table decides what is next. Nothing here chooses; it observes.

Kept apart from machine.py so the table stays pure and testable without the
ledger, and apart from cli/ so the actions can be driven by tests directly.
"""

from __future__ import annotations

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
    results = health.run_all(ctx.paths, fast=True)
    failing = [r for r in results if r.blocking]
    steps = provision.plan(ctx.project, ctx.state_root)
    auto = [s for s in steps if not s.needs_question]
    for hit in recall.recall_lessons(ctx.home / "memory" / "lessons.md", m.request or "")[:2]:
        ctx.say(f"RECALL  {hit[:120]}")
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
        return "high"
    ctx.say(f"QUESTION  {decision.question or decision.rationale}")
    return decision.confidence  # "ambiguous" | "none"


def _question(m: Machine, ctx: StepContext) -> str:
    if ctx.answer:
        m.request = ctx.answer
        return "answered"
    ctx.say('WAITING  answer with: awino step --answer "<clearer request>"')
    return "waiting"


def _ladder(m: Machine, ctx: StepContext) -> str:
    from smith import ladder

    choice = ladder.choose(m.request, m.skill or "", ctx.verify, ctx.scope or [])
    m.loop, m.why = choice.loop, choice.why
    ctx.say(f"LOOP  {choice.loop}  ({choice.why})")
    return choice.loop


def _answer(m: Machine, ctx: StepContext) -> str:
    ctx.say(f"ANSWER  load skill {m.skill} and reply directly; no run needed")
    return "done"


def _budget(m: Machine, ctx: StepContext) -> str:
    floors = 1 if m.loop == "floor" else MAX_ATTEMPTS
    per = 2 if m.loop == "graph" else 1
    ctx.say(f"BUDGET  loop={m.loop}  floors<={floors}  subprocesses<={floors * per}")
    if not ctx.confirmed_budget:
        ctx.say("WAITING  approve with: awino step --confirm-budget")
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
            ctx.say('REFUSED  no verification command found; awino step --verify "<check>"')
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
    ctx.say("EXECUTE  run the prompt in this or any agent environment, then: awino step")
    return "executed"


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

    verify = ctx.verify or 'echo "reviewer verdict pending"'
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
    ctx.say("EXECUTE  the reviewer prompt in this or any agent environment, then: awino step")
    return "waiting"


def _gates(m: Machine, ctx: StepContext) -> str:
    """Record every gate that has a mechanical command, then wait only on the
    ones that need a human (plan approval, lesson text)."""
    run = ctx.ledger.load(m.run_id or "")
    commands = _gate_commands(ctx)
    for gate in run.required:
        have = {e.gate for e in ctx.ledger.evidence(run.run_id) if e.passed}
        if gate in have or gate not in commands:
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
            f"GATES  need a human: {', '.join(missing)}  (awino gate record <gate> --cmd ..., then: awino step)"
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
        out["linted"] = "ruff check ."
    out["tests_not_weakened"] = "git diff --exit-code -- tests"
    return out


def _close(m: Machine, ctx: StepContext) -> str:
    ctx.say(f"CLOSE  run: awino gate close --run {m.run_id}")
    return "closed"


def _stop(_m: Machine, ctx: StepContext) -> str:
    ctx.say("STOP  human decision: awino step --answer continue | drop")
    if ctx.answer in ("continue", "drop"):
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
