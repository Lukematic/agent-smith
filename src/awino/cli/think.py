"""owns: think

Critical thinking modes: ``awino think <mode>`` prints a mode's prompt
template (the structure of the thinking -- the model thinks inside it, like
a phase prompt block); ``awino think <mode> --record <file>`` validates the
output structurally and writes its insights to working memory. With no mode,
lists all nine modes with one-line when-to-use descriptions, like
``awino skills``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from awino import think
from awino.cli import _echo, _workspace, app


@app.command("think")
def think_command(
    mode: str = typer.Argument(
        None, help="Thinking mode: feynman, blindspot, devil, premortem, "
        "uncomfortable, thought-experiment, first-principles, "
        "assumption-destroyer, or simplify. Omit to list all nine."
    ),
    record: str = typer.Option(
        None,
        "--record",
        help="Validate this file's output structurally and record its "
        "insights in working memory (facts.md / decisions.md).",
    ),
) -> None:
    """Run a critical-thinking mode: the executable form of "challenge assumptions".

    Each mode has a prompt template (the structure of the thinking), required
    output sections enforced by a structural validator, and a working-memory
    destination -- thinking that doesn't land in memory didn't happen.
    Modes map onto stances where they overlap (devil -> steel-man,
    feynman -> teach-back, first-principles -> the research breakdown);
    the rest are genuinely new. Verify an output any time with
    ``awino stance --verify <mode> --response <file>``.
    """
    if mode is None:
        _echo("THINK_MODES  nine ways to challenge assumptions (like `awino skills`)")
        for item in think.MODES:
            stance_note = (
                f" [maps onto the {item.stance} stance]"
                if item.stance
                else " [no stance equivalent]"
            )
            _echo(f"  {item.name:<20} {item.when_to_use}{stance_note}")
        _echo("Run one: awino think <mode>  (then record its output with --record <file>)")
        return

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
        _echo(f"MODE  {item.name} -- {item.when_to_use}")
        if item.stance:
            _echo(f"STANCE  maps onto the {item.stance} stance (reused, not duplicated)")
        else:
            _echo("STANCE  no stance equivalent -- genuinely new")
        _echo("")
        _echo(item.prompt)
        _echo("")
        sections = ", ".join(name for name, _ in item.sections)
        _echo(f"REQUIRED SECTIONS  {sections}")
        _echo(f"RECORD  awino think {item.name} --record <file>")
        _echo(f"VERIFY  awino stance --verify {item.name} --response <file>")
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
        item.name, text, workspace.state_root, source=f"awino think {item.name}"
    )
    _echo(f"THINK_RECORDED  {item.name}")
    _echo(f"MEMORY  {filename} {entry_id}")
