"""owns: brain start, brain stage, brain record, brain confirm, brain me, brain list, brain use, brain export

Brain mode: turn a messy problem into a plan you can explain and a proposal a
decision maker can say yes to. ``awino brain`` shows where the active session
stands and the next stage's prompt; ``record`` checks a stage's Markdown and
stores it; ``confirm`` records the human's answer at the two checkpoints. The
engine and the stage contracts live in ``awino.brain``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from awino import brain
from awino.cli import _echo, _workspace

brain_app = typer.Typer(
    no_args_is_help=False,
    help="Brain mode: frame a problem, map what you bring, propose, and prepare to explain it.",
)


def _state_root() -> Path:
    return _workspace().state_root


def _active() -> brain.Session:
    session = brain.active(_state_root())
    if session is None:
        _echo('NO_SESSION  start one with: awino brain start "<short title>" [--brief <file>]')
        raise typer.Exit(1)
    return session


def _show_stage(session: brain.Session, stage: brain.Stage) -> None:
    n = brain.STAGES.index(stage) + 1
    _echo(f"STAGE  {n}/{len(brain.STAGES)} {stage.name} - {stage.title}: {stage.purpose}")
    _echo(f"WRITE  {session.file_for(stage)}")
    _echo("")
    _echo(stage.prompt)
    _echo("")
    _echo(f"RECORD  awino brain record {stage.name}")


def _status(session: brain.Session) -> None:
    _echo(f"BRAIN  {session.title}  ({session.id})")
    _echo(f"FOLDER  {session.path}")
    _echo(f"BRIEF  {session.path / brain.BRIEF_FILE}")
    gaps = brain.profile_gaps()
    if gaps:
        _echo(f"PROFILE  missing {', '.join(gaps)}: {brain.profile_path()}")
    else:
        _echo(f"PROFILE  {brain.profile_path()}")
    for stage in brain.STAGES:
        if session.recorded(stage.name):
            mark = "done"
            if stage.checkpoint:
                mark = "confirmed" if session.confirmed(stage.name) else "WAITING for the human"
        else:
            mark = "-"
        _echo(f"  {stage.name:<8} {mark}")
    nxt = session.next_stage()
    if nxt is None:
        _echo("COMPLETE  report.md and speaker-notes.md are ready; awino brain export <dir>")
        return
    if session.recorded(nxt.name) and nxt.checkpoint:
        _echo(
            f"CHECKPOINT  show {nxt.filename} to the human, then: "
            f'awino brain confirm {nxt.name} --note "<their words>"'
        )
        return
    _echo("")
    _show_stage(session, nxt)


@brain_app.callback(invoke_without_command=True)
def brain_default(ctx: typer.Context) -> None:
    """Bare `awino brain` shows the active session and what to do next."""
    if ctx.invoked_subcommand is None:
        _status(_active())


@brain_app.command("start")
def brain_start(
    title: str = typer.Argument(..., help="A short title, e.g. 'Acme intake triage'."),
    brief: str = typer.Option(None, "--brief", help="A file with everything you have."),
) -> None:
    """Start a session. Everything the human gives you goes in brief.md."""
    try:
        text = brain.copy_brief(Path(brief)) if brief else None
    except brain.BrainError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    session = brain.start(_state_root(), title, text)
    _echo(f"STARTED  {session.id}")
    _echo("PRIVATE  .awino/brain/ ignores itself in git; nothing here is committed")
    if not text:
        _echo(f"BRIEF  paste everything into {session.path / brain.BRIEF_FILE}")
    _status(session)


@brain_app.command("stage")
def brain_stage(
    name: str = typer.Argument(None, help="Stage name; omit for the next one."),
) -> None:
    """Print a stage's prompt: the structure the thinking goes into."""
    session = _active()
    try:
        stage = brain.by_name(name) if name else session.next_stage()
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    if stage is None:
        _echo("COMPLETE  every stage is recorded")
        return
    _show_stage(session, stage)


@brain_app.command("record")
def brain_record(
    name: str = typer.Argument(..., help="Stage name."),
    file: str = typer.Option(None, "--file", help="Defaults to the stage's file in the session."),
) -> None:
    """Check a stage's Markdown and store it in the session."""
    session = _active()
    try:
        stage = brain.by_name(name)
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    source = Path(file) if file else session.file_for(stage)
    if not source.is_file():
        _echo(f"REFUSED  nothing to record: write {source} first")
        raise typer.Exit(2)
    problems = brain.record(session, stage.name, source.read_text(encoding="utf-8"))
    if problems:
        _echo(f"NOT_YET  {stage.name}")
        for problem in problems:
            _echo(f"  - {problem}")
        raise typer.Exit(1)
    _echo(f"RECORDED  {stage.name} -> {session.file_for(stage)}")
    if stage.name == "plan":
        likes = brain.interests()
        fit = brain.interest_fit(source.read_text(encoding="utf-8"), likes)
        for head, score in fit:
            _echo(f"  FIT {score:.2f}  {head}")
        if likes and not any(score for _, score in fit):
            _echo("  NOTE  no step touches your interests; is that deliberate?")
    if stage.name == "grow":
        _echo(f"PROFILE  growth log updated: {brain.profile_path()}")
    if stage.checkpoint:
        _echo(
            f"CHECKPOINT  stop here. Show {stage.filename} to the human and ask; then "
            f'awino brain confirm {stage.name} --note "<their words>"'
        )
        return
    nxt = session.next_stage()
    if nxt is None:
        _echo("COMPLETE  report.md and speaker-notes.md are ready; awino brain export <dir>")
    else:
        _echo(f"NEXT  awino brain stage {nxt.name}")


@brain_app.command("confirm")
def brain_confirm(
    name: str = typer.Argument(..., help="The checkpoint stage: problem or options."),
    note: str = typer.Option(..., "--note", help="What the human said, in their words."),
) -> None:
    """Record the human's answer at a checkpoint. Only after they actually answered."""
    session = _active()
    try:
        brain.confirm(session, name, note)
    except (brain.BrainError, ValueError) as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    _echo(f"CONFIRMED  {name}: {note.strip()[:120]}")
    nxt = session.next_stage()
    if nxt is not None:
        _echo(f"NEXT  awino brain stage {nxt.name}")


@brain_app.command("me")
def brain_me(
    init: bool = typer.Option(False, "--init", help="Create the profile from the template."),
) -> None:
    """Show the private profile (background, strengths, interests) Brain uses."""
    path = brain.init_profile() if init else brain.profile_path()
    _echo(f"PROFILE  {path}")
    _echo("PRIVATE  on this machine only; never committed")
    if not path.is_file():
        _echo("MISSING  create it with: awino brain me --init  (then interview the human)")
        raise typer.Exit(1)
    gaps = brain.profile_gaps()
    _echo(f"GAPS  {', '.join(gaps)}" if gaps else "COMPLETE  background, strengths, interests")
    _echo("")
    _echo(path.read_text(encoding="utf-8"))


@brain_app.command("list")
def brain_list() -> None:
    """Every Brain session in this project."""
    current = brain.active(_state_root())
    found = brain.sessions(_state_root())
    if not found:
        _echo("NO_SESSIONS")
        return
    for s in found:
        nxt = s.next_stage()
        where = nxt.name if nxt else "complete"
        flag = "*" if current and current.id == s.id else " "
        _echo(f"{flag} {s.id:<50} next: {where}")


@brain_app.command("use")
def brain_use(session_id: str = typer.Argument(..., help="Session id from `awino brain list`.")):
    """Switch the active session."""
    try:
        session = brain.use(_state_root(), session_id)
    except brain.BrainError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(2) from None
    _status(session)


@brain_app.command("export")
def brain_export(dest: str = typer.Argument(..., help="Folder to copy the documents to.")):
    """Copy report.md and speaker-notes.md somewhere you will open them."""
    session = _active()
    out = brain.export(session, Path(dest))
    if not out:
        _echo("NOTHING  record the report and notes stages first")
        raise typer.Exit(1)
    for path in out:
        _echo(f"EXPORTED  {path}")
