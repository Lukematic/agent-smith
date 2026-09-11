"""owns: loop run rpi, loop next, loop status, loop approve, loop back

The RPI loop CLI: the machine drives phases, the model thinks inside them.
Every command here is deterministic -- the model calls these rather than
tracking phase state in prose, which is the ``MODEL_DOES_DETERMINISM`` guard
applied to the RPI loop.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from smith import loops
from smith.cli import _echo, _ledger, _workspace
from smith.paths import SmithPaths

loop_app = typer.Typer(no_args_is_help=True, help="RPI loop driver: research -> plan -> implement.")
loop_run_app = typer.Typer(no_args_is_help=True, help="Start a loop of the given kind.")
loop_app.add_typer(loop_run_app, name="run")


def _loops_dir() -> Path:
    return _workspace().state_root / "loops"


def _skill_md() -> Path:
    return SmithPaths.discover().skills / "awino-rpi" / "SKILL.md"


def _open_rpi_run() -> str | None:
    """Id of an open ledger run tagged --loop rpi, or None.

    Reads the ledger's on-disk layout through its public ``base`` path rather
    than reimplementing close: this is the handoff point the implement phase
    checks, nothing more.
    """
    ledger = _ledger()
    if not ledger.base.is_dir():
        return None
    for run_dir in sorted(ledger.base.iterdir()):
        if not run_dir.is_dir():
            continue
        meta = run_dir / "run.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("loop") == "rpi" and data.get("terminal_state") is None:
            return run_dir.name
    return None


def _driver() -> loops.RpiDriver:
    workspace = _workspace()
    return loops.RpiDriver(
        project_root=workspace.project.root,
        loops_dir=_loops_dir(),
        skill_md=_skill_md(),
        open_rpi_run=_open_rpi_run,
        ledger=_ledger(),
    )


def _resolve_loop(driver: loops.RpiDriver, loop_id: str | None) -> loops.LoopState:
    resolved = loop_id or driver.current_id()
    if not resolved:
        _echo("NO_LOOP  create one first: awino loop run rpi --task \"...\"")
        raise typer.Exit(2)
    try:
        return driver.load(resolved)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None


def _print_phase_start(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    phase = next(p for p in driver.phases() if p.name == state.phase)
    _echo(f"PHASE  {state.phase}")
    if state.phase == "research":
        _echo(f"ARTIFACT  {state.research_artifact}")
    elif state.phase == "plan":
        _echo(f"ARTIFACT  {state.plan_artifact}")
    else:
        # Implement has no loop artifact of its own: the model does the
        # thinking in the gate ledger's run, and the driver only checks the
        # handoff point. Say so rather than pointing at a file.
        _echo("ARTIFACT  (none -- this phase hands off to the gate ledger)")
    _echo("")
    _echo(phase.prompt_block(driver))


@loop_run_app.command("rpi")
def loop_run_rpi(
    task: str = typer.Option(..., "--task", help="One sentence describing the change"),
    topic: str = typer.Option(None, "--topic", help="Slug for the artifact filenames"),
) -> None:
    """Start an RPI loop and print the phase-1 (research) prompt block."""
    driver = _driver()
    try:
        state = driver.new(task, topic=topic)
        prompt = next(p for p in driver.phases() if p.name == "research").prompt_block(driver)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"LOOP  {state.id}")
    _echo(f"task: {state.task}")
    _echo("phase: research")
    _echo(f"ARTIFACT  {state.research_artifact}")
    _echo("")
    _echo(prompt)


@loop_app.command("next")
def loop_next(
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Validate the current phase artifact; advance, or print exactly what is missing."""
    driver = _driver()
    state = _resolve_loop(driver, loop_id)
    if state.locked:
        _echo(
            f"REFUSED  LOOP_LOCKED  loop {state.id} is locked after "
            f"{loops.MAX_ATTEMPTS} failed validations of phase '{state.phase}'"
        )
        _echo("A human must intervene: fix the artifact by hand or start a new loop.")
        raise typer.Exit(1)
    if state.phase == "done":
        _echo("REFUSED  loop is already done; the gate ledger owns what remains")
        raise typer.Exit(1)

    missing = driver.check(state)
    if missing:
        attempts = state.attempts.get(state.phase, 0)
        if state.locked:
            _echo(
                f"ESCALATED  phase '{state.phase}' failed validation "
                f"{loops.MAX_ATTEMPTS} times; the loop is locked"
            )
            _echo("A human must intervene: fix the artifact by hand or start a new loop.")
            raise typer.Exit(1)
        _echo(f"VALIDATION_FAILED  phase={state.phase}  attempt={attempts}/{loops.MAX_ATTEMPTS}")
        for item in missing:
            _echo(f"  - {item}")
        _echo("Fix the artifact, then rerun `awino loop next`.")
        raise typer.Exit(1)

    try:
        new_phase = driver.advance(state)
    except loops.ApprovalRequired as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None

    if new_phase == "done":
        _echo(f"HANDOFF  gate run {state.gate_run_id} now owns completion")
        _echo(f"Close the work with: awino gate close --run {state.gate_run_id}")
        return
    _echo(f"ADVANCED  phase={new_phase}")
    _print_phase_start(driver, state)


@loop_app.command("status")
def loop_status(
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Show loop id, current phase, attempts, and approval state."""
    driver = _driver()
    state = _resolve_loop(driver, loop_id)
    _echo(f"LOOP  {state.id}")
    _echo(f"task: {state.task}")
    _echo(f"phase: {state.phase}")
    attempts = " ".join(f"{name}={state.attempts.get(name, 0)}" for name in loops.PHASE_ORDER)
    _echo(f"attempts: {attempts}")
    latest = state.approvals[-1] if state.approvals else None
    if latest:
        _echo(
            f"approval: approved by={latest['by']} at={latest['at']} "
            f"reason={latest.get('reason') or '(none)'}"
        )
    else:
        _echo("approval: pending (required between plan and implement)")
    _echo(f"locked: {'yes' if state.locked else 'no'}")
    if state.gate_run_id:
        _echo(f"gate_run: {state.gate_run_id}")


@loop_app.command("back")
def loop_back(
    phase: str = typer.Argument(..., help="Earlier phase to re-enter: research or plan"),
    reason: str = typer.Option("", "--reason", help="Why the phase is being re-entered"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Re-enter an earlier phase: it becomes current with a fresh attempt count.

    The phase's artifact file is kept but must re-validate on the next
    `awino loop next`. The phase's prompt block prints again so the model can
    redo the thinking.
    """
    driver = _driver()
    state = _resolve_loop(driver, loop_id)
    was_locked = state.locked
    try:
        driver.reenter_phase(state, phase, reason)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"REENTERED  phase={state.phase}")
    _echo(
        f"attempts for '{state.phase}' reset to 0: "
        "three-strikes gets a fresh count on re-entry"
    )
    if was_locked:
        _echo("UNLOCKED  re-entry is a human intervention; the lock is cleared")
    if reason.strip():
        _echo(f"reason: {reason.strip()}")
    _print_phase_start(driver, state)


@loop_app.command("approve")
def loop_approve(
    by: str = typer.Option(..., "--by", help="Person approving the plan"),
    reason: str = typer.Option("", "--reason", help="Why the plan is approved"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Record human approval of the plan. Required before implement starts."""
    driver = _driver()
    state = _resolve_loop(driver, loop_id)
    if state.locked:
        _echo(f"REFUSED  LOOP_LOCKED  loop {state.id} is locked; a human must intervene")
        raise typer.Exit(1)
    if state.phase != "plan":
        _echo(
            f"REFUSED  nothing to approve: current phase is '{state.phase}'; "
            "approval is only meaningful between plan and implement"
        )
        raise typer.Exit(1)
    driver.approve_plan(state, by, reason)
    _echo(f"APPROVED  plan by={by}")
    if reason:
        _echo(f"reason: {reason}")
    _echo("Advance with: awino loop next")
