"""owns: loop run rpi, loop run ralph, loop run delegate, loop next, loop status, loop approve, loop back, loop answer, loop default

The loop CLI: the machine drives phases, the model thinks inside them.
Every command here is deterministic -- the model calls these rather than
tracking phase state in prose, which is the ``MODEL_DOES_DETERMINISM`` guard
applied to the loop drivers.

Machine-readable lines (LOOP, PHASE, ARTIFACT, ADVANCED, VALIDATION_FAILED,
ESCALATED, REFUSED, HANDOFF, APPROVED, REENTERED, ...) are stable: narration
is additive around them, never a replacement.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from smith import loops
from smith.cli import _echo, _ledger, _workspace
from smith.paths import SmithPaths

loop_app = typer.Typer(
    no_args_is_help=True,
    help="Loop drivers: rpi (research-pair-plan-plan-implement), "
    "ralph (attempt-verify-retry), delegate (decompose-assign-execute-verify).",
)
loop_run_app = typer.Typer(no_args_is_help=True, help="Start a loop of the given kind.")
loop_app.add_typer(loop_run_app, name="run")

_LOOP_KINDS = ("rpi", "ralph", "delegate")


def _loops_dir() -> Path:
    return _workspace().state_root / "loops"


def _skill_md_for(kind: str) -> Path:
    skills = SmithPaths.discover().skills
    return {
        "rpi": skills / "awino-rpi" / "SKILL.md",
        "ralph": skills / "awino-ralph" / "SKILL.md",
        "delegate": skills / "awino-delegate" / "SKILL.md",
    }[kind]


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


def _driver_for_kind(kind: str) -> loops.LoopDriver:
    """Build the driver for a loop kind. RPI gets the open-run handoff hook;
    all kinds share the loops dir, the ledger, and their skill document."""
    if kind not in _LOOP_KINDS:
        raise loops.LoopError(f"unknown loop kind {kind!r}: expected one of {', '.join(_LOOP_KINDS)}")
    workspace = _workspace()
    common = {
        "project_root": workspace.project.root,
        "loops_dir": _loops_dir(),
        "skill_md": _skill_md_for(kind),
        "ledger": _ledger(),
    }
    if kind == "rpi":
        return loops.RpiDriver(open_rpi_run=_open_rpi_run, **common)
    if kind == "ralph":
        return loops.RalphDriver(**common)
    return loops.DelegateDriver(**common)


def _resolve_driver_and_state(
    loop_id: str | None,
) -> tuple[loops.LoopDriver, loops.LoopState]:
    """Resolve a loop id (or the current one) to its driver and state.

    The driver kind comes from the persisted loop id's prefix, so `next`,
    `status`, and `back` work for any loop kind without a --kind flag.
    """
    probe = _driver_for_kind("rpi")  # kinds share the loops dir; any probe reads current
    resolved = loop_id or probe.current_id()
    if not resolved:
        _echo("NO_LOOP  create one first: awino loop run rpi --task \"...\"")
        raise typer.Exit(2)
    try:
        kind = loops.kind_of(resolved)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    driver = _driver_for_kind(kind)
    try:
        state = driver.load(resolved)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    return driver, state


def _print_phase_start(driver: loops.LoopDriver, state: loops.LoopState) -> None:
    """Print the PHASE/ARTIFACT machine lines, then additive narration
    (purpose, human role, check why), then the skill's prompt block."""
    phase = next(p for p in driver.phases() if p.name == state.phase)
    _echo(f"PHASE  {state.phase}")
    artifact = driver.artifact_path(state)
    if artifact:
        _echo(f"ARTIFACT  {artifact}")
    else:
        _echo(f"ARTIFACT  {driver.no_artifact_note}")
    blurb = driver.phase_blurbs.get(state.phase)
    if blurb:
        _echo(f"PURPOSE  {blurb}")
    role = driver.human_roles.get(state.phase)
    if role:
        _echo(f"YOU  {role}")
    why = driver.check_whys.get(state.phase)
    if why:
        _echo(f"CHECK  {why}")
    _echo("")
    _echo(phase.prompt_block(driver))
    if isinstance(driver, loops.RpiDriver) and state.phase == "pair-plan":
        for line in driver.describe_pairing(state):
            _echo(line)


def _print_run_opened(
    driver: loops.LoopDriver, state: loops.LoopState, first_phase: str
) -> None:
    phase = next(p for p in driver.phases() if p.name == first_phase)
    _echo(f"LOOP  {state.id}")
    _echo(f"task: {state.task}")
    _echo(f"phase: {first_phase}")
    artifact = driver.artifact_path(state)
    if artifact:
        _echo(f"ARTIFACT  {artifact}")
    else:
        _echo(f"ARTIFACT  {driver.no_artifact_note}")
    if state.seed_id:
        _echo(f"seed: {state.seed_id} (stays open until the loop's own completion)")
    purpose = driver.loop_purpose
    if purpose:
        _echo(f"PURPOSE  {purpose}")
    role = driver.human_roles.get(first_phase)
    if role:
        _echo(f"YOU  {role}")
    _echo("")
    _echo(phase.prompt_block(driver))


@loop_run_app.command("rpi")
def loop_run_rpi(
    task: str = typer.Option(..., "--task", help="One sentence describing the change"),
    topic: str = typer.Option(None, "--topic", help="Slug for the artifact filenames"),
    seed: str = typer.Option(None, "--seed", help="Seed ID to link; the loop never closes it itself"),
) -> None:
    """Start an RPI loop and print the phase-1 (research) prompt block."""
    driver = _driver_for_kind("rpi")
    try:
        state = driver.new(task, topic=topic, seed_id=seed)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _print_run_opened(driver, state, "research")


@loop_run_app.command("ralph")
def loop_run_ralph(
    task: str = typer.Option(..., "--task", help="One sentence describing the task"),
    topic: str = typer.Option(None, "--topic", help="Slug for the artifact filenames"),
    seed: str = typer.Option(None, "--seed", help="Seed ID to link; closed only on verified success"),
    check: str = typer.Option(..., "--check", help="Verification command; exit 0 means done"),
) -> None:
    """Start a Ralph loop and print the attempt prompt block."""
    driver = _driver_for_kind("ralph")
    try:
        state = driver.new(task, topic=topic, seed_id=seed, check=check)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _print_run_opened(driver, state, "attempt")


@loop_run_app.command("delegate")
def loop_run_delegate(
    task: str = typer.Option(..., "--task", help="One sentence describing the work to split"),
    topic: str = typer.Option(None, "--topic", help="Slug for the artifact filenames"),
    seed: str = typer.Option(None, "--seed", help="Seed ID to link; closed only on verified success"),
) -> None:
    """Start a Delegate loop and print the decompose prompt block."""
    driver = _driver_for_kind("delegate")
    try:
        state = driver.new(task, topic=topic, seed_id=seed)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _print_run_opened(driver, state, "decompose")


@loop_app.command("next")
def loop_next(
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Validate the current phase artifact; advance, or print exactly what is missing.

    Ralph's verify phase runs its check command here: a pass completes the
    loop, a failure routes to retry, and the third failure escalates.
    """
    driver, state = _resolve_driver_and_state(loop_id)
    if state.locked:
        _echo(
            f"REFUSED  LOOP_LOCKED  loop {state.id} is locked; "
            f"{driver.lock_next(state)}"
        )
        raise typer.Exit(1)
    if state.phase == "done":
        _echo(f"REFUSED  loop is already done; {driver.done_note}")
        raise typer.Exit(1)

    if driver.check_before_advance(state):
        missing = driver.check(state)
        if missing:
            attempts = state.attempts.get(state.phase, 0)
            if state.locked:
                _echo(
                    f"ESCALATED  phase '{state.phase}' failed validation "
                    f"{loops.MAX_ATTEMPTS} times; the loop is locked"
                )
                _echo(driver.lock_next(state))
                raise typer.Exit(1)
            _echo(f"VALIDATION_FAILED  phase={state.phase}  attempt={attempts}/{loops.MAX_ATTEMPTS}")
            for item in missing:
                _echo(f"  - {item}")
            _echo(f"Fix the artifact, then rerun `awino loop next --id {state.id}`.")
            raise typer.Exit(1)

    try:
        new_phase = driver.advance(state)
    except loops.ApprovalRequired as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    except loops.PairingIncomplete as exc:
        _echo(f"REFUSED  {exc}")
        if isinstance(driver, loops.RpiDriver):
            for qid, question in driver.pairing_questions(state):
                if qid in exc.unanswered:
                    _echo(f"  {qid}: {question}")
            _echo(
                f"Answer with: awino loop answer --question {exc.unanswered[0]} "
                f"--answer \"...\" --id {state.id}"
            )
        raise typer.Exit(1) from None
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None

    if new_phase == "done":
        for line in driver.completion_lines(state):
            _echo(line)
        return
    _echo(f"ADVANCED  phase={new_phase}")
    _print_phase_start(driver, state)


@loop_app.command("status")
def loop_status(
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Show loop id, kind, phase, attempts, human role, and the concrete next action."""
    driver, state = _resolve_driver_and_state(loop_id)
    _echo(f"LOOP  {state.id}")
    _echo(f"kind: {driver.loop_kind}")
    _echo(f"task: {state.task}")
    _echo(f"phase: {state.phase}")
    attempts = " ".join(f"{name}={state.attempts.get(name, 0)}" for name in driver.phase_order)
    _echo(f"attempts: {attempts}")
    _echo(driver.approval_line(state))
    if isinstance(driver, loops.RpiDriver) and state.phase == "pair-plan":
        for line in driver.describe_pairing(state):
            _echo(line)
    if state.seed_id:
        _echo(f"seed: {state.seed_id}")
    _echo(f"locked: {'yes' if state.locked else 'no'}")
    if state.gate_run_id:
        _echo(f"gate_run: {state.gate_run_id}")
    role = driver.human_roles.get(state.phase)
    if role and state.phase != "done":
        _echo(f"role: {role}")
    _echo(f"next: {driver.status_next(state)}")


@loop_app.command("back")
def loop_back(
    phase: str = typer.Argument(..., help="Earlier phase to re-enter"),
    reason: str = typer.Option("", "--reason", help="Why the phase is being re-entered"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Re-enter an earlier phase: it becomes current with a fresh attempt count.

    The phase's artifact file is kept but must re-validate on the next
    `awino loop next`. The phase's prompt block prints again so the model can
    redo the thinking. Valid targets depend on the loop kind; use
    `awino loop status` to see the phase order.
    """
    driver, state = _resolve_driver_and_state(loop_id)
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
    """Record human approval of the plan. Required before implement starts (RPI)."""
    driver, state = _resolve_driver_and_state(loop_id)
    if state.locked:
        _echo(f"REFUSED  LOOP_LOCKED  loop {state.id} is locked; a human must intervene")
        raise typer.Exit(1)
    if state.phase != "plan":
        _echo(
            f"REFUSED  nothing to approve: current phase is '{state.phase}'; "
            "approval is only meaningful between plan and implement"
        )
        raise typer.Exit(1)
    if not isinstance(driver, loops.RpiDriver):
        _echo(f"REFUSED  loop {state.id} is not an RPI loop; only RPI plans need approval")
        raise typer.Exit(1)
    driver.approve_plan(state, by, reason)
    _echo(f"APPROVED  plan by={by}")
    if reason:
        _echo(f"reason: {reason}")
    _echo(f"Advance with: awino loop next --id {state.id}")


@loop_app.command("answer")
def loop_answer(
    question: str = typer.Option(..., "--question", help="Pairing question id, e.g. Q1"),
    answer: str = typer.Option(..., "--answer", help="The human's answer, recorded verbatim"),
    by: str = typer.Option("human", "--by", help="Who answered"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Record a human answer to a pair-planning question (RPI)."""
    driver, state = _resolve_driver_and_state(loop_id)
    if not isinstance(driver, loops.RpiDriver):
        _echo(f"REFUSED  loop {state.id} is not an RPI loop; answers only apply to pair-planning")
        raise typer.Exit(1)
    try:
        driver.record_pair_answer(state, question, "answer", answer, by=by)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"ANSWERED  {question} by={by}")
    remaining = driver.unanswered_questions(state)
    if remaining:
        _echo(f"REMAINING  {', '.join(remaining)}")
        _echo(
            f"Next: awino loop answer --question {remaining[0]} "
            f"--answer \"...\" --id {state.id}"
        )
    else:
        _echo(f"All questions answered. Advance with: awino loop next --id {state.id}")


@loop_app.command("default")
def loop_default(
    question: str = typer.Option(..., "--question", help="Pairing question id, e.g. Q1"),
    reason: str = typer.Option(..., "--reason", help="Why this default stands in for an answer"),
    by: str = typer.Option("human", "--by", help="Who declared the default"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Declare a default for a pair-planning question instead of answering it (RPI)."""
    driver, state = _resolve_driver_and_state(loop_id)
    if not isinstance(driver, loops.RpiDriver):
        _echo(f"REFUSED  loop {state.id} is not an RPI loop; defaults only apply to pair-planning")
        raise typer.Exit(1)
    try:
        driver.record_pair_answer(state, question, "default", reason, by=by)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"DEFAULTED  {question} by={by}")
    remaining = driver.unanswered_questions(state)
    if remaining:
        _echo(f"REMAINING  {', '.join(remaining)}")
        _echo(
            f"Next: awino loop answer --question {remaining[0]} "
            f"--answer \"...\" --id {state.id}"
        )
    else:
        _echo(f"All questions answered. Advance with: awino loop next --id {state.id}")
