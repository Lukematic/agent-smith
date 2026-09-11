"""owns: loop run rpi, loop run ralph, loop run delegate, loop next, loop status, loop approve, loop back, loop answer, loop default, loop close, loop think, loop explain, loop probe-answer, loop suggest, loop suggest-answer, loop confirm-problem

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
from datetime import UTC, datetime
from pathlib import Path

import typer

from awino import heilmeier, loops, onboarding, think, working_memory
from awino.cli import _echo, _ledger, _workspace
from awino.enforce import LoopEvent
from awino.paths import AwinoPaths
from awino.seeds import Seeds

loop_app = typer.Typer(
    no_args_is_help=True,
    help="Loop drivers: rpi (research-pair-plan-plan-implement), "
    "ralph (attempt-verify-retry), delegate (decompose-assign-execute-verify).",
)
loop_run_app = typer.Typer(no_args_is_help=True, help="Start a loop of the given kind.")
loop_app.add_typer(loop_run_app, name="run")

_LOOP_KINDS = ("rpi", "ralph", "delegate")


def _challenge_gate_or_refuse(skip_challenge: bool, skip_reason: str) -> None:
    """The challenge gate: no loop starts on an unchallenged plan.

    Ralph and Delegate have no plan-approval step, so the gate lives here,
    at loop creation: at least one challenge-mode think
    (devil/blindspot/premortem/uncomfortable/assumption-destroyer) must be
    recorded in the project's working memory, or the human explicitly skips
    with a reason. Mirrors RPI's --waive-thinking/--waive-reason: the skip
    is a conscious, stated decision, never a silent default.
    """
    if skip_challenge and not skip_reason.strip():
        _echo(
            "REFUSED  --skip-challenge requires --skip-reason <text>: "
            "a skip without a reason is not a conscious decision"
        )
        raise typer.Exit(2)
    if skip_challenge:
        _echo(f"CHALLENGE_SKIPPED  reason: {skip_reason.strip()}")
        return
    mode = think.challenge_recorded(_workspace().state_root)
    if mode is None:
        _echo(
            "REFUSED  no challenge-mode thinking recorded in this project: "
            "a plan that was never challenged is a guess with a checklist. "
            "Run one first, e.g. `awino think devil --record <file>` "
            "(devil, blindspot, premortem, uncomfortable, or "
            "assumption-destroyer), or skip consciously with "
            "`--skip-challenge --skip-reason \"...\"`."
        )
        raise typer.Exit(1)
    _echo(f"CHALLENGE  satisfied by recorded thinking: {mode}")


def _loops_dir() -> Path:
    return _workspace().state_root / "loops"


def _skill_md_for(kind: str) -> Path:
    skills = AwinoPaths.discover().skills
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
        # Working memory: the checklist and decisions live in project state.
        "state_root": workspace.state_root,
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

    Missing state is never silently treated as a fresh project: when the
    project was onboarded but its loop state is gone (deleted state
    directory, wiped loops dir), the refusal says so and names the
    recovery command instead of suggesting `loop run` as if nothing had
    happened.
    """
    probe = _driver_for_kind("rpi")  # kinds share the loops dir; any probe reads current
    resolved = loop_id or probe.current_id()
    if not resolved:
        if onboarding.path_for(probe.project_root).is_file():
            _echo(
                "REFUSED  this project was onboarded but its loop state is "
                "gone (no current loop found) -- the state directory may "
                "have been deleted; re-run `awino onboard` to restore it, "
                "or start a fresh loop with `awino loop run rpi --task \"...\"`"
            )
            raise typer.Exit(2)
        _echo("NO_LOOP  create one first: awino loop run rpi --task \"...\"")
        raise typer.Exit(2)
    try:
        kind = loops.kind_of(resolved)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    driver = _driver_for_kind(kind)
    state_file = driver.loops_dir / f"{resolved}.json"
    if not state_file.is_file():
        _echo(
            f"REFUSED  loop {resolved!r} is selected but its state file is "
            f"missing ({state_file}) -- it was deleted or never finished "
            "writing; start a fresh loop with `awino loop run ...`, or "
            "re-run `awino onboard` if the whole state directory was removed"
        )
        raise typer.Exit(2)
    try:
        state = driver.load(resolved)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    return driver, state


def _terse_narration() -> bool:
    """Whether the user model asks for terse narration.

    Terse mode drops the additive PURPOSE/YOU/CHECK narration lines; the
    stable machine-readable lines (PHASE, ARTIFACT, LOOP, ...) always stay.
    Calibration degrades to normal when the profile is unreadable.
    """
    try:
        return (
            working_memory.UserModel.narration(working_memory.UserModel.load())
            == "terse"
        )
    except Exception:
        return False


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
    if not _terse_narration():
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
    # Woven in, not opt-in-only: each checkpoint offers its context-relevant
    # thinking mode in plain language. The human accepts by running it, or
    # declines by not -- and the approval gate asks again regardless.
    if isinstance(driver, loops.RpiDriver) and not driver.thinking_satisfied(state):
        offer = driver.thinking_offer_for_phase(state.phase)
        if offer and not _terse_narration():
            mode, text = offer
            _echo(f"SUGGEST  {text}")
            _echo(f"         run it: awino loop think --mode {mode} --id {state.id}")
            _echo(
                "         or ask the driver to run one and share its take "
                "(always labeled as the driver's, never yours)"
            )


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
    if purpose and not _terse_narration():
        _echo(f"PURPOSE  {purpose}")
    role = driver.human_roles.get(first_phase)
    if role and not _terse_narration():
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
    skip_challenge: bool = typer.Option(
        False,
        "--skip-challenge",
        help="Skip the challenge-mode thinking gate; recorded as a conscious "
        "decision with --skip-reason.",
    ),
    skip_reason: str = typer.Option(
        "",
        "--skip-reason",
        help="Why the challenge gate is skipped; required with --skip-challenge.",
    ),
) -> None:
    """Start a Ralph loop and print the attempt prompt block.

    The challenge gate applies: at least one challenge-mode think
    (devil/blindspot/premortem/uncomfortable/assumption-destroyer) must be
    recorded in the project first -- `awino think <mode> --record <file>` --
    or skip consciously with --skip-challenge --skip-reason "...".
    """
    _challenge_gate_or_refuse(skip_challenge, skip_reason)
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
    skip_challenge: bool = typer.Option(
        False,
        "--skip-challenge",
        help="Skip the challenge-mode thinking gate; recorded as a conscious "
        "decision with --skip-reason.",
    ),
    skip_reason: str = typer.Option(
        "",
        "--skip-reason",
        help="Why the challenge gate is skipped; required with --skip-challenge.",
    ),
) -> None:
    """Start a Delegate loop and print the decompose prompt block.

    The challenge gate applies: at least one challenge-mode think
    (devil/blindspot/premortem/uncomfortable/assumption-destroyer) must be
    recorded in the project first -- `awino think <mode> --record <file>` --
    or skip consciously with --skip-challenge --skip-reason "...".
    """
    _challenge_gate_or_refuse(skip_challenge, skip_reason)
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

    if driver.last_criteria:
        _echo("CRITERIA  success criteria vs this artifact:")
        for criterion, status in driver.last_criteria:
            _echo(f"  [{status}] {criterion}")

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
    except loops.ComprehensionRequired as exc:
        # "Execute when comfortable and understanding": the driver enters
        # teach-back -- explains the concept, then asks the human to explain
        # it back -- and does NOT advance.
        for line in exc.teach_back:
            _echo(line)
        _echo("MISSING  before the plan advances:")
        for item in exc.missing:
            _echo(f"  - {item}")
        raise typer.Exit(1) from None
    except loops.ProblemUnconfirmed as exc:
        # The lawyer move, put to the user directly: you asked me to solve
        # X, but the evidence says the real problem is Y -- which do we
        # solve? The loop does NOT advance until answered.
        _echo(f"REFUSED  {exc}")
        _echo(f"ANSWER  {exc.question}")
        raise typer.Exit(1) from None
    except loops.ReceiptBlocked as exc:
        _echo(f"REFUSED  {exc}")
        for problem in exc.problems:
            _echo(f"  - {problem}")
        _echo(
            "Receipts are written by the driver when the artifact validates, "
            "never by hand: fix the named problem, then rerun "
            f"`awino loop next --id {state.id}`."
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
    if isinstance(driver, loops.RpiDriver) and state.phase == "research":
        _echo(driver.problem_line(state))
    if isinstance(driver, loops.RpiDriver) and state.phase == "pair-plan":
        for line in driver.describe_pairing(state):
            _echo(line)
    if (
        isinstance(driver, loops.RpiDriver)
        and state.phase == "plan"
        and state.plan_artifact
        and (driver.project_root / state.plan_artifact).is_file()
    ):
        for line in driver.describe_comprehension(state):
            _echo(line)
        comp = state.comprehension or {}
        if comp.get("explanation") or comp.get("probes") or comp.get("suggestions"):
            _echo(
                "COMPREHENSION_RECORD  paste at the end of the plan's "
                "decisions section:"
            )
            for line in driver.comprehension_record_block(state).splitlines():
                _echo(f"  {line}")
    if state.seed_id:
        _echo(f"seed: {state.seed_id}")
    _echo(f"locked: {'yes' if state.locked else 'no'}")
    if state.gate_run_id:
        _echo(f"gate_run: {state.gate_run_id}")
    role = driver.human_roles.get(state.phase)
    if role and state.phase != "done":
        _echo(f"role: {role}")
    _echo(f"next: {driver.status_next(state)}")
    # The spine: the owner's 10-step precondition chain, in order. Read-only
    # here -- advance()/close enforce it. "missing" names the artifact that
    # would let the loop advance; "pending" is not yet due or enforced at
    # its own boundary (work, verdict).
    _echo("SPINE  precondition status, in owner order:")
    for name, status, artifact in driver.spine_status(state):
        _echo(f"  [{status}] {name}: {artifact}")


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
    waive_thinking: bool = typer.Option(
        False,
        "--waive-thinking",
        help="Explicitly waive the critical-thinking requirement; recorded "
        "in the ledger as a conscious decision with --waive-reason.",
    ),
    waive_reason: str = typer.Option(
        "",
        "--waive-reason",
        help="Why critical thinking is waived; required with --waive-thinking.",
    ),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Record human approval of the plan. Required before implement starts (RPI).

    Ledger-enforced minimum bar: approval is BLOCKED until at least one
    critical-thinking mode has run on the loop (``awino loop think --mode
    <mode> --record <file>``) or the human explicitly waives it with
    --waive-thinking --waive-reason "...". The waiver is recorded in the
    ledger as a conscious decision ("thinking waived by human, reason:
    ..."). Forgetting is impossible -- the gate asks every time.

    "Execute when comfortable and understanding": the comprehension check
    (``awino loop explain --text "..."`` plus answering every probe with
    ``awino loop probe-answer``) must ALSO complete before approval -- a
    plan approved without understanding is a rubber stamp. Approval is
    refused until the human has explained the plan back.
    """
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
    if waive_thinking and not waive_reason.strip():
        _echo(
            "REFUSED  --waive-thinking requires --waive-reason <text>: "
            "a waiver without a reason is not a conscious decision"
        )
        raise typer.Exit(2)
    try:
        driver.approve_plan(
            state, by, reason, waive_reason=waive_reason if waive_thinking else None
        )
    except loops.ApprovalRequired as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    except loops.ComprehensionRequired as exc:
        # "Execute when comfortable and understanding": comprehension comes
        # BEFORE approval. The driver enters teach-back -- explains the
        # concept, then asks the human to explain it back -- and does NOT
        # approve.
        _echo("REFUSED  comprehension check incomplete: the plan cannot be approved")
        for line in exc.teach_back:
            _echo(line)
        _echo("MISSING  before the plan can be approved:")
        for item in exc.missing:
            _echo(f"  - {item}")
        raise typer.Exit(1) from None
    _echo(f"APPROVED  plan by={by}")
    if reason:
        _echo(f"reason: {reason}")
    # Case law: past similar decisions and their outcomes, surfaced where
    # the decision was recorded. Advisory only -- never blocks.
    for line in driver.last_precedents:
        _echo(f"PRECEDENT  {line}")
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
    # Case law: past similar decisions and their outcomes, surfaced where
    # the decision was recorded. Advisory only -- never blocks.
    for line in driver.last_precedents:
        _echo(f"PRECEDENT  {line}")
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
    # Case law: past similar decisions and their outcomes, surfaced where
    # the decision was recorded. Advisory only -- never blocks.
    for line in driver.last_precedents:
        _echo(f"PRECEDENT  {line}")
    remaining = driver.unanswered_questions(state)
    if remaining:
        _echo(f"REMAINING  {', '.join(remaining)}")
        _echo(
            f"Next: awino loop answer --question {remaining[0]} "
            f"--answer \"...\" --id {state.id}"
        )
    else:
        _echo(f"All questions answered. Advance with: awino loop next --id {state.id}")


# ── critical thinking, woven into the loop ─────────────────────────────────


@loop_app.command("think")
def loop_think(
    mode: str = typer.Option(..., "--mode", help="Thinking mode to run"),
    record: str = typer.Option(
        None,
        "--record",
        help="File with the mode's output: validates it structurally, writes "
        "its insights to working memory, and records the run on the loop.",
    ),
    by: str = typer.Option("human", "--by", help="Who ran the thinking"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Run a critical-thinking mode as a loop step (RPI).

    Without --record, prints the mode's prompt template (the structure of
    the thinking). With --record <file>, validates the output, records its
    insights in working memory, and records the run on the loop with a
    ledger event -- this is what satisfies the plan-approval thinking gate.
    The plan's decisions section may cite the run as thinking:<mode>. After
    recording, the driver shares its take as a suggestion, always attributed
    as the driver's -- never as the human's judgment.
    """
    driver, state = _resolve_driver_and_state(loop_id)
    if not isinstance(driver, loops.RpiDriver):
        _echo(
            f"REFUSED  loop {state.id} is not an RPI loop; "
            "thinking runs attach to RPI plans"
        )
        raise typer.Exit(1)
    try:
        item = think.by_name(mode)
    except ValueError:
        _echo(
            f"REFUSED  unknown thinking mode {mode!r}: "
            f"one of {', '.join(think.MODE_NAMES)}"
        )
        raise typer.Exit(2) from None
    if record is None:
        _echo(f"THINK  {item.name}")
        _echo(f"LOOP  {state.id}")
        _echo("")
        _echo(item.prompt)
        _echo("")
        _echo(f"REQUIRED SECTIONS  {', '.join(name for name, _ in item.sections)}")
        _echo(
            f"RECORD  awino loop think --mode {item.name} "
            f"--record <file> --id {state.id}"
        )
        return
    try:
        text = Path(record).read_text(encoding="utf-8")
    except OSError as exc:
        _echo(f"REFUSED  cannot read {record}: {exc}")
        raise typer.Exit(2) from None
    failures = think.validate(item.name, text)
    if failures:
        _echo(f"THINK_VERIFY  {item.name}  NON-COMPLIANT")
        for failure in failures:
            _echo(f"  - {failure}")
        raise typer.Exit(1)
    workspace = _workspace()
    filename, entry_id = think.record_insight(
        item.name,
        text,
        workspace.state_root,
        source=f"awino loop think {item.name} --id {state.id}",
    )
    driver.record_thinking_run(state, item.name, by, entry_id)
    _echo(f"THINK_RECORDED  {item.name}  loop={state.id}")
    _echo(f"MEMORY  {filename} {entry_id}")
    _echo(
        "DRIVER_VIEW  the driver's take (a suggestion, never your judgment): "
        f"{think.headline(text)}"
    )


@loop_app.command("explain")
def loop_explain(
    text: str = typer.Option(..., "--text", help="The plan in your own words"),
    by: str = typer.Option("human", "--by", help="Who is explaining"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Record your explanation of the plan, in your own words (RPI).

    "Execute when comfortable and understanding": the plan advances to
    implementation only when you can explain it back. Without a recorded
    explanation the gate waits; if the probes go unanswered or the
    explanation doesn't reference the plan's key decisions, the driver
    enters teach-back and does not advance.
    """
    driver, state = _resolve_driver_and_state(loop_id)
    if not isinstance(driver, loops.RpiDriver):
        _echo(
            f"REFUSED  loop {state.id} is not an RPI loop; "
            "explanations only apply to RPI plans"
        )
        raise typer.Exit(1)
    try:
        driver.record_explanation(state, text, by=by)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"EXPLAINED  by={by} ({len(text.strip())} chars)")
    remaining = driver.comprehension_missing(state)
    if remaining:
        _echo("REMAINING  before the plan advances:")
        for item in remaining:
            _echo(f"  - {item}")
    else:
        _echo(f"Comprehension complete. Advance with: awino loop next --id {state.id}")


@loop_app.command("probe-answer")
def loop_probe_answer(
    question: str = typer.Option(..., "--question", help="Probe id, e.g. P1"),
    answer: str = typer.Option(..., "--answer", help="Your answer, recorded verbatim"),
    by: str = typer.Option("human", "--by", help="Who answered"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Answer one of the driver's comprehension probes on the plan (RPI).

    Probes are 2-3 targeted questions derived from the plan's decisions
    section and stated risks; see them with `awino loop status`. The plan
    advances only when every probe is answered and your explanation
    references the plan's key decisions by name.
    """
    driver, state = _resolve_driver_and_state(loop_id)
    if not isinstance(driver, loops.RpiDriver):
        _echo(
            f"REFUSED  loop {state.id} is not an RPI loop; "
            "probes only apply to RPI plans"
        )
        raise typer.Exit(1)
    try:
        driver.record_probe_answer(state, question, answer, by=by)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"PROBE_ANSWERED  {question} by={by}")
    remaining = driver.comprehension_missing(state)
    if remaining:
        _echo("REMAINING  before the plan advances:")
        for item in remaining:
            _echo(f"  - {item}")
    else:
        _echo(f"Comprehension complete. Advance with: awino loop next --id {state.id}")


@loop_app.command("suggest")
def loop_suggest(
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """List the driver's suggestions on the plan (RPI).

    Goals clarity, missing objectives, and alternatives worth considering
    (Honda-first with effort labels). Decide each with `awino loop
    suggest-answer --suggestion S1 --verdict accepted|rejected --reason ...`.
    Accepting a plan-changing suggestion revises the plan: the approval is
    cleared and the revised plan re-validates.
    """
    driver, state = _resolve_driver_and_state(loop_id)
    if not isinstance(driver, loops.RpiDriver):
        _echo(
            f"REFUSED  loop {state.id} is not an RPI loop; "
            "suggestions only apply to RPI plans"
        )
        raise typer.Exit(1)
    suggestions = driver.plan_suggestions(state)
    if not suggestions:
        _echo("SUGGESTIONS  none: the driver has nothing to add to this plan")
        return
    _echo("SUGGESTIONS  the driver's suggestions (decide each one):")
    for suggestion in suggestions:
        decided = (state.comprehension.get("suggestions") or {}).get(suggestion.id)
        status = decided["verdict"] if decided else "open"
        _echo(f"  {suggestion.id} [{suggestion.kind}] [{status}] {suggestion.text}")
        if suggestion.changes_plan:
            _echo("      accepting this revises the plan (approval clears, plan re-validates)")
    _echo(
        "Decide with: awino loop suggest-answer --suggestion S1 "
        f"--verdict accepted|rejected --reason \"...\" --id {state.id}"
    )


@loop_app.command("suggest-answer")
def loop_suggest_answer(
    suggestion: str = typer.Option(..., "--suggestion", help="Suggestion id, e.g. S1"),
    verdict: str = typer.Option(..., "--verdict", help="accepted or rejected"),
    reason: str = typer.Option(..., "--reason", help="Why"),
    by: str = typer.Option("human", "--by", help="Who decided"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Record accepted/rejected + reason for a driver suggestion (RPI).

    Suggestions are recorded, never silently dropped. Accepting a
    plan-changing suggestion clears the plan approval and the comprehension
    records: revise the plan, then the gates ask again.
    """
    driver, state = _resolve_driver_and_state(loop_id)
    if not isinstance(driver, loops.RpiDriver):
        _echo(
            f"REFUSED  loop {state.id} is not an RPI loop; "
            "suggestions only apply to RPI plans"
        )
        raise typer.Exit(1)
    try:
        decided = driver.record_suggestion_decision(state, suggestion, verdict, reason, by=by)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"SUGGESTION_DECIDED  {suggestion} {decided['verdict']} by={by}")
    if decided["verdict"] == "accepted" and decided["changes_plan"]:
        _echo(
            "REVISED  plan-changing suggestion accepted: the plan approval "
            "was cleared and comprehension reset"
        )
        _echo(
            f"REVISED  revise the plan, then `awino loop next --id {state.id}` "
            "(it re-validates), explain it back, and approve again"
        )


@loop_app.command("confirm-problem")
def loop_confirm_problem(
    reframed: str = typer.Option(
        None,
        "--reframed",
        help="The real problem, when the evidence says the stated problem is "
        "wrong: records that the user chose the reframe.",
    ),
    confirmed: bool = typer.Option(
        False,
        "--confirmed",
        help="The stated problem stands: records that the user confirmed the "
        "problem as given is the actual problem.",
    ),
    by: str = typer.Option("human", "--by", help="Who confirmed the problem"),
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
) -> None:
    """Answer the lawyer move: which problem do we solve? (RPI, research phase).

    Before anything is solved, the research must ask whether the charge
    applies at all: stated problem vs. reframed problem, with the evidence.
    Exactly one of --reframed "..." / --confirmed. The confirmation is
    recorded on the loop (ledger event problem_confirmed) and research
    cannot advance -- to pair-planning or straight to plan -- without it.
    The command also prints the exact line to paste into the research
    artifact's applicability-check section, which the validator requires.
    """
    driver, state = _resolve_driver_and_state(loop_id)
    if not isinstance(driver, loops.RpiDriver):
        _echo(
            f"REFUSED  loop {state.id} is not an RPI loop; "
            "problem confirmation only applies to RPI research"
        )
        raise typer.Exit(1)
    if state.phase != "research":
        _echo(
            f"REFUSED  nothing to confirm: current phase is '{state.phase}'; "
            "the problem is confirmed during research, before planning"
        )
        raise typer.Exit(1)
    if (reframed is None) == (not confirmed):
        _echo(
            "REFUSED  exactly one of --reframed \"...\" / --confirmed: "
            "answer the question -- which problem do we solve?"
        )
        raise typer.Exit(2)
    try:
        confirmation = driver.confirm_problem(state, by, reframed=reframed)
    except loops.LoopError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    word = "reframed" if confirmation["verdict"] == "reframed" else "stated"
    _echo(f"PROBLEM_CONFIRMED  {confirmation['verdict']} by={by}")
    _echo(f"SOLVE  the {word} problem: {confirmation['solve']}")
    _echo("PASTE  into the research artifact's applicability-check section:")
    _echo(f"  {driver.problem_confirmation_line(state)}")
    _echo(f"Then advance: awino loop next --id {state.id}")


# ── outcome verdict ──────────────────────────────────────────────────────────

_VERDICTS = ("yes", "partial", "no")

_VERDICT_MEANINGS = {
    "yes": "the goal was accomplished",
    "partial": "part of the goal was accomplished",
    "no": "the goal was not accomplished",
}


def _verdict_seed_context(
    project_root: Path, driver: loops.LoopDriver, state: loops.LoopState
) -> tuple[str | None, str, str]:
    """Seed title (or None), the seed's resulting status, and the next action.

    Read-only: the verdict records the seed's status, it never changes it.
    The loop's own terminal semantics -- Ralph/Delegate close the seed on
    verified success, RPI hands it to the gate ledger, escalation leaves it
    open -- are untouched.
    """
    seed_id = state.seed_id
    recorded = (
        "nothing follows: the verdict is recorded; "
        "`awino buddy` reports outcome rates"
    )
    by_hand = (
        "check the seeds tracker by hand, then close the seed by hand "
        "when the work is truly done"
    )
    if not seed_id:
        return None, "n/a: no seed attached", recorded
    try:
        tracker = Seeds(project_root)
        usable, message = tracker.state()
        issue = tracker.show(seed_id) if usable else None
    except Exception as exc:
        return None, f"unknown: seeds tracker unreadable ({exc})", by_hand
    if not usable:
        return None, f"unknown: seeds tracker unavailable ({message})", by_hand
    title = issue.title if issue is not None else None
    if issue is None:
        return None, "unknown: the seed is not in the tracker", by_hand
    if not issue.open:
        return title, "closed already", recorded
    if isinstance(driver, loops.RpiDriver):
        run = state.gate_run_id or "<run>"
        return (
            title,
            "still open: the RPI loop hands the seed to the gate ledger; "
            "it closes with the gate evidence, not with this loop",
            f"finish gate evidence, run `awino work-close --run {run}`, "
            f"then `awino gate close --run {run}`",
        )
    if state.locked:
        return (
            title,
            "still open: escalation leaves the seed open because the work "
            "is unverified",
            driver.seed_open_note(state),
        )
    if state.phase != "done":
        return (
            title,
            "still open: the loop has not completed, and a seed closes only "
            "on successful verification",
            "finish the loop, or close the seed by hand when the work is "
            "truly done",
        )
    # Ralph/Delegate completed with the seed still open: verified success
    # should have closed it, so closure failed or the seed was reopened.
    return (
        title,
        f"still open: {driver.seed_open_note(state)}",
        "close the seed by hand when the work is truly done",
    )


def _verdict_criteria_lines(
    driver: loops.LoopDriver,
    state: loops.LoopState,
    project_root: Path,
    criteria: list[str],
) -> tuple[list[str], list[str]]:
    """Judge the outcome against the mission's success criteria.

    Returns (ledger detail parts, CLI lines): which criteria were met, unmet,
    or unjudgeable against the loop's latest artifact. No criteria on file
    means the verdict judges the goal statement only -- never invented ones.
    """
    if not criteria:
        return (
            ["criteria_unjudgeable: no success criteria on file"],
            ["CRITERIA  none on file: the verdict judges the goal statement only"],
        )
    artifact_rel = driver.judged_artifact(state)
    if artifact_rel is None:
        judged = [(criterion, "unjudgeable") for criterion in criteria]
        note = "no artifact on disk to judge"
    else:
        text = (project_root / artifact_rel).read_text(
            encoding="utf-8", errors="replace"
        )
        judged = loops.evaluate_success_criteria(criteria, text)
        note = f"judged against {artifact_rel}"
    met = [c for c, status in judged if status == "met"]
    unmet = [c for c, status in judged if status == "unmet"]
    unjudgeable = [c for c, status in judged if status == "unjudgeable"]
    detail = [
        f"criteria_met: {'; '.join(met) or '(none)'}",
        f"criteria_unmet: {'; '.join(unmet) or '(none)'}",
        f"criteria_unjudgeable: {'; '.join(unjudgeable) or '(none)'}",
    ]
    lines = [
        f"CRITERIA  {note}: {len(met)} met, {len(unmet)} unmet, "
        f"{len(unjudgeable)} unjudgeable"
    ]
    for criterion, status in judged:
        lines.append(f"  [{status}] {criterion}")
    return detail, lines


@loop_app.command("close")
def loop_close(
    loop_id: str = typer.Option(None, "--id", help="Loop id; defaults to the current one"),
    verdict: str = typer.Option(
        None, "--verdict", help="Outcome verdict: yes, partial, or no"
    ),
    note: str = typer.Option("", "--note", help="Short note on the outcome"),
) -> None:
    """Record the outcome verdict for a loop: did it accomplish the goal?

    Asks the outcome question directly -- "Did this accomplish the goal?" --
    with the loop's goal context (mission goal / seed title), then records an
    outcome_verdict ledger event. `awino buddy` reports outcome rates from
    these verdicts: outcomes are the scoreboard.
    """
    driver, state = _resolve_driver_and_state(loop_id)
    workspace = _workspace()
    goal = loops.mission_goal_statement(workspace.project.root)
    seed_title, seed_status, next_action = _verdict_seed_context(
        workspace.project.root, driver, state
    )

    # The outcome question always prints: the human answers it by rerunning
    # with --verdict.
    _echo("Did this accomplish the goal?")
    _echo(f"  goal: {goal}")
    if state.seed_id:
        seed_line = f"  seed: {state.seed_id}"
        if seed_title:
            seed_line += f" - {seed_title}"
        _echo(seed_line)
    else:
        _echo("  seed: none")

    if verdict is None:
        _echo(
            "REFUSED  missing --verdict: rerun with "
            f"--verdict yes|partial|no --id {state.id}"
        )
        raise typer.Exit(2)
    verdict = verdict.strip().lower()
    if verdict not in _VERDICTS:
        _echo(
            f"REFUSED  bad --verdict {verdict!r}: expected one of "
            f"{', '.join(_VERDICTS)}"
        )
        raise typer.Exit(2)

    # Mission is living: if the success criteria changed since this loop's
    # work was last examined against them, prompt the human to update the
    # mission and re-examine first -- the verdict must judge live criteria,
    # not stale ones. Recovery: update the mission, run `awino loop next`
    # (which re-validates and re-syncs), then close.
    live_hash = heilmeier.criteria_hash(heilmeier.load(workspace.state_root))
    if (
        state.criteria_hash
        and live_hash != state.criteria_hash
        and driver.has_validated_artifacts(state)
    ):
        _echo(
            "PROMPT  the mission's success criteria changed since this loop's "
            "work was last examined;"
        )
        _echo(
            "        update the mission first -- the verdict must judge live "
            "criteria, not stale ones:"
        )
        _echo('        awino mission --set "exams=<claim> -> <verify command>"')
        _echo(
            "        then re-examine the work: "
            f"awino loop next --id {state.id}"
        )
        _echo(
            f"REFUSED  stale success criteria; no verdict recorded for loop {state.id}"
        )
        raise typer.Exit(2)

    criteria_detail, criteria_lines = _verdict_criteria_lines(
        driver,
        state,
        workspace.project.root,
        loops.mission_success_criteria(workspace.project.root),
    )

    detail = "; ".join(
        [
            f"verdict: {verdict}",
            f"loop_id: {state.id}",
            f"loop_kind: {driver.loop_kind}",
            f"seed: {state.seed_id if state.seed_id else 'none'}",
            f"goal: {goal}",
            f"seed_status: {seed_status}",
            *criteria_detail,
            *([f"note: {note.strip()}"] if note.strip() else []),
        ]
    )
    _ledger().record_loop_event(
        LoopEvent(
            loop_id=state.id,
            loop_kind=driver.loop_kind,
            phase=state.phase,
            kind="outcome_verdict",
            at=datetime.now(UTC).isoformat(),
            detail=detail,
        )
    )
    # Outcome verdicts update the user model: what the verdict's note says
    # about how the work landed teaches how this human works.
    model = working_memory.UserModel.load()
    if working_memory.UserModel.apply_verdict(model, verdict, note):
        working_memory.UserModel.save(model)
        _echo("USER_MODEL  updated from this verdict (see ~/.awino/profile.yaml)")
    # The verdict is also the loop's closing boundary for the checklist.
    working_memory.Checklist(workspace.state_root).note_verdict(
        state.id, verdict, note.strip()
    )
    if not _terse_narration():
        _echo(
            "PURPOSE  record the loop's outcome; `awino buddy` reports outcome "
            "rates from these verdicts"
        )
        _echo(
            f"YOU  answer honestly: yes ({_VERDICT_MEANINGS['yes']}), "
            f"partial ({_VERDICT_MEANINGS['partial']}), "
            f"no ({_VERDICT_MEANINGS['no']})"
        )
        _echo(
            "CHECK  the goal above is the yardstick: a verdict means nothing "
            "without the goal it was measured against"
        )
    _echo(f"VERDICT  {verdict}")
    if note.strip():
        _echo(f"note: {note.strip()}")
    for line in criteria_lines:
        _echo(line)
    _echo(f"seed: {seed_status}")
    _echo(f"next: {next_action}")
