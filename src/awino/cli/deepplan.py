"""owns: deepplan start, deepplan stage, deepplan record, deepplan ask, deepplan answer, deepplan defer, deepplan learn, deepplan grill-done, deepplan confirm, deepplan compile, deepplan go, deepplan done, deepplan seeds, deepplan list, deepplan use

Deep Plan: define the problem, get grilled and challenged, break the work into
prioritized steps, then hand the harness a plan it holds the work to. ``awino deepplan`` shows where the active session
stands and what to do next; the grill is ``ask`` / ``answer`` / ``learn`` /
``defer``, one question at a time; ``compile`` writes the plan in the RPI
format and ``go`` opens a gated run bound to it, approved by the human;
``done <n>`` runs a phase's own success command as gate evidence and ticks it
off only when it passes; ``seeds`` puts the steps in the Seeds tracker with
their priorities and dependencies. The engine and the stage contracts live in
``awino.deepplan``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import typer

from awino import deepplan, seeds
from awino.cli import _echo, _ledger, _resolve_run, _workspace
from awino.enforce import Gate, LedgerError, PlanDecision, TaskClass

deepplan_app = typer.Typer(
    no_args_is_help=False,
    help="Deep Plan: define the problem, get grilled, prioritize the steps, then execute a plan the harness checks.",
)


def _active() -> deepplan.Session:
    ws = _workspace()
    session = deepplan.active(ws.state_root, ws.project.root)
    if session is None:
        _echo('NO_SESSION  start one with: awino deepplan start "<short title>" [--brief <file>]')
        raise typer.Exit(1)
    return session


def _refuse(exc: Exception, code: int = 2) -> None:
    _echo(f"REFUSED  {exc}")
    raise typer.Exit(code)


def _show_stage(session: deepplan.Session, stage: deepplan.Stage) -> None:
    n = deepplan.STAGES.index(stage) + 1
    _echo(f"STAGE  {n}/{len(deepplan.STAGES)} {stage.name} - {stage.title}: {stage.purpose}")
    if stage.name != "grill":
        _echo(f"WRITE  {session.file_for(stage)}")
    _echo("")
    _echo(stage.prompt)
    _echo("")
    if stage.name != "grill":
        _echo(f"RECORD  awino deepplan record {stage.name}")


def _status(session: deepplan.Session) -> None:
    _echo(f"DEEP_PLAN  {session.title}  ({session.id})")
    _echo(f"FOLDER  {session.path}")
    for stage in deepplan.STAGES:
        if session.recorded(stage.name):
            mark = "done"
            if stage.checkpoint:
                mark = "confirmed" if session.confirmed(stage.name) else "WAITING for the human"
        else:
            mark = "-"
        _echo(f"  {stage.name:<10} {mark}")
    asked = [q for q in session.questions if q.get("by") != "code"]
    learned = [q for q in session.questions if q.get("by") == "code"]
    challenges = [q for q in session.questions if q.get("challenge")]
    if session.questions:
        _echo(
            f"GRILL  {len(asked)} asked ({len(challenges)} challenge), "
            f"{len(learned)} answered from the code"
        )
    record = session.data.get("seeds") or {}
    if record.get("epic"):
        _echo(f"SEEDS  epic {record['epic']}, {len(record.get('steps', {}))} step seed(s)")
    pending = session.open_question()
    if pending:
        _echo(f"OPEN  {pending['id']}: {pending['text']}")
        _echo(f"      recommended: {pending['recommend']}")
        _echo(
            'WAITING  for the human; record with: awino deepplan answer "<their words>" | --accept'
        )
        return
    nxt = session.next_stage()
    if nxt is None and session.data.get("approved"):
        _execution_status(session)
        return
    if nxt is None:
        plan = session.data.get("plan_path")
        if plan:
            _echo(f"PLAN  {plan}")
            _echo(
                'NEXT  after the human approves: awino deepplan go --by "<name>" --note "<their words>"'
            )
        else:
            _echo("NEXT  awino deepplan compile")
        return
    if session.recorded(nxt.name) and nxt.checkpoint:
        _echo(
            f"CHECKPOINT  show {nxt.filename} to the human, then: "
            f'awino deepplan confirm {nxt.name} --note "<their words>"'
        )
        return
    _echo("")
    _show_stage(session, nxt)


def _execution_status(session: deepplan.Session) -> None:
    done = deepplan.done_phases(session)
    plan = deepplan.phases(session)
    p0 = [ph for ph in plan if ph.priority == 0]
    _echo(
        f"EXECUTING  run {session.data.get('run_id', '?')}: {len(done)}/{len(plan)} phases "
        f"verified, P0 {sum(1 for ph in p0 if ph.number in done)}/{len(p0)}"
    )
    for ph in plan:
        seed = deepplan.seed_for(session, ph.number)
        tail = f"  seed {seed}" if seed else ""
        mark = "x" if ph.number in done else " "
        _echo(f"  [{mark}] Phase {ph.number} {ph.label}: {ph.title}  ({ph.command}){tail}")
    left = deepplan.remaining(session)
    if left and left[0].priority == 0:
        _echo(f"NEXT  build phase {left[0].number}, then: awino deepplan done {left[0].number}")
        return
    _echo("DONE_CRITERIA  every P0 phase is verified")
    if left:
        _echo(
            f"OPTIONAL  phase(s) {', '.join(str(ph.number) for ph in left)} (P1/P2): "
            "build and `awino deepplan done <n>` now, or leave them as seeds"
        )
    _echo("NEXT  awino gate record linted --cmd <lint>; awino gate check --diff-base HEAD;")
    _echo("      awino gate close")


@deepplan_app.callback(invoke_without_command=True)
def deepplan_default(ctx: typer.Context) -> None:
    """Bare `awino deepplan` shows the active session and what to do next."""
    if ctx.invoked_subcommand is None:
        _status(_active())


@deepplan_app.command("start")
def deepplan_start(
    title: str = typer.Argument(..., help="A short title, e.g. 'per-user notes'."),
    brief: str = typer.Option(None, "--brief", help="A file with everything known so far."),
) -> None:
    """Start a Deep Plan session in thoughts/plans/."""
    text = None
    if brief:
        source = Path(brief)
        if not source.is_file():
            _refuse(deepplan.PlanError(f"cannot read brief {source}"))
        text = source.read_text(encoding="utf-8", errors="replace")
    ws = _workspace()
    session = deepplan.start(ws.state_root, ws.project.root, title, text)
    _echo(f"STARTED  {session.id}")
    _status(session)


@deepplan_app.command("stage")
def deepplan_stage(name: str = typer.Argument(None, help="Stage name; omit for the next one.")):
    """Print a stage's prompt: the structure the thinking goes into."""
    session = _active()
    try:
        stage = deepplan.by_name(name) if name else session.next_stage()
    except ValueError as exc:
        _refuse(exc)
    if stage is None:
        _echo("COMPLETE  every stage is recorded; awino deepplan compile")
        return
    _show_stage(session, stage)


@deepplan_app.command("record")
def deepplan_record(
    name: str = typer.Argument(..., help="Stage name."),
    file: str = typer.Option(None, "--file", help="Defaults to the stage's file in the session."),
) -> None:
    """Check a stage's Markdown and store it in the session."""
    session = _active()
    try:
        stage = deepplan.by_name(name)
    except ValueError as exc:
        _refuse(exc)
    source = Path(file) if file else session.file_for(stage)
    if stage.name != "grill" and not source.is_file():
        _refuse(deepplan.PlanError(f"nothing to record: write {source} first"))
    text = source.read_text(encoding="utf-8") if source.is_file() else ""
    problems = deepplan.record(session, stage.name, text)
    if problems:
        _echo(f"NOT_YET  {stage.name}")
        for problem in problems:
            _echo(f"  - {problem}")
        raise typer.Exit(1)
    _echo(f"RECORDED  {stage.name} -> {session.file_for(stage)}")
    if stage.checkpoint:
        _echo(
            f"CHECKPOINT  stop here. Show {stage.filename} to the human and ask; then "
            f'awino deepplan confirm {stage.name} --note "<their words>"'
        )
        return
    nxt = session.next_stage()
    _echo(f"NEXT  awino deepplan stage {nxt.name}" if nxt else "NEXT  awino deepplan compile")


@deepplan_app.command("ask")
def deepplan_ask(
    question: str = typer.Argument(..., help="One question for the human."),
    recommend: str = typer.Option(..., "--recommend", help="Your recommended answer."),
    why: str = typer.Option("", "--why", help="What the answer changes in the plan."),
    not_blocking: bool = typer.Option(
        False, "--not-blocking", help="Planning may finish without an answer."
    ),
    challenge: bool = typer.Option(
        False,
        "--challenge",
        help="Pushback: a vague answer, a solution posing as the problem, scope creep, "
        "or the riskiest assumption.",
    ),
) -> None:
    """Ask the human one question, with your recommendation. One open at a time."""
    session = _active()
    try:
        q = deepplan.ask(
            session, question, recommend, why, blocking=not not_blocking, challenge=challenge
        )
    except deepplan.PlanError as exc:
        _refuse(exc)
    _echo(f"{'CHALLENGED' if challenge else 'ASKED'}  {q['id']}: {q['text']}")
    _echo(f"  recommended: {q['recommend']}")
    if q["why"]:
        _echo(f"  why it matters: {q['why']}")
    _echo("STOP  ask the human this question in plain words and wait for the answer")


@deepplan_app.command("answer")
def deepplan_answer(
    text: str = typer.Argument("", help="The human's answer, in their words."),
    accept: bool = typer.Option(False, "--accept", help="They took your recommendation."),
) -> None:
    """Record the human's answer to the open question."""
    session = _active()
    try:
        q = deepplan.answer(session, text, accept=accept)
    except deepplan.PlanError as exc:
        _refuse(exc)
    _echo(f"ANSWERED  {q['id']}: {q['answer']}")
    _echo("NEXT  ask the next question that changes the plan, or: awino deepplan grill-done")


@deepplan_app.command("defer")
def deepplan_defer(reason: str = typer.Option(..., "--reason", help="Why it can wait.")) -> None:
    """Park the open question with a reason the human gave."""
    session = _active()
    try:
        q = deepplan.defer(session, reason)
    except deepplan.PlanError as exc:
        _refuse(exc)
    _echo(f"DEFERRED  {q['id']}: {reason}")


@deepplan_app.command("learn")
def deepplan_learn(
    fact: str = typer.Argument(..., help="What the code shows is true."),
    evidence: str = typer.Option(..., "--evidence", help="path/to/file:line in this project."),
) -> None:
    """Record something the code answered, so the human is not asked."""
    session = _active()
    try:
        q = deepplan.learn(session, fact, evidence)
    except deepplan.PlanError as exc:
        _refuse(exc)
    _echo(f"LEARNED  {q['id']}: {q['answer']}  ({q['evidence']})")


@deepplan_app.command("grill-done")
def deepplan_grill_done(
    enough: str = typer.Option("", "--enough", help="Why fewer than three questions is right."),
) -> None:
    """Finish the grill: no blocking question may be open."""
    session = _active()
    problems = deepplan.finish_grill(session, enough)
    if problems:
        _echo("NOT_YET  grill")
        for problem in problems:
            _echo(f"  - {problem}")
        raise typer.Exit(1)
    _echo("RECORDED  grill")
    _echo(deepplan.grill_record(session))
    _echo("NEXT  awino deepplan stage directions")


@deepplan_app.command("confirm")
def deepplan_confirm(
    name: str = typer.Argument(..., help="The checkpoint stage."),
    note: str = typer.Option(..., "--note", help="What the human said, in their words."),
) -> None:
    """Record the human's answer at a checkpoint. Only after they actually answered."""
    session = _active()
    try:
        deepplan.confirm(session, name, note)
    except (deepplan.PlanError, ValueError) as exc:
        _refuse(exc)
    _echo(f"CONFIRMED  {name}: {note.strip()[:120]}")
    nxt = session.next_stage()
    _echo(f"NEXT  awino deepplan stage {nxt.name}" if nxt else "NEXT  awino deepplan compile")


@deepplan_app.command("compile")
def deepplan_compile() -> None:
    """Write plan.md in the format the harness executes and checks."""
    session = _active()
    try:
        plan = deepplan.compile_plan(session)
    except deepplan.PlanError as exc:
        _refuse(exc)
    _echo(f"PLAN  {plan}")
    _echo(f"SCOPE  {', '.join(deepplan.scope(session)) or '(none)'}")
    _echo("STOP  show the plan to the human. Execute only after they approve it:")
    _echo('  awino deepplan go --by "<their name>" --note "<their words>"')


@deepplan_app.command("go")
def deepplan_go(
    by: str = typer.Option(..., "--by", help="The human approving the plan."),
    note: str = typer.Option(..., "--note", help="Their approval, in their words."),
    task_class: TaskClass = typer.Option(
        TaskClass.CODE_CHANGE, "--class", help="code-change, refactor, bugfix, authoring..."
    ),
    issue: str = typer.Option(None, "--issue", help="Issue this work serves, if required."),
) -> None:
    """Open a gated run bound to the approved plan's exact bytes and scope."""
    from awino.cli.gate import _plan_decision, gate_open

    session = _active()
    if not note.strip() or not by.strip():
        _refuse(deepplan.PlanError("approval needs the human's name and words"))
    try:
        plan = deepplan.approvable_plan(session)
    except deepplan.PlanError as exc:
        _refuse(exc)
    ledger = _ledger()
    prev_run = session.data.get("run_id")
    old = None
    if prev_run:
        try:
            old = ledger.load(prev_run)
        except LedgerError:
            old = None
    same_plan = (
        old is not None
        and old.terminal_state is None
        and bool(old.plan_decisions)
        and old.plan_decisions[-1].plan_sha256 == hashlib.sha256(plan.read_bytes()).hexdigest()
        and old.plan_decisions[-1].approved_scope == deepplan.scope(session)
    )
    if same_plan:
        _refuse(
            deepplan.PlanError(
                f"run {prev_run} is already executing this exact plan: continue with "
                "awino deepplan, and awino deepplan done <n> after each phase"
            )
        )
    with deepplan.opening_run():
        gate_open(
            task_class=task_class,
            objective=deepplan.goal(session),
            scope=deepplan.scope(session),
            also=None,
            plan=plan,
            issue=issue,
            by=by,
            loop="rpi",
        )
    _plan_decision(PlanDecision.APPROVED, None, by, note)
    new_run = _resolve_run(None)
    previous = session.data.get("approved_phases") or []
    session.data["approved"] = {"by": by, "note": note}
    session.data["run_id"] = new_run
    session.data["approved_phases"] = deepplan.approved_snapshot(session)
    session.data.pop("replanning", None)
    session.save()
    if prev_run and prev_run != new_run:
        _supersede(session, ledger, old, prev_run, new_run, by, previous)
    progress = deepplan.write_progress(session)
    _echo("EXECUTE  one phase at a time, in order. Never edit plan.md: the run is bound to")
    _echo("  its exact bytes. When a phase is built, verify it with its own command:")
    _echo("    awino deepplan done 1      (runs it as gate evidence; ticks it only if it passes)")
    _echo(f"  Progress: {progress}. Anything outside the scope goes under its Follow-ups.")
    _echo("  Done is every P0 phase verified; then: awino gate check --diff-base HEAD,")
    _echo("  awino gate close. P1/P2 phases can follow, or stay tracked as seeds.")
    if not (session.data.get("seeds") or {}).get("epic"):
        _echo("SEEDS  track the steps in Seeds with: awino deepplan seeds")


def _supersede(session, ledger, old, prev_run, new_run, by, previous) -> None:
    """A re-approved plan replaces the run executing the old one. Steps that were
    verified and did not change must pass again on the new run to stay ticked."""
    from awino.cli.gate import gate_record

    if old is not None and old.terminal_state is None:
        ledger.pause(prev_run, by, f"superseded by run {new_run}: the human re-approved the plan")
        _echo(f"SUPERSEDED  run {prev_run} paused; the re-approved plan runs as {new_run}")
    before = deepplan.done_phases(session)
    was_verified = {p["title"] for p in previous if p["number"] in before}
    carried = deepplan.carry_over(session, previous)
    tracker = seeds.Seeds(_workspace().project.root)
    for ph in deepplan.phases(session):
        if ph.number not in carried:
            continue
        _echo(f"RECHECK  phase {ph.number} was verified and is unchanged: running its check again")
        try:
            gate_record(gate=Gate.TESTED, cmd=ph.command, attest=None, run_id=new_run)
        except typer.Exit:
            _echo(
                f"RESET  phase {ph.number}: its check fails now; fix it, then awino deepplan done {ph.number}"
            )
            continue
        deepplan.tick(session, ph.number)
        _echo(f"CARRIED  phase {ph.number}")
        seed = deepplan.seed_for(session, ph.number)
        if seed and tracker.close(seed, f"re-verified on run {new_run}: `{ph.command}` exit 0").ok:
            _echo(f"SEED_CLOSED  {seed}")
    still = deepplan.done_phases(session)
    for ph in deepplan.phases(session):
        seed = deepplan.seed_for(session, ph.number)
        reopen = seed and ph.title in was_verified and ph.number not in still
        if reopen and tracker.reopen(seed).ok:
            _echo(f"SEED_REOPENED  {seed}  phase {ph.number} must be verified again")


@deepplan_app.command("done")
def deepplan_done(
    phase: int = typer.Argument(..., help="Phase number from plan.md."),
) -> None:
    """Verify a phase: run its success command as gate evidence, tick it if it passes."""
    from awino.cli.gate import gate_record

    session = _active()
    try:
        cmd = deepplan.next_phase_command(session, phase)
    except deepplan.PlanError as exc:
        _refuse(exc)
    step = deepplan.phases(session)[phase - 1]
    _echo(f"PHASE {phase} {step.label}  {step.title}")
    gate_record(gate=Gate.TESTED, cmd=cmd, attest=None, run_id=session.data.get("run_id"))
    deepplan.tick(session, phase)
    _echo(f"PHASE_VERIFIED  {phase}")
    seed = deepplan.seed_for(session, phase)
    if seed:
        reason = (
            f"awino deepplan done {phase}: `{cmd}` exit 0, recorded as gate evidence on run "
            f"{session.data.get('run_id', '?')}"
        )
        tracker = seeds.Seeds(_workspace().project.root)
        closed = tracker.close(seed, reason)
        _echo(f"SEED_CLOSED  {seed}" if closed.ok else f"SEED_OPEN  {seed}: {closed.detail}")
        epic = (session.data.get("seeds") or {}).get("epic")
        if epic and not deepplan.remaining(session):
            why = f"every step of the plan verified on run {session.data.get('run_id', '?')}"
            if tracker.close(epic, why).ok:
                _echo(f"EPIC_CLOSED  {epic}")
    _execution_status(session)


@deepplan_app.command("seeds")
def deepplan_seeds(
    init: bool = typer.Option(
        False, "--init", help="Create a Seeds tracker here if there is none (writes .seeds/)."
    ),
) -> None:
    """Track the plan in Seeds: an epic, one seed per step with its priority and
    dependencies, linked as a seeds plan. `done <n>` closes each step's seed."""
    session = _active()
    tracker = seeds.Seeds(_workspace().project.root)
    state, detail = tracker.state()
    if state == seeds.SeedsState.NOT_INITIALIZED and init:
        made = tracker.init(confirmed=True)
        if not made.ok:
            _refuse(deepplan.PlanError(f"could not create the tracker: {made.detail}"))
        state, detail = tracker.state()
    if state != seeds.SeedsState.READY:
        hint = (
            " (or run this again with --init)" if state == seeds.SeedsState.NOT_INITIALIZED else ""
        )
        _refuse(deepplan.PlanError(f"no usable Seeds tracker: {detail}{hint}"))
    try:
        record = deepplan.create_seeds(session, tracker)
    except deepplan.PlanError as exc:
        _refuse(exc)
    _echo(f"EPIC  {record['epic']}  Plan: {session.title}")
    for ph in deepplan.phases(session):
        deps = ", ".join(deepplan.seed_for(session, d) or "?" for d in ph.depends)
        after = f"  after {deps}" if deps else ""
        _echo(f"SEED  {deepplan.seed_for(session, ph.number)}  {ph.label}  {ph.title}{after}")
    if record.get("plan"):
        _echo(f"PLAN  {record['plan']}  (sd plan show {record['plan']})")
    _echo("NEXT  sd ready shows what can start now; awino deepplan done <n> closes a step's seed")


@deepplan_app.command("list")
def deepplan_list() -> None:
    """Every Deep Plan session in this project."""
    ws = _workspace()
    current = deepplan.active(ws.state_root, ws.project.root)
    found = deepplan.sessions(ws.project.root)
    if not found:
        _echo("NO_SESSIONS")
        return
    for s in found:
        nxt = s.next_stage()
        if nxt:
            where = nxt.name
        elif s.data.get("approved"):
            done, total = len(deepplan.done_phases(s)), len(deepplan.phases(s))
            where = "complete" if done == total else f"executing {done}/{total}"
            if done < total and not any(ph.priority == 0 for ph in deepplan.remaining(s)):
                where = f"done (P0), {total - done} optional open"
        else:
            where = "compile"
        flag = "*" if current and current.id == s.id else " "
        _echo(f"{flag} {s.id:<50} next: {where}")


@deepplan_app.command("use")
def deepplan_use(
    session_id: str = typer.Argument(..., help="Session id from `awino deepplan list`."),
):
    """Switch the active session."""
    ws = _workspace()
    try:
        session = deepplan.use(ws.state_root, ws.project.root, session_id)
    except deepplan.PlanError as exc:
        _refuse(exc)
    _status(session)
