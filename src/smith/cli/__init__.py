"""A.W.I.N.O. (Agentic Workflow Intelligence & Navigation Orchestrator) command line interface.

Working history: this project shipped as "A.W.I.N.O."; the package, module path,
and `smith` command are kept for compatibility (see docs/MISSION.md). Every
command here is deterministic. The model calls these rather than doing the
work in prose, which is the ``MODEL_DOES_DETERMINISM`` guard applied to A.W.I.N.O.

This package owns the Typer apps and the helpers every command shares. The
commands themselves live in sibling modules, each declaring the commands it
owns in the first line of its docstring; they are imported at the bottom of
this file so that ``from smith import cli; cli.app`` has every command
registered by the time the import returns.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import typer

from smith import debugging, seeds, skill_catalog
from smith.enforce import Gate, Ledger, LedgerError
from smith.knowledge import KnowledgeStore
from smith.paths import SmithPaths, Workspace
from smith.toolchain import Toolchain

__all__ = [
    "KnowledgeStore",
    "app",
    "debug_app",
    "deprecated_smith_entry",
    "floor_app",
    "gate_app",
    "gate_plan_app",
    "main",
    "version_callback",
]

# On Windows, sys.stdout/stderr can default to the legacy console codepage
# (e.g. cp1252) even when the terminal or a redirected file expects UTF-8.
# That codepage cannot round-trip characters such as an em-dash cleanly and
# silently substitutes U+FFFD (the mojibake replacement character) instead
# of raising. Force UTF-8 explicitly so CLI output is not corrupted by the
# ambient console codepage. reconfigure() is a no-op where it is unsupported
# (e.g. some captured/piped streams in test runners), so this is safe
# everywhere, not just on Windows.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8")

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="A.W.I.N.O.: knowledge harness, artifact validation, and folder hygiene.",
)
gate_app = typer.Typer(
    no_args_is_help=True, help="Run ledger. Completion is computed, never claimed."
)
gate_plan_app = typer.Typer(no_args_is_help=True, help="Review the current run's plan.")
debug_app = typer.Typer(no_args_is_help=True, help="Evidence-first four-phase bug debugging.")
floor_app = typer.Typer(help="Portable dispatch floors: any environment can be the worker.")


def _version() -> str:
    try:
        return version("awino-harness")
    except PackageNotFoundError:
        return "0+unknown"


def version_callback(value: bool) -> None:
    if value:
        _echo(f"awino {_version()}")
        raise typer.Exit()


@app.callback()
def main(
    version_requested: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the installed A.W.I.N.O. version and exit.",
    ),
) -> None:
    """A.W.I.N.O. command group."""
    del version_requested


def deprecated_smith_entry() -> None:
    """Compatibility entry point for the former executable name."""
    if os.environ.get("AWINO_SUPPRESS_DEPRECATION") != "1":
        typer.echo("DEPRECATED: 'smith' is the former command name; use 'awino'.", err=True)
    app()


app.add_typer(gate_app, name="gate")
app.add_typer(debug_app, name="debug")
gate_app.add_typer(gate_plan_app, name="plan")
app.add_typer(floor_app, name="floor")


# ── shared helpers ───────────────────────────────────────────────────────────


def _paths() -> SmithPaths:
    return SmithPaths.discover()


def _workspace() -> Workspace:
    return Workspace.discover()


def _toolchain(workspace: Workspace | None = None) -> Toolchain:
    """Toolchain of the project under work, not of A.W.I.N.O."""
    return Toolchain((workspace or _workspace()).project.root)


def _skill_catalog() -> skill_catalog.SkillCatalog:
    workspace = _workspace()
    return skill_catalog.SkillCatalog(
        workspace.project.root / ".kilo" / "skills",
        Path.home() / ".config" / "kilo" / "skills",
        workspace.home.skills,
    )


def _echo(message: str) -> None:
    typer.echo(message)


def _debug_session(run_id: str | None = None) -> debugging.DebugSession:
    try:
        return debugging.DebugSession.current(_ledger(), run_id)
    except (LedgerError, ValueError) as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None


def _detect_claude_plugin() -> bool:
    """Whether this install is used through the Claude Code plugin system.

    A single settings.json check is enough: it is written by the Claude
    plugin manager itself when a plugin is enabled, so its presence is
    evidence of the plugin path, not an assumption about how A.W.I.N.O.
    happens to be installed on this particular machine.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.is_file():
        return False
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    enabled = settings.get("enabledPlugins", {})
    return any(key.split("@")[0] == "awino" for key in enabled)


# ── the run ledger: completion is earned, not asserted ───────────────────────


def _ledger() -> Ledger:
    """Ledger for the project under work, never for A.W.I.N.O. home.

    Runs are project history. A shared A.W.I.N.O. install that accumulated every
    project's runs would make attribution impossible.
    """
    workspace = _workspace()
    workspace.ensure_state()
    return Ledger(workspace.state_root)


def _resolve_run(run_id: str | None) -> str:
    ledger = _ledger()
    resolved = run_id or ledger.current_id()
    if not resolved:
        _echo('NO_RUN  open one first: awino gate open <task-class> "<objective>"')
        raise typer.Exit(2)
    return resolved


def _ledger_error(exc: LedgerError) -> None:
    _echo(f"REFUSED  {exc}")
    raise typer.Exit(1) from None


def _validate_issue(issue_id: str) -> seeds.Issue:
    tracker = seeds.Seeds(_workspace().project.root)
    state, reason = tracker.state()
    if not state.usable:
        _echo(f"ISSUE_UNAVAILABLE  {reason}")
        raise typer.Exit(2)
    issue = tracker.show(issue_id)
    if issue is None:
        _echo(f"ISSUE_NOT_FOUND  {issue_id}")
        raise typer.Exit(2)
    if not issue.open:
        _echo(f"ISSUE_CLOSED  {issue_id} status={issue.status}")
        raise typer.Exit(2)
    return issue


def _require_valid_plan(ledger: Ledger, run_id: str) -> None:
    run = ledger.load(run_id)
    if run.schema_version < 2 or Gate.PLANNED not in run.required:
        return
    problems = ledger.validate_plan(run_id)
    if problems:
        raise LedgerError(f"PLAN_INVALID: {'; '.join(problems)}")


# Command modules register themselves against the apps above on import. This
# must stay at the bottom: each module imports the helpers defined here.
from smith.cli import dispatch, gate, install, knowledge, maintain, project  # noqa: E402

del dispatch, gate, install, knowledge, maintain, project
