"""owns: start, best, mission, onboard, context, stance, project-bootstrap, project-scaffold, work, work-init, work-close, resume, note, ask, session-log, remember, workflow, env, setup, limits, ladder, plan

The project under work: its mission, intent, toolchain, tracker, session memory,
and the startup contract. Nothing here inspects A.W.I.N.O.'s own installation.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from smith import (
    capability,
    cli,
    completion_review,
    exam,
    fix,
    health,
    heilmeier,
    mission,
    models,
    onboarding,
    playbook,
    project_guard,
    project_template,
    provision,
    recall,
    seeds,
    session_log,
    session_state,
    stance,
    stepper,
)
from smith.cli import (
    _echo,
    _ledger,
    _ledger_error,
    _paths,
    _require_valid_plan,
    _skill_catalog,
    _toolchain,
    _workspace,
    app,
)
from smith.enforce import (
    Ledger,
    LedgerError,
    TaskClass,
    adjudicate,
)
from smith.toolchain import Manager, Toolchain, tool_install_command


@app.command("project-scaffold")
def project_scaffold_command(
    name: str = typer.Option(None, "--name", help="Project name; defaults to the folder name"),
    description: str = typer.Option("", "--description", help="One-line project description"),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Replace an existing pyproject.toml/justfile"
    ),
    force: bool = typer.Option(
        False,
        "--i-know-this-is-the-right-folder",
        help="Override the multi-project-container refusal",
    ),
) -> None:
    """Write a fresh, generic pyproject.toml and justfile into this project.

    Only for a project with no Python declaration at all - if pyproject.toml,
    setup.py, requirements.txt, or a lockfile already exists, use 'awino
    project-bootstrap' instead, which respects what is already there. This
    command never reads or stores any project's name/description anywhere
    outside the target project itself; the template is generic and ships
    with A.W.I.N.O., the instantiated file lives only where you run this.

    Refuses when this folder looks like a container of several independent
    projects (multiple subdirectories each with their own .git/pyproject.toml/
    package.json) rather than being a single project itself - live-caught
    running this against exactly that shape of folder, which wrote a real
    pyproject.toml at the wrong level before this check existed.
    """
    root = _workspace().project.root
    subprojects = project_template.independent_subprojects(root)
    if len(subprojects) >= 2 and not force:
        _echo(
            f"REFUSED  MULTI_PROJECT_CONTAINER  {root} contains {len(subprojects)} "
            "independent-looking subdirectories, not itself:"
        )
        for item in subprojects[:8]:
            _echo(f"  - {item.name}")
        _echo("")
        _echo(
            "Run this from inside the specific subproject you mean, or pass "
            "--i-know-this-is-the-right-folder if this container is genuinely "
            "the intended scaffolding target."
        )
        raise typer.Exit(1)
    resolved_name = name or root.name
    results = project_template.scaffold(
        root, resolved_name, description=description, overwrite=overwrite
    )
    for item in results:
        _echo(f"{item.outcome.upper()}  {item.path.name}  {item.detail}")
    written = [item for item in results if item.outcome == "written"]
    if written:
        _echo("")
        _echo("Run 'awino project-bootstrap' next to confirm environment/tracker/runner setup.")


@app.command("remember")
def remember_command(
    value: str = typer.Argument(..., help="Durable project fact or rule to remember"),
    kind: str = typer.Option(
        "tenet",
        "--as",
        help="tenet, goal, expectation, non-goal, mission, primary-user, success-metric",
    ),
) -> None:
    """Persist an explicit user memory in this project's confirmed intent."""
    project = _workspace().project.root
    found = mission.discover(project, tracker=seeds.Seeds(project))
    intent = onboarding.load(project) or onboarding.seed_from_mission(found)
    try:
        onboarding.remember(intent, kind, value)
    except ValueError as exc:
        _echo(str(exc))
        raise typer.Exit(2) from exc
    saved = onboarding.save(project, intent)
    _echo(f"REMEMBERED  {kind}  {value.strip()}")
    _echo(f"PROJECT     {saved}")


@app.command("ask")
def ask_command(
    question: str = typer.Argument(..., help="The planning/clarifying question about to be asked"),
) -> None:
    """Check a planning question against this session before asking it.

    This is the mechanical enforcement point the 'adaptive grill' referenced
    by awino-delegate's planning step needed and did not have: that
    reference was prose ("use the adaptive grill"), which cannot enforce
    itself. Call this before posing any clarifying question during planning.
    Exits 1 with the earlier turn if an equivalent question/answer already
    exists this session - the same asymmetry onboarding.frontier() already
    uses for its own fixed six questions, extended to arbitrary planning
    questions via session_log rather than a closed vocabulary.
    """
    workspace = _workspace()
    session = session_state.load(workspace.state_root)
    session_id = session.session_id if session else "unknown"
    duplicate = session_log.find_duplicate_question(
        workspace.state_root, session_id, question, kind="agent_question"
    )
    if duplicate is not None:
        _echo(
            f"ALREADY_ASKED  turn={duplicate.turn}  {duplicate.text!r}. "
            "Do not re-ask this; if the prior answer might be stale, say why "
            "and confirm instead of asking fresh."
        )
        raise typer.Exit(1)
    ask = session_log.append(workspace.state_root, session_id, "agent_question", question)
    _echo(f"CLEAR_TO_ASK  turn={ask.turn}")


@app.command("note")
def note_command(
    text: str = typer.Argument(..., help="What the human said, corrected, or asked"),
    kind: str = typer.Option("correction", "--as", help="user_turn, agent_question, or correction"),
    run_id: str | None = typer.Option(None, "--run", help="Link to the active run, if any"),
) -> None:
    """Record one session-scoped ask/instruction/correction.

    This is separate from Seeds and separate from the gate ledger: it holds
    what was said this conversation, not tasks or verified evidence. Most of
    a real conversation happens with no open gate run at all, so this exists
    independently of Run/Checkpoint rather than requiring one.
    """
    workspace = _workspace()
    session = session_state.load(workspace.state_root)
    session_id = session.session_id if session else "unknown"
    ask = session_log.append(workspace.state_root, session_id, kind, text, run_id=run_id)
    _echo(f"NOTED  turn={ask.turn}  kind={kind}")


@app.command("session-log")
def session_log_command(
    session: str | None = typer.Option(None, "--session", help="Session id, defaults to current"),
) -> None:
    """Show this session's recorded corrections and unresolved agent questions.

    Read-only inspection of the log 'awino note' and the UserPromptSubmit
    hook write to. Corrections are the most direct evidence that an earlier
    instruction was not honored.
    """
    workspace = _workspace()
    if session is None:
        current = session_state.load(workspace.state_root)
        session = current.session_id if current else "unknown"
    corrections = session_log.corrections(workspace.state_root, session)
    unresolved = session_log.unresolved_questions(workspace.state_root, session)
    _echo(f"SESSION  {session}")
    if corrections:
        _echo(f"CORRECTIONS  {len(corrections)}")
        for item in corrections:
            _echo(f"  turn={item.turn}  {item.text}")
    else:
        _echo("CORRECTIONS  none recorded")
    if unresolved:
        _echo(f"UNRESOLVED_QUESTIONS  {len(unresolved)}")
        for item in unresolved:
            _echo(f"  turn={item.turn}  {item.text}")
    else:
        _echo("UNRESOLVED_QUESTIONS  none")


@app.command("workflow")
def workflow_command(
    issue_pattern: str = typer.Option(None, "--issue-pattern"),
    base_branch: str = typer.Option(None, "--base-branch"),
    branch_pattern: str = typer.Option(None, "--branch-pattern"),
    changelog_file: str = typer.Option(None, "--changelog-file"),
    one_task_per_session: bool | None = typer.Option(
        None, "--one-task-per-session/--allow-multiple-tasks"
    ),
    planning_interview: str = typer.Option(None, "--planning-interview"),
) -> None:
    """Configure mechanically enforced project workflow rules."""
    project = _workspace().project.root
    intent = onboarding.load(project)
    if intent is None:
        _echo("NO_PROJECT_INTENT  run awino onboard first")
        raise typer.Exit(2)
    workflow = intent.workflow
    if issue_pattern is not None:
        re.compile(issue_pattern)
        workflow.issue_pattern = issue_pattern
        workflow.issue_required = bool(issue_pattern)
    if branch_pattern is not None:
        re.compile(branch_pattern)
        workflow.branch_pattern = branch_pattern
    if base_branch is not None:
        workflow.base_branch = base_branch
    if changelog_file is not None:
        workflow.changelog_file = changelog_file
    if one_task_per_session is not None:
        workflow.one_task_per_session = one_task_per_session
    if planning_interview is not None:
        workflow.planning_interview = planning_interview
    saved = onboarding.save(project, intent)
    _echo(f"WORKFLOW  {saved}")
    _echo(project_guard.project_context(intent))


@app.command("plan")
def plan_command(
    request: str = typer.Argument(..., help="What you are trying to do, in your own words"),
    task_class: TaskClass = typer.Option(TaskClass.CODE_CHANGE, "--class", help="Task class"),
    understood: bool = typer.Option(False, "--understood", help="The current system is documented"),
    interfaces: bool = typer.Option(False, "--interfaces-settled", help="Interfaces will not move"),
    disjoint: bool = typer.Option(False, "--units-disjoint", help="Work units share no files"),
    inspected: bool = typer.Option(
        True, "--inspected/--not-inspected", help="A human looked recently"
    ),
    run_id: str = typer.Option(None, "--run", help="Read verification evidence from this run"),
) -> None:
    """Apply the mental models to a request and produce a reasoned plan.

    The models are decision functions, not documents. They answer: which rung is
    this really on, where is the binding constraint, how much autonomy does the
    current verification support, and is the loop rotting.
    """
    paths = _paths()
    ledger = _ledger()
    resolved = run_id or ledger.current_id()
    evidence = ledger.evidence(resolved) if resolved else []

    store = cli.KnowledgeStore(paths)
    plan = models.build_plan(
        request,
        task_class=task_class,
        evidence=evidence,
        understood=understood,
        interfaces_settled=interfaces,
        units_disjoint=disjoint,
        knowledge_age_days=store.manifest.newest_age_days,
        stale_after_days=store.stale_days,
        human_inspected_recently=inspected,
        pre_execution=resolved is None,
    )

    _echo(f"REQUEST  {request}")
    _echo(f"run      {resolved or 'none, so verification reads as unproven'}")
    _echo("")

    _echo("LEVERAGE LADDER")
    _echo(f"  actual rung: {plan.rung.actual.name.lower()} ({plan.rung.reason})")
    _echo(f"  {plan.rung.advice}")
    _echo("")

    _echo("CONSTRAINT")
    _echo(f"  {plan.constraint.constraint}: {plan.constraint.reason}")
    _echo(f"  spend effort on {plan.constraint.constraint.spend_effort_on}")
    _echo("")

    _echo("VERIFIER STRENGTH")
    _echo(f"  executed: {', '.join(plan.verifier.executed) or 'none'}")
    _echo(f"  attested: {', '.join(plan.verifier.attested) or 'none'}")
    _echo(f"  missing:  {', '.join(plan.verifier.missing) or 'none'}")
    _echo(
        f"  max autonomy: {plan.verifier.max_autonomy.name.lower()} ({plan.verifier.max_autonomy.meaning})"
    )
    for reason in plan.verifier.reasons:
        _echo(f"  because {reason}")
    _echo("")

    visible_anti_patterns = [
        hit
        for hit in plan.anti_patterns
        if not (plan.pre_execution and hit.pattern is models.AntiPattern.OPEN_LOOP)
    ]
    if visible_anti_patterns:
        _echo("ANTI-PATTERNS")
        for hit in visible_anti_patterns:
            _echo(f"  {hit.pattern}: {hit.evidence}")
            _echo(f"    -> {hit.fix}")
        _echo("")

    _echo(f"FAN OUT   {'yes' if plan.may_fan_out else 'no, not yet'}")
    _echo(f"NEXT      {plan.next_action}")


@app.command()
def ladder() -> None:
    """Show the leverage ladder, which decides what you should be authoring."""
    _echo("rung        authored artifact                    human position")
    _echo("-" * 78)
    for rung in models.Rung:
        _echo(f"{rung.name.lower():<11} {rung.authored_artifact:<36} {rung.human_position}")
    _echo("")
    _echo("Intervening further upstream changes more behaviour per unit of effort.")
    _echo("The common error is treating a higher-rung problem at a lower rung:")
    _echo("rewording a prompt to fix an environment defect, or hand-prompting")
    _echo("work that recurs nightly.")


@app.command("work")
def work_command(
    verify_only: bool = typer.Option(False, "--verify", help="Only issues that describe a check"),
    limit: int = typer.Option(15, "--limit", help="How many to show"),
) -> None:
    """Show tracked work that is ready to start, from the project's own tracker.

    Seeds is optional. When absent this reports that and stops, rather than
    inventing a worklist A.W.I.N.O. would then be the only one aware of.
    """
    workspace = _workspace()
    tracker = seeds.Seeds(workspace.project.root)
    state, reason = tracker.state()

    _echo(f"project: {workspace.project.root}")
    _echo(f"tracker: {state} ({reason})")

    if not state.usable:
        _echo("")
        _echo("No tracker, so work is untracked. A.W.I.N.O. will not create one unasked.")
        _echo(f"  install: {seeds.INSTALL_HINT}")
        _echo(f"  init:    {seeds.INIT_HINT}   (or: awino work-init --confirm)")
        return

    issues = tracker.verification_issues(limit) if verify_only else tracker.ready(limit)
    if not issues:
        _echo("")
        _echo("nothing ready" if not verify_only else "no issues awaiting verification")
        return

    _echo("")
    label = "AWAITING VERIFICATION" if verify_only else "READY"
    _echo(f"{label} ({len(issues)})")
    for issue in issues:
        marker = " [verify]" if issue.wants_verification and not verify_only else ""
        _echo(f"  {issue.id:<22} {issue.priority_label:<8} {issue.title}{marker}")

    _echo("")
    _echo("Start one with a gated run:")
    _echo(f'  awino gate open code-change "<objective>" --issue {issues[0].id}')


@app.command("work-init")
def work_init_command(
    confirm: bool = typer.Option(False, "--confirm", help="Skip the prompt and initialize"),
    no_input: bool = typer.Option(
        False, "--no-input", help="Never prompt, for non-interactive use"
    ),
) -> None:
    """Offer to initialize a seeds tracker, then do it if the human agrees.

    Refusing silently leaves the user stuck. Acting silently mutates a repository
    A.W.I.N.O. may not own. So A.W.I.N.O. states exactly what would change, asks, and abides
    by the answer.
    """
    workspace = _workspace()
    tracker = seeds.Seeds(workspace.project.root)
    state, reason = tracker.state()

    _echo(f"project: {workspace.project.root}")
    _echo(f"tracker: {state} ({reason})")

    if state.usable:
        _echo("Nothing to do.")
        return

    if not tracker.installed:
        _echo("")
        _echo(f"seeds is not installed. Install it with:  {seeds.INSTALL_HINT}")
        _echo("A.W.I.N.O. does not install global tooling on your behalf.")
        raise typer.Exit(1)

    _echo("")
    _echo("Initializing would write, inside your repository:")
    for line in seeds.INIT_EFFECTS:
        _echo(f"  {line}")
    _echo("")
    _echo("Reversible: delete .seeds/ and revert .gitattributes.")

    approved = confirm
    if not approved and not no_input:
        approved = typer.confirm("Create the tracker now?", default=False)

    if not approved:
        _echo("")
        _echo(f"Declined. Run '{seeds.INIT_HINT}' yourself whenever you want one.")
        raise typer.Exit(1)

    result = tracker.init(confirmed=True)
    _echo("")
    if not result.ok:
        _echo(f"FAILED  {result.detail}")
        raise typer.Exit(1)
    _echo("INITIALIZED  tracker created")
    _echo("Next: awino work")


@app.command("work-close")
def work_close_command(
    issue_id: str = typer.Argument(None, help="Issue to close; inferred from the run when omitted"),
    run_id: str = typer.Option(None, "--run", help="Run whose evidence justifies closure"),
    force: bool = typer.Option(
        False, "--force", help="Close despite unmet gates, recorded as such"
    ),
) -> None:
    """Close a tracked issue using gate evidence as the close reason.

    An issue closed with "done" proves nothing and that claim survives in git
    history. This closes with the recorded exit codes instead, so the reason is
    auditable.
    """
    workspace = _workspace()
    tracker = seeds.Seeds(workspace.project.root)
    state, reason = tracker.state()
    if not state.usable:
        _echo(f"NO TRACKER  {reason}")
        raise typer.Exit(1)

    ledger = _ledger()
    resolved = run_id or ledger.current_id()
    verdict = None
    evidence: list = []
    run = None
    if resolved:
        try:
            run = ledger.load(resolved)
            _require_valid_plan(ledger, resolved)
        except LedgerError as exc:
            _ledger_error(exc)
        issue_id = issue_id or run.issue_id
        evidence = ledger.evidence(resolved)
        verdict = adjudicate(run, evidence)

    if not issue_id:
        _echo("NO_ISSUE  pass an issue id or link one with gate open --issue")
        raise typer.Exit(2)

    check = seeds.check_closure(issue_id, verdict, evidence)
    _echo(f"issue:    {issue_id}")
    _echo(f"run:      {resolved or 'none'}")
    _echo(f"evidence: {check.evidence_summary}")

    if not check.may_close and not force:
        _echo("")
        _echo(f"REFUSED  {check.reason}")
        _echo("Satisfy the gates, or pass --force to close with the shortfall recorded.")
        raise typer.Exit(1)

    reason_text = check.close_reason if check.may_close else f"FORCED despite {check.reason}"
    if run is not None and run.provenance is not None:
        reason_text = f"{reason_text} [review: {completion_review.provenance_summary_for_seed(run.provenance)}]"
    result = tracker.close(issue_id, reason_text)
    _echo("")
    if not result.ok:
        _echo(f"FAILED  {result.detail}")
        raise typer.Exit(1)
    _echo(f"CLOSED  {issue_id}")
    _echo(f"reason  {reason_text}")


@app.command("mission")
def mission_command(
    as_json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
    heilmeier_walk: bool = typer.Option(
        False, "--heilmeier", help="Walk the Heilmeier catechism as a living mission document"
    ),
    set_answer: str = typer.Option(
        None, "--set", help="Answer a Heilmeier question: key=text (multi-line with \\n)"
    ),
) -> None:
    """Read what the project is for, from its own authored sources.

    A.W.I.N.O. never invents a mission. An agent acting confidently on a fabricated
    purpose is worse than one that asks, because the fabrication propagates into
    every downstream plan.

    --heilmeier turns the mission into a living document: eight questions,
    prefilled where the project already knows the answer (labeled), one gap
    asked at a time in the stance that question calls for, exams wired to
    verification commands, and derived insights written to .smith/MISSION.md.
    """
    workspace = _workspace()
    tracker = seeds.Seeds(workspace.project.root)

    if heilmeier_walk or set_answer:
        cat = heilmeier.load(workspace.state_root)
        if set_answer:
            key, _, text = set_answer.partition("=")
            if key not in {q.key for q in heilmeier.QUESTIONS}:
                _echo(
                    f"REFUSED  unknown key {key!r}; keys: {', '.join(q.key for q in heilmeier.QUESTIONS)}"
                )
                raise typer.Exit(2)
            cat.answers[key] = text.replace("\\n", "\n").strip()
            cat.source[key] = "human"
            heilmeier.save(workspace.state_root, cat)
            _echo(f"ANSWERED  {key}")
        open_titles = [i.title for i in tracker.list_open()] if tracker.state()[0].usable else []
        doc = heilmeier.render(workspace.state_root, cat, open_seeds=open_titles)
        _echo(f"MISSION_DOC  {doc}")
        answered = sum(1 for q in heilmeier.QUESTIONS if cat.answers.get(q.key, "").strip())
        _echo(f"ANSWERED  {answered}/8")
        for line in heilmeier.insights(cat, open_seeds=open_titles):
            _echo(f"INSIGHT  {line}")
        gap = cat.next_gap()
        if gap is not None:
            _echo(f"STANCE  -> {gap.stance}")
            _echo(f"QUESTION  [{gap.key}] {gap.text}")
            _echo(f'          answer with: awino mission --set "{gap.key}=<your answer>"')
        else:
            _echo("COMPLETE  all eight answered; exam commands are gate-ready")
        return

    found = mission.discover(workspace.project.root, tracker=tracker)

    if as_json_output:
        _echo(
            json.dumps(
                {
                    "project": found.project,
                    "statement": found.statement,
                    "confidence": str(found.confidence),
                    "kind": str(found.kind),
                    "known": found.known,
                    "non_goals": found.non_goals,
                    "evidence": [str(e) for e in found.evidence],
                    "advice": found.advice(),
                },
                indent=2,
            )
        )
        return

    _echo(f"PROJECT   {found.project}")
    _echo(f"KIND      {found.kind}  ({found.kind.expectations})")
    _echo(f"MISSION   {found.summary}")
    _echo(f"          confidence: {found.confidence}")
    _echo("")

    if found.agent_instructions:
        _echo(f"AGENT INSTRUCTIONS  {', '.join(found.agent_instructions)}")
    if found.non_goals:
        _echo("NON-GOALS")
        for goal in found.non_goals[:6]:
            _echo(f"  - {goal}")
    if found.open_work:
        _echo("CURRENT WORK")
        for title in found.open_work[:5]:
            _echo(f"  - {title}")

    _echo("")
    _echo("EVIDENCE")
    for item in found.evidence:
        text = item.text if len(item.text) <= 90 else item.text[:87] + "..."
        _echo(f"  {item.source}: {text}")

    _echo("")
    _echo("HOW A.W.I.N.O. WILL CALIBRATE")
    for item in found.advice():
        _echo(f"  - {item}")


@app.command("onboard")
def onboard_command(
    set_value: list[str] = typer.Option(
        None,
        "--set",
        help="Confirm a field as key=value. Repeatable; list fields use semicolons.",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Persist the current draft after all required fields are present.",
    ),
    init_seeds: bool = typer.Option(
        False,
        "--with-seeds",
        help="Initialize the optional Seeds tracker after explicit confirmation.",
    ),
    as_json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Run the mission-first project handshake.

    This is the canonical first command in a new repository. It composes context,
    mission, project intent, toolchain, tracker state, and next skill selection so
    the user does not have to know six separate commands or their order.
    """
    workspace = _workspace()
    project = workspace.project.root
    tracker = seeds.Seeds(project)
    found = mission.discover(project, tracker=tracker)
    intent = onboarding.load(project) or onboarding.seed_from_mission(found)

    for assignment in set_value or []:
        if "=" not in assignment:
            _echo(f"INVALID --set {assignment!r}; expected key=value")
            raise typer.Exit(2)
        key, _, value = assignment.partition("=")
        try:
            onboarding.apply(intent, key.strip(), value)
        except ValueError as exc:
            _echo(str(exc))
            raise typer.Exit(2) from exc

    # Persist partial answers after every turn. Without this, the command asks one
    # question at a time but forgets each answer before the next invocation—the
    # exact opposite of mission-first onboarding.
    if set_value:
        onboarding.save(project, intent)

    questions = onboarding.frontier(intent)

    if confirm:
        if questions:
            _echo(
                f"REFUSED  unresolved fields: {', '.join(question.key for question in questions)}"
            )
            _echo("Answer one at a time with: awino onboard --set key=value")
            raise typer.Exit(1)
        intent.source = "confirmed"
        saved = onboarding.save(project, intent)
    else:
        saved = None

    state, tracker_reason = tracker.state()
    if init_seeds and not state.usable:
        result = tracker.init(confirmed=True)
        state, tracker_reason = tracker.state()
        if not result.ok:
            _echo(f"SEEDS FAILED  {result.detail}")

    if as_json_output:
        _echo(onboarding.as_json(intent, questions))
        return

    _echo("IDENTITY")
    _echo(f"  A.W.I.N.O. home  {workspace.home.root}")
    _echo(f"  project     {project}")
    _echo("")
    _echo("MISSION DRAFT")
    _echo(f"  {intent.mission or '(unknown)'}")
    _echo(f"  source: {intent.source}")
    _echo(f"  project kind: {found.kind} — {found.kind.expectations}")
    if intent.non_goals:
        _echo("  non-goals:")
        for item in intent.non_goals:
            _echo(f"    - {item}")
    _echo("")

    chain = Toolchain(project)
    _echo("TOOLCHAIN")
    for name, tool in chain.summary().items():
        _echo(f"  {name:<8} {tool.command or 'unavailable'}")
        _echo(f"           because {tool.reason}")
    _echo("")
    _echo(f"TRACKER   {state} ({tracker_reason})")
    if not state.usable:
        _echo("          optional: awino onboard --with-seeds")
    _echo("")
    _echo("PROJECT BOOTSTRAP")
    _echo(_bootstrap_status(project, intent))
    if not onboarding.bootstrap_current(project, intent):
        _echo("  inspect and confirm: awino project-bootstrap")

    if saved:
        _echo("")
        _echo(f"CONFIRMED  {saved}")
        _echo('NEXT       awino plan "<your first concrete task>"')
        return

    _echo("")
    if questions:
        question = questions[0]
        _echo("NEXT QUESTION")
        _echo(f"  {question.prompt}")
        _echo(f"  Why: {question.why}")
        _echo(f'  Answer: awino onboard --set {question.key}="<your answer>"')
    else:
        _echo("READY TO CONFIRM")
        _echo("  awino onboard --confirm")


def _bootstrap_status(project: Path, intent: onboarding.ProjectIntent | None) -> str:
    if onboarding.bootstrap_current(project, intent):
        assert intent is not None and intent.bootstrap is not None
        state = intent.bootstrap
        return (
            f"BOOTSTRAP_CURRENT environment={state.environment} tracker={state.tracker} "
            f"runner={state.runner} confirmed_by={state.confirmed_by}"
        )
    if intent and intent.bootstrap:
        return "BOOTSTRAP_STALE project declarations changed; inspect before reconfirming"
    return "BOOTSTRAP_REQUIRED no confirmed project setup decision"


@app.command("project-bootstrap")
def project_bootstrap_command(
    environment: onboarding.EnvironmentDecision | None = typer.Option(None, "--environment"),
    tracker: onboarding.TrackerDecision | None = typer.Option(None, "--tracker"),
    runner: onboarding.RunnerDecision | None = typer.Option(None, "--runner"),
    confirm: bool = typer.Option(False, "--confirm", help="Persist and execute selected decisions"),
    actor: str = typer.Option("human", "--by", help="Actor confirming these decisions"),
    as_json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Inspect project setup, or explicitly confirm environment/tracker/runner choices."""
    workspace = _workspace()
    project = workspace.project.root
    intent = onboarding.load(project)
    chain = Toolchain(project)
    tracker_client = seeds.Seeds(project)
    tracker_state, tracker_reason = tracker_client.bootstrap_state()
    manager, manager_reason = chain.manager
    detected_runner, runner_reason = chain.runner
    fingerprint = onboarding.bootstrap_fingerprint(project)
    status = _bootstrap_status(project, intent)

    if not confirm:
        if any(choice is not None for choice in (environment, tracker, runner)):
            _echo("REFUSED  decisions are persisted or executed only with --confirm")
            raise typer.Exit(2)
        if as_json_output:
            _echo(
                json.dumps(
                    {
                        "project": str(project),
                        "status": status,
                        "fingerprint": fingerprint,
                        "environment": {
                            "manager": str(manager),
                            "reason": manager_reason,
                            "setup_command": chain.install_command.command,
                            "guidance": chain.environment_guidance,
                        },
                        "tracker": {"state": str(tracker_state), "reason": tracker_reason},
                        "runner": {"detected": str(detected_runner), "reason": runner_reason},
                    },
                    indent=2,
                )
            )
            return
        _echo(status)
        _echo(f"project: {project}")
        _echo(f"environment: {manager} ({manager_reason})")
        _echo(f"setup command: {chain.install_command.command or 'unavailable'}")
        for item in chain.environment_guidance:
            _echo(f"  - {item}")
        _echo(f"tracker: {tracker_state} ({tracker_reason})")
        _echo(f"runner: {detected_runner} ({runner_reason})")
        missing = chain.missing_runner_binary
        if missing is not None:
            binary, benefit = missing
            _echo(f"  {binary} is declared by this project but not installed")
            _echo(f"  why install it: {benefit}")
            found = tool_install_command(binary)
            if found is not None:
                command, package_manager = found
                _echo(f"  install with: {' '.join(command)}  (via {package_manager})")
                _echo(
                    "  confirm with: awino project-bootstrap ... --runner install-missing --confirm"
                )
            else:
                _echo(f"  no known install command for '{binary}' on this platform")
        _echo("")
        _echo("No changes made. Confirm all three decisions explicitly with:")
        _echo(
            "  awino project-bootstrap --environment <choice> --tracker <choice> "
            "--runner <choice> --confirm"
        )
        return

    if any(choice is None for choice in (environment, tracker, runner)):
        _echo("REFUSED  --confirm requires --environment, --tracker, and --runner")
        raise typer.Exit(2)
    if not actor.strip():
        _echo("REFUSED  --by must name the confirming actor")
        raise typer.Exit(2)

    assert environment is not None and tracker is not None and runner is not None
    install = chain.install_command
    if environment is onboarding.EnvironmentDecision.SETUP:
        subprojects = project_template.independent_subprojects(project)
        if len(subprojects) >= 2:
            _echo(
                f"REFUSED  MULTI_PROJECT_CONTAINER  {project} contains "
                f"{len(subprojects)} independent-looking subdirectories; run this "
                "from inside the specific subproject instead"
            )
            raise typer.Exit(1)
        if not install.usable:
            _echo(f"REFUSED  environment setup unavailable: {install.reason}")
            raise typer.Exit(1)
        _echo(f"ENVIRONMENT SETUP  {install.command}")
        code = subprocess.run(install.command, shell=True, cwd=str(project), check=False).returncode
        if code:
            _echo(f"FAILED  environment setup exit {code}")
            raise typer.Exit(code)
    elif environment is onboarding.EnvironmentDecision.USE_EXISTING and manager in {
        Manager.NONE,
        Manager.SYSTEM,
    }:
        _echo("REFUSED  no existing project environment or manager is usable")
        raise typer.Exit(1)
    elif environment is onboarding.EnvironmentDecision.NOT_APPLICABLE and chain.python_project:
        _echo("REFUSED  environment cannot be not-applicable for a detected Python project")
        raise typer.Exit(1)

    if runner is onboarding.RunnerDecision.INSTALL_MISSING:
        missing = chain.missing_runner_binary
        if missing is None:
            _echo("REFUSED  no declared task-runner file is missing its binary; nothing to install")
            raise typer.Exit(1)
        binary, benefit = missing
        found = tool_install_command(binary)
        if found is None:
            _echo(
                f"REFUSED  no known install command for '{binary}' on this platform; "
                "install it manually, then re-run with --runner use-detected"
            )
            raise typer.Exit(1)
        command, package_manager = found
        _echo(f"RUNNER INSTALL  {binary} via {package_manager}: {' '.join(command)}")
        _echo(f"  why: {benefit}")
        code = subprocess.run(command, cwd=str(project), check=False).returncode
        if code:
            _echo(f"FAILED  {binary} install exit {code}")
            raise typer.Exit(code)
        detected_runner, runner_reason = chain.runner
        _echo(f"INSTALLED  {binary}; runner is now {detected_runner} ({runner_reason})")

    if tracker is onboarding.TrackerDecision.INITIALIZE:
        _echo("TRACKER INITIALIZE  repository-root .seeds and .gitattributes")
        result = tracker_client.init(confirmed=True)
        if not result.ok:
            _echo(f"FAILED  {result.detail}")
            raise typer.Exit(1)
    elif tracker is onboarding.TrackerDecision.USE_EXISTING and not tracker_state.usable:
        _echo("REFUSED  no repository-root tracker is usable")
        raise typer.Exit(1)

    intent = intent or onboarding.ProjectIntent(mission="", source="draft")
    intent.bootstrap = onboarding.BootstrapState(
        environment=str(environment),
        tracker=str(tracker),
        runner=str(runner),
        detected_manager=str(manager),
        detected_runner=str(detected_runner),
        environment_command=install.command or "",
        tracker_root=str(project) if tracker_client.initialized else "",
        fingerprint=onboarding.bootstrap_fingerprint(project),
        confirmed_by=actor.strip(),
        confirmed_at=datetime.now(UTC).isoformat(),
    )
    saved = onboarding.save(project, intent)
    _echo(f"BOOTSTRAP_CONFIRMED environment={environment} tracker={tracker} runner={runner}")
    _echo(f"PROJECT  {saved}")


@app.command("limits")
def limits_command(
    claims: bool = typer.Option(False, "--claims", help="Audit documented claims against reality"),
    strict: bool = typer.Option(
        False, "--strict", help="Exit nonzero if any documented claim is false"
    ),
) -> None:
    """Report what A.W.I.N.O. can actually do, probed rather than claimed.

    This exists because of a real failure: the persona said A.W.I.N.O. "spawns scoped
    subagents" and a skill described how, while no spawn code existed. Prose
    describing a capability is indistinguishable from prose describing an
    aspiration, so capabilities are probed at call time and the probe wins.
    """
    caps = capability.assess()

    if claims:
        rows = capability.audit_claims()
        false_claims = [(c, cap) for c, cap in rows if not cap.state.claimable]
        for claim, cap in rows:
            mark = (
                "OK" if cap.state.claimable else ("CAVEAT" if cap.state.needs_caveat else "FALSE")
            )
            _echo(f'  {mark:<7} "{claim}"')
            _echo(f"          {cap.honest_claim}")
        _echo("")
        _echo(f"CLAIMS  {len(rows) - len(false_claims)}/{len(rows)} supported by a passing probe")
        if false_claims and strict:
            _echo("")
            _echo("UNGROUNDED_CAPABILITY: change the document or write the code.")
            raise typer.Exit(1)
        return

    width = max(len(c.name) for c in caps)
    for cap in caps:
        _echo(f"  {cap.state:<9} {cap.name:<{width}}  {cap.detail}")
        if cap.limit:
            _echo(f"  {'':<9} {'':<{width}}  LIMIT: {cap.limit}")

    counts = capability.summary(caps)
    _echo("")
    _echo(
        f"CAPABILITY  real={counts['real']}  degraded={counts['degraded']}  absent={counts['absent']}"
    )
    _echo("")
    _echo("Degraded means usable with the stated limit attached to any claim.")
    _echo("Absent means claiming it is UNGROUNDED_CAPABILITY.")


@app.command()
def context() -> None:
    """Show what A.W.I.N.O. considers home, what it considers the project, and the toolchain.

    Run this first in an unfamiliar repository. Every gate command A.W.I.N.O. records
    comes from this resolution, so a wrong answer here makes every later green gate
    meaningless.
    """
    workspace = _workspace()
    chain = _toolchain(workspace)

    _echo("IDENTITY")
    _echo(f"  A.W.I.N.O. home    {workspace.home.root}")
    _echo(f"  project       {workspace.project.root}")
    _echo(
        f"  layout        {'working on A.W.I.N.O. itself' if workspace.working_on_self else workspace.describe()}"
    )
    _echo(f"  run ledger    {workspace.runs}")
    _echo("")

    _echo("PROJECT TOOLCHAIN")
    summary = chain.summary()
    width = max(len(k) for k in summary)
    for name, tool in summary.items():
        shown = tool.command or "unavailable"
        _echo(f"  {name:<{width}}  {shown}")
        _echo(f"  {'':<{width}}    because {tool.reason}")
    _echo("")

    runner, runner_reason = chain.runner
    _echo(f"  task runner   {runner} ({runner_reason})")
    if chain.recipes:
        _echo(f"  recipes       {', '.join(chain.recipes[:12])}")

    if chain.blocking_gaps:
        _echo("")
        _echo("GAPS")
        for gap in chain.blocking_gaps:
            _echo(f"  - {gap}")
    if chain.advice:
        _echo("")
        _echo("ADVICE")
        for item in chain.advice:
            _echo(f"  - {item}")


@app.command("env")
def environment() -> None:
    """Explain the target project's Python environment without changing it."""
    workspace = _workspace()
    chain = _toolchain(workspace)
    manager, reason = chain.manager
    project_env = chain.in_project_venv

    _echo(f"project: {workspace.project.root}")
    _echo(f"manager: {manager}")
    _echo(f"detected because: {reason}")
    _echo(f"environment: {project_env or 'not created'}")
    for item in chain.environment_guidance:
        _echo(item)
    if manager is Manager.UV and project_env is None:
        _echo("Create it only when intended: awino project-bootstrap --environment setup --confirm")


@app.command()
def setup(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the command without running it"),
) -> None:
    """Install the project's dependencies using whatever manager it already uses.

    A.W.I.N.O. does not impose uv. It reproduces a committed lockfile when one exists
    and creates no environment unless this command is explicitly run without --dry-run.
    """
    workspace = _workspace()
    chain = _toolchain(workspace)
    install = chain.install_command

    _echo(f"project: {workspace.project.root}")
    manager, reason = chain.manager
    _echo(f"manager: {manager} ({reason})")

    if not install.usable:
        _echo(f"CANNOT INSTALL  {install.reason}")
        for item in chain.advice:
            _echo(f"  - {item}")
        raise typer.Exit(1)

    _echo(f"command: {install.command}")
    if dry_run:
        _echo("DRY_RUN  nothing executed")
        return

    code = subprocess.run(
        install.command, shell=True, cwd=str(workspace.project.root), check=False
    ).returncode
    if code != 0:
        _echo(f"FAILED  exit {code}")
        raise typer.Exit(code)
    _echo("INSTALLED")


@app.command("stance")
def stance_command(
    set_name: str = typer.Option(None, "--set", help="Persist a default stance for this project"),
    for_text: str = typer.Option(
        None, "--for", help="Print the stance this message calls for, and why"
    ),
) -> None:
    """Show, set, or detect the conversational stance.

    A stance is the controller's posture toward the human - advisor,
    steel-man, teach-back - switched by the human's own words rather than a
    name they must remember. Switches are never silent: callers print one
    STANCE line whenever detection differs from the current stance.
    """
    workspace = _workspace()
    project = workspace.project.root

    if set_name is not None:
        try:
            stance.save_default(project, set_name)
        except ValueError as exc:
            _echo(f"REFUSED  {exc}")
            raise typer.Exit(2) from exc
        _echo(f"STANCE_DEFAULT  {set_name}")
        return

    current = stance.load_default(project)
    if for_text is not None:
        detected = stance.detect(for_text)
        if detected is None or detected.name == current:
            _echo(f"STANCE  {current} (unchanged)")
        else:
            _echo(f"STANCE  -> {detected.name} ({detected.trigger_description})")
            _echo(detected.rules)
        return

    _echo(f"STANCE  {current} (default for this project)")
    for item in stance.STANCES:
        marker = "*" if item.name == current else " "
        _echo(f"  {marker} {item.name:<17} {item.trigger_description}")


@app.command("best")
def best_command(
    request: str = typer.Argument(
        None, help="What you want, in your words - the operator names the floor and guides you"
    ),
    end: bool = typer.Option(False, "--end", help="Run the session-end order instead"),
) -> None:
    """Run the written session order - the one word to remember.

    `awino best` alone: session-start (startup report, the next Heilmeier gap
    as a question, the next Seed, relevant lessons). `awino best "<request>"`:
    elevator mode - locate where you are, route your words to one skill,
    switch stance if your words call for it, recall what we learned last time,
    and print the exact next commands or the one question that blocks routing.
    Nothing is spawned. --end: session-end order.
    """
    workspace = _workspace()
    tracker = seeds.Seeds(workspace.project.root)
    open_titles = [i.title for i in tracker.list_open()] if tracker.state()[0].usable else []
    if request:
        for line in playbook.elevator(
            request,
            workspace.state_root,
            workspace.project.root,
            ledger=Ledger(workspace.state_root),
            catalog=_skill_catalog(),
        ):
            _echo(line)
        return
    event = "session-end" if end else "session-start"
    if not end:
        start_command(fix_it=False)
        _echo("")
    for line in playbook.run_event(
        event,
        workspace.state_root,
        workspace.project.root,
        ledger=Ledger(workspace.state_root),
        open_seeds=open_titles,
    ):
        _echo(line)


@app.command("resume")
def resume_command() -> None:
    """Show the active run's durable continuation state and next action."""
    ledger = _ledger()
    inspected = ledger.inspect_current()
    if inspected.status == "none":
        _echo('NO_RUN  open one first: awino gate open <task-class> "<objective>"')
        raise typer.Exit(2)
    if inspected.status == "broken":
        _echo(f"BROKEN_CURRENT  no run metadata for {inspected.run_id}")
        raise typer.Exit(1)
    run = inspected.run
    if run is None:
        _echo("BROKEN_CURRENT  current run could not be loaded")
        raise typer.Exit(1)
    _echo(f"RUN {run.run_id}  status={inspected.status}  class={run.task_class}")
    _echo(f"objective: {run.objective}")
    if run.issue_id:
        state = "started" if run.issue_started_at else "linked"
        _echo(f"issue: {run.issue_id} ({state})")
    if run.plan_path:
        problems = ledger.validate_plan(run.run_id)
        _echo(f"plan: {'valid' if not problems else 'invalid'}  {run.plan_path}")
        for problem in problems:
            _echo(f"  ! {problem}")
    if not run.checkpoints:
        _echo("checkpoint: none")
        return
    checkpoint = run.checkpoints[-1]
    _echo(f"checkpoint: {checkpoint.checkpoint_id} phase={checkpoint.phase}")
    _echo(f"summary: {checkpoint.summary}")
    if checkpoint.pending_decision and checkpoint.selected_decision is None:
        _echo(f"PENDING  {checkpoint.pending_decision}")
        _echo(f"options: {', '.join(checkpoint.options)}")
    elif checkpoint.selected_decision:
        _echo(f"decision: {checkpoint.selected_decision} by {checkpoint.decided_by}")
    _echo(f"next: {checkpoint.next_action}")


@app.command("start")
def start_command(
    fix_it: bool = typer.Option(
        False, "--fix", help="Perform only mechanical repairs; report the rest"
    ),
) -> None:
    """One command producing the full startup contract: project, mission
    confidence, toolchain, tracker, active run, pending decision, next action,
    and route skill.

    Composes context, mission, doctor --fast, resume, and skill routing rather
    than re-implementing their logic. Read-only unless --fix is passed, and
    even then only mechanical repairs run; anything else is reported, not
    guessed at.
    """
    workspace = _workspace()
    paths = _paths()

    project_line = str(workspace.project.root)

    try:
        tracker = seeds.Seeds(workspace.project.root)
        found = mission.discover(workspace.project.root, tracker=tracker)
        mission_confidence = str(found.confidence)
    except Exception as exc:
        mission_confidence = f"unknown ({exc})"
        tracker = None

    try:
        chain = _toolchain(workspace)
        toolchain_line = ", ".join(sorted(chain.summary())) or "unknown"
    except Exception as exc:
        toolchain_line = f"unknown ({exc})"

    if tracker is not None:
        try:
            state, reason = tracker.state()
            tracker_line = reason if state.usable else f"none ({reason})"
        except Exception as exc:
            tracker_line = f"unknown ({exc})"
    else:
        tracker_line = "unknown"

    ledger = Ledger(workspace.state_root)
    inspected = ledger.inspect_current()
    active_run = inspected.run_id or "none"
    pending_decision = "none"
    next_action = "run 'awino gate open <task-class> \"<objective>\"' to start tracked work"
    if inspected.status == "active" and inspected.run is not None:
        run = inspected.run
        pending = next(
            (
                item
                for item in reversed(run.checkpoints)
                if item.pending_decision is not None and item.selected_decision is None
            ),
            None,
        )
        if pending is not None:
            pending_decision = pending.pending_decision or "none"
        if run.checkpoints:
            next_action = run.checkpoints[-1].next_action

    try:
        health_results = health.run_all(paths, fast=True)
        failing = [r for r in health_results if r.blocking]
    except Exception as exc:
        failing = []
        health_results = []
        _echo(f"NOTE  health check itself failed: {exc}")

    if fix_it:
        try:
            fix.fix_scaffold(paths)
        except Exception as exc:
            _echo(f"NOTE  --fix could not run scaffold repair: {exc}")

        def _ask(question: str) -> bool:
            if not sys.stdin.isatty():
                _echo(f"QUESTION  {question}  (non-interactive: declined; answer via a terminal)")
                return False
            try:
                return typer.confirm(question, default=False)
            except (typer.Abort, EOFError):
                _echo(f"QUESTION  {question}  (no answer: declined)")
                return False

        for action in provision.apply_steps(
            workspace.project.root,
            provision.plan(workspace.project.root, workspace.state_root),
            ask=_ask,
        ):
            _echo(f"{action.outcome:<9} {action.kind.value}  {action.detail}")
    else:
        for step in provision.plan(workspace.project.root, workspace.state_root):
            _echo(f"MISSING  {step.kind.value}: {step.reason} (run 'awino start --fix')")

    route_skill = "direct"
    try:
        catalog = _skill_catalog()
        current = ledger.inspect_current()
        if current.status == "active" and current.run is not None:
            recommendation = catalog.recommend(current.run.objective)
            if recommendation is not None:
                route_skill = recommendation.skill.name
    except Exception:
        pass

    _echo(f"Project: {project_line}")
    _echo(f"Mission confidence: {mission_confidence}")
    _echo(f"Toolchain: {toolchain_line}")
    _echo(f"Tracker: {tracker_line}")
    _echo(f"Active run: {active_run}")
    _echo(f"Pending human decision: {pending_decision}")
    _echo(f"Next recommended action: {next_action}")
    _echo(f"Route skill: {route_skill}")
    objective_for_recall = (
        inspected.run.objective
        if inspected.status == "active" and inspected.run is not None
        else ""
    )
    if not objective_for_recall:
        carried = playbook.load_intent(workspace.state_root)
        objective_for_recall = carried["request"] if carried else ""
    if objective_for_recall:
        seen: set[str] = set()
        for lessons_file in (
            workspace.state_root / "memory" / "lessons.md",
            workspace.home.root / "memory" / "lessons.md",
        ):
            for hit in recall.recall_lessons(lessons_file, objective_for_recall):
                if hit not in seen:
                    seen.add(hit)
                    _echo(f"Recall: {hit[:140]}")
    _echo(f"Stance: {stance.load_default(workspace.project.root)}")
    cat = heilmeier.load(workspace.state_root)
    if not cat.exam_commands():
        _echo(
            "Mission exams: none wired - run 'awino mission --heilmeier' so success is a command, not a sentence"
        )
    else:
        _echo(f"Mission exams: {len(cat.exam_commands())} command(s) wired")

    if failing:
        _echo("")
        _echo(
            f"REFUSED  {len(failing)} health gate(s) failing: {', '.join(r.name for r in failing)}"
        )
        raise typer.Exit(1)


@app.command("exam")
def exam_command(
    keep: bool = typer.Option(
        False, "--keep", help="Keep the disposable fixture repo for inspection"
    ),
    record: bool = typer.Option(
        False, "--record", help="Record the exam as an artifact on the current run"
    ),
) -> None:
    """Put A.W.I.N.O. through every capability it claims, live, in a disposable
    repo, and print FIRES/SILENT per capability. A SILENT line is a regression,
    not an opinion. With --record the result lands in the active run's ledger.
    """
    results = exam.run_exam(keep=keep)
    for line in exam.render(results):
        _echo(line)
    if record:
        ledger = _ledger()
        current = ledger.inspect_current()
        if current.status == "active" and current.run_id:
            ledger.append_artifact(
                current.run_id,
                "exam",
                "awino-exam",
                {r.name: r.fired for r in results},
            )
            _echo(f"RECORDED  exam artifact on {current.run_id}")
        else:
            _echo("NOTE  no active run; exam not recorded")
    if not all(r.fired for r in results):
        raise typer.Exit(1)


def _step_context(
    confirm_budget: bool, answer: str | None, verify: str | None, scope: list[str] | None
) -> stepper.StepContext:
    workspace = _workspace()
    return stepper.StepContext(
        state_root=workspace.state_root,
        project=workspace.project.root,
        home=workspace.home.root,
        paths=_paths(),
        ledger=Ledger(workspace.state_root),
        catalog=_skill_catalog(),
        confirmed_budget=confirm_budget,
        answer=answer,
        verify=verify,
        scope=scope,
    )


@app.command("step")
def step_command(
    request: str = typer.Argument(
        None, help="Start a new trip from these words; omit to advance the current one"
    ),
    confirm_budget: bool = typer.Option(False, "--confirm-budget"),
    answer: str = typer.Option(None, "--answer", help="Answer the pending QUESTION or STOP"),
    verify: str = typer.Option(None, "--verify"),
    scope: list[str] = typer.Option(None, "--scope"),
    reset: bool = typer.Option(False, "--reset", help="Forget the current trip"),
) -> None:
    """Advance the machine by exactly one node.

    Reads the persisted node, performs that node's single action, records the
    observation, follows the one edge, stops. Nothing is remembered between
    calls except what is on disk, so a fresh context resumes exactly here.
    Repeat until it prints DONE or WAITING.
    """
    ctx = _step_context(confirm_budget, answer, verify, scope)
    if reset:
        from smith import machine as _m

        _m.reset(ctx.state_root)
        _echo("RESET  machine idle")
        return
    _machine, lines = stepper.step(ctx, request)
    for line in lines:
        _echo(line)
