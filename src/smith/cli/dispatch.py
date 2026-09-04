"""owns: dispatch, floor open, floor close, auto, gate graph, gate loop

Bounded delegation surfaces: the dispatch trip, portable floors, the seeds-driven
auto loop, the worker-reviewer graph, and the retry loop. None of these close a
run; ``gate close`` remains the completion authority.
"""

from __future__ import annotations

import typer

from smith import (
    auto,
    dispatch,
    graph,
    loop,
    provision,
    seeds,
    skill_catalog,
    spawn,
)
from smith.cli import (
    _echo,
    _ledger,
    _resolve_run,
    _skill_catalog,
    _workspace,
    app,
    floor_app,
    gate_app,
)
from smith.enforce import (
    MAX_ATTEMPTS,
    Gate,
    Ledger,
)


@gate_app.command("loop")
def gate_loop(
    gate: Gate = typer.Argument(..., help="Which gate this evidence satisfies"),
    cmd: str = typer.Option(..., "--cmd", help="Command to execute, automatically retried"),
    max_iterations: int = typer.Option(3, "--max-iterations", help="Never exceeds THREE_STRIKES"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Automatically re-invoke --cmd until it passes or the budget is spent.

    For a genuinely non-deterministic command (a flaky test, a resource
    that needs a moment to become ready) where retrying the SAME command
    can plausibly succeed. This is not the tool for "the fix needs to
    change between attempts" - that decision belongs to whoever is fixing
    the problem, recorded via ordinary 'gate record' calls between edits.
    Never exceeds the ledger's own THREE_STRIKES cap regardless of
    --max-iterations.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    capped = min(max_iterations, MAX_ATTEMPTS)
    result = loop.run_loop(
        ledger, resolved, gate, lambda _n: cmd, max_iterations=capped, cwd=_workspace().project.root
    )
    for item in result.iterations:
        mark = "PASS" if item.passed else "FAIL"
        _echo(f"  [{item.number}] {mark}  exit={item.exit_code}")
    _echo(f"{result.outcome.value.upper()}  {result.reason}")
    if result.outcome is not loop.LoopOutcome.SHIPPED:
        raise typer.Exit(1)


@gate_app.command("graph")
def gate_graph(
    task: str = typer.Option(..., "--task", help="Task for each fresh worker invocation"),
    verify: str = typer.Option(None, "--verify", help="Cross-platform worker verification command"),
    scope: list[str] = typer.Option(None, "--scope", help="Worker writable file; repeatable"),
    runner: str = typer.Option(None, "--runner", help="claude, goose, or codex"),
    max_rounds: int = typer.Option(MAX_ATTEMPTS, "--max-rounds"),
    confirm_budget: bool = typer.Option(
        False,
        "--confirm-budget",
        help="Explicitly approve up to max-rounds worker and reviewer subprocess pairs",
    ),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Run the bounded independent worker-reviewer graph without closing the run."""
    if not confirm_budget:
        _echo("REFUSED  pass --confirm-budget to approve subprocess cost")
        raise typer.Exit(2)
    if not verify:
        _echo("REFUSED  --verify is required")
        raise typer.Exit(2)
    if not scope:
        _echo("REFUSED  at least one --scope is required for the worker")
        raise typer.Exit(2)
    if not 1 <= max_rounds <= MAX_ATTEMPTS:
        _echo(f"REFUSED  max-rounds must be between 1 and {MAX_ATTEMPTS}")
        raise typer.Exit(2)
    _echo(f"BUDGET_CONFIRMED  pairs={max_rounds}  subprocesses<={max_rounds * 2}")
    resolved = _resolve_run(run_id)
    chosen, reason = spawn.detect_runner(runner)
    _echo(f"runner: {chosen} ({reason})")
    workspace = _workspace()
    result = graph.run_worker_reviewer_graph(
        _ledger(),
        resolved,
        task,
        workspace.home.root,
        workspace.project.root,
        chosen,
        file_scope=scope,
        worker_verification=verify,
        confirmed_budget=True,
        max_rounds=max_rounds,
    )
    for item in result.rounds:
        _echo(f"  round={item.number} route={item.route.value} feedback={item.feedback or '-'}")
    _echo(f"{result.outcome.value.upper()}  {result.reason}")
    _echo("NOTE  graph acceptance does not close the run; gate close remains authoritative")
    if result.outcome is not graph.GraphOutcome.SHIP:
        raise typer.Exit(1)


@app.command("dispatch")
def dispatch_command(
    request: str = typer.Argument(..., help="Plain-language description of what needs to happen"),
    confirm_budget: bool = typer.Option(
        False, "--confirm-budget", help="Explicitly approve up to max-floors subprocess spawns"
    ),
    max_floors: int = typer.Option(MAX_ATTEMPTS, "--max-floors"),
    runner: str = typer.Option(None, "--runner", help="claude, goose, or codex"),
    verify: str = typer.Option(None, "--verify", help="Cross-platform verification command"),
    scope: list[str] = typer.Option(None, "--scope", help="Writable file; repeatable"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the routing decision and preflight verdict; spawn nothing"
    ),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Match a plain-language request to a skill, dispatch it, wait, independently
    verify the result, route to remediation or completion, and record the trip.

    This never closes a gate run; 'gate close' remains the completion authority.
    """
    workspace = _workspace()
    # Read-only until budget is confirmed: _ledger() would call ensure_state()
    # and write .smith/.gitignore even under --dry-run.
    ledger = Ledger(workspace.state_root)
    if not 1 <= max_floors <= MAX_ATTEMPTS:
        _echo(f"REFUSED  max-floors must be between 1 and {MAX_ATTEMPTS}")
        raise typer.Exit(2)
    catalog = skill_catalog.SkillCatalog(
        workspace.project.root, workspace.home.root / "skills", workspace.home.root / "skills"
    )

    decision = dispatch.decide(request, catalog)
    _echo(f"REQUEST  {request}")
    _echo(f"CONFIDENCE  {decision.confidence}")
    if decision.skill is not None:
        _echo(f"MATCHED  {decision.skill.name}")
    if decision.question is not None:
        _echo(f"QUESTION  {decision.question}")

    pre = dispatch.preflight(ledger, workspace.home)
    _echo(f"PREFLIGHT  {'ok' if pre.ok else 'blocked'}: {pre.detail}")

    if dry_run:
        _echo("DRY_RUN  no subprocess was spawned")
        if decision.confidence != "high" or not pre.ok:
            raise typer.Exit(1)
        return

    if not confirm_budget:
        _echo("REFUSED  pass --confirm-budget to approve subprocess cost")
        raise typer.Exit(2)
    chosen, reason = spawn.detect_runner(runner)
    _echo(f"runner: {chosen} ({reason})")
    if not chosen.enforces_read_only:
        _echo(f"REFUSED  {chosen} cannot mechanically enforce read-only dispatch review")
        raise typer.Exit(1)
    _echo(f"BUDGET_CONFIRMED  floors={max_floors}  subprocesses<={max_floors}")
    if not verify:
        discovered = provision.discover_verification(workspace.project.root)
        if discovered is None:
            _echo('REFUSED  no verification command found; pass --verify "<real check>"')
            raise typer.Exit(2)
        verify, source = discovered
        _echo(f"VERIFY  {verify} (from {source})")

    # Budget is confirmed, so writing project state is now legitimate.
    ledger = _ledger()
    resolved = _resolve_run(run_id)
    result = dispatch.run_dispatch(
        ledger,
        resolved,
        request,
        catalog,
        workspace.home,
        workspace.home.root,
        workspace.project.root,
        chosen,
        verify,
        file_scope=scope or [],
        confirmed_budget=True,
        max_floors=max_floors,
    )
    for floor in result.floors:
        _echo(f"  floor={floor.number} skill={floor.skill} verified={floor.verified}")
    _echo(f"{result.outcome.value.upper()}  {result.reason}")
    _echo("NOTE  dispatch acceptance does not close the run; gate close remains authoritative")
    if result.outcome is not dispatch.DispatchOutcome.COMPLETE:
        raise typer.Exit(1)


@app.command("auto")
def auto_command(
    max_seeds: int = typer.Option(..., "--max-seeds", help="Hard cap on Seeds this sitting"),
    confirm_budget: bool = typer.Option(False, "--confirm-budget"),
    verify: str = typer.Option(
        None, "--verify", help="Verification command; discovered when omitted"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show which Seeds would run; touch nothing"
    ),
) -> None:
    """Drive ready Seeds through dispatch floors until done, blocked, or out of
    budget. One command, bounded loop, no scheduler: a human starts every
    sitting. Stops on the first Seed that does not verify - a stuck Seed is a
    human decision, not something to skip past. Never pushes.

    The worker for each floor is whatever agent environment runs this command's
    printed prompts; in an interactive session, execute each floor prompt and
    rerun. For a fully hands-off sitting inside an agent session, the agent
    plays worker between floor open and close.
    """
    workspace = _workspace()
    tracker = seeds.Seeds(workspace.project.root)
    ready = [i for i in tracker.ready() if i.status == "open"]
    _echo(f"READY  {len(ready)} seed(s)")
    for issue in ready[:max_seeds]:
        _echo(f"  {issue.id}  [{issue.type}] {issue.title[:70]}")
    if dry_run:
        _echo("DRY_RUN  nothing was started")
        return
    if not confirm_budget:
        _echo("REFUSED  pass --confirm-budget to approve the sitting")
        raise typer.Exit(2)
    if not verify:
        discovered = provision.discover_verification(workspace.project.root)
        if discovered is None:
            _echo('REFUSED  no verification command found; pass --verify "<real check>"')
            raise typer.Exit(2)
        verify, source = discovered
        _echo(f"VERIFY  {verify} (from {source})")

    def worker(state) -> None:
        _echo(f"FLOOR  {state.floor}/{state.max_floors}  skill={state.skill}")
        _echo(f"PROMPT  {state.prompt_path}")
        _echo("Execute the prompt (this session or any agent), then press Enter to verify...")
        try:
            input()
        except EOFError:
            _echo("(non-interactive: proceeding straight to verification)")

    result = auto.run_auto(
        _ledger(),
        tracker,
        _skill_catalog(),
        workspace.home.root,
        workspace.project.root,
        worker,
        verify,
        max_seeds=max_seeds,
        confirmed_budget=True,
    )
    for seed_result in result.seeds:
        _echo(f"{seed_result.outcome.upper():<8} {seed_result.issue_id}  {seed_result.detail[:90]}")
    _echo(f"STOPPED  {result.stopped_because}")
    if any(s.outcome != "closed" for s in result.seeds):
        raise typer.Exit(1)


@floor_app.command("open")
def floor_open(
    request: str = typer.Argument(..., help="Plain-language description of what needs to happen"),
    verify: str = typer.Option(
        None,
        "--verify",
        help="Real verification command; discovered from justfile/Makefile/pytest when omitted",
    ),
    scope: list[str] = typer.Option(..., "--scope", help="Writable file; repeatable"),
    max_floors: int = typer.Option(MAX_ATTEMPTS, "--max-floors"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Route the request and write a floor prompt for whatever agent is present
    to execute - this session, Claude Code, Cline, or a human. No external agent
    CLI or login is needed; the harness that is already running does the work,
    then calls 'awino floor close', which re-runs verification itself.
    """
    workspace = _workspace()
    if not verify:
        discovered = provision.discover_verification(workspace.project.root)
        if discovered is None:
            _echo('REFUSED  no verification command found; pass --verify "<real check>"')
            raise typer.Exit(2)
        verify, source = discovered
        _echo(f"VERIFY  {verify} (from {source})")
    ledger = _ledger()
    resolved = _resolve_run(run_id)
    catalog = _skill_catalog()
    try:
        state = dispatch.open_floor(
            ledger,
            resolved,
            request,
            catalog,
            workspace.home.root,
            verify,
            file_scope=scope,
            max_floors=max_floors,
        )
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from exc
    _echo(f"FLOOR_OPEN  floor={state.floor}/{state.max_floors}  skill={state.skill}")
    _echo(f"INVOCATION  {state.invocation_id}")
    _echo(f"PROMPT  {state.prompt_path}")
    _echo("Execute the prompt in this or any agent environment, then run: awino floor close")


@floor_app.command("close")
def floor_close(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Independently verify the pending floor's work and route the trip.

    The closer runs the verification command itself; a worker's completion claim
    is never sufficient, regardless of which environment did the work.
    """
    workspace = _workspace()
    ledger = _ledger()
    resolved = _resolve_run(run_id)
    try:
        result = dispatch.close_floor(ledger, resolved, workspace.project.root)
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from exc
    _echo(f"{result.outcome.value.upper()}  {result.detail}")
    if result.next_state is not None:
        _echo(
            f"FLOOR_OPEN  floor={result.next_state.floor}/{result.next_state.max_floors}"
            f"  skill={result.next_state.skill}"
        )
        _echo(f"PROMPT  {result.next_state.prompt_path}")
    _echo("NOTE  floor completion does not close the run; gate close remains authoritative")
    if result.outcome is not dispatch.DispatchOutcome.COMPLETE:
        raise typer.Exit(1)
