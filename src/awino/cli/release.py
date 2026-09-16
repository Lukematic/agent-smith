"""owns: release verify, release publish, release push, release tag

`awino release`: verify the artifact, then gate publish/push/tag behind
explicit authorization."""

from __future__ import annotations

from pathlib import Path

import typer

from awino import release as _release
from awino.cli import registered_command_names

release_app = typer.Typer(
    no_args_is_help=True,
    help="Verify the artifact and gate releases behind explicit authorization.",
)


def _default_root() -> Path:
    """The checkout this code runs from.

    Prefers the current directory when it looks like the A.W.I.N.O. checkout
    (so `awino release verify` checks the tree you're standing in), else the
    checkout containing this file.
    """
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").is_file() and (cwd / "src" / "awino").is_dir():
        return cwd
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return cwd


def _resolve_root(root: str | None) -> Path:
    return Path(root).resolve() if root else _default_root()


@release_app.command("verify")
def verify_command(
    root: str | None = typer.Option(
        None, "--root", help="Artifact root to verify (default: this A.W.I.N.O. checkout)."
    ),
) -> None:
    """Verify the artifact: manifest accuracy, bundle completeness.

    On a good artifact lists the verified capabilities; on a broken one says
    exactly what is broken and exits 1.
    """
    report = _release.verify_artifact(_resolve_root(root), registered_command_names())
    if report.ok:
        typer.echo(f"VERIFIED  artifact {report.version}: {len(report.capabilities)} capabilities")
        for cap in report.capabilities:
            typer.echo(f"  - {cap}")
    else:
        typer.echo(f"REFUSED  artifact is broken ({len(report.problems)} problem(s)):")
        for problem in report.problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(1) from None


def _guarded(action: str, authorize: bool, root: str | None) -> None:
    try:
        result = _release.release_gate(
            _resolve_root(root),
            action=action,
            authorize=authorize,
            commands=registered_command_names(),
        )
    except _release.AuthorizationRequired as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from None
    except _release.ReleaseRefused as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"AUTHORIZED  {result.action}: artifact verified ({len(result.capabilities)} capabilities)"
    )
    typer.echo(f"NOTE  {result.note}")


@release_app.command("publish")
def publish_command(
    authorize: bool = typer.Option(
        False, "--authorize", help="Explicit, separate authorization to publish."
    ),
    root: str | None = typer.Option(
        None, "--root", help="Artifact root to verify (default: this A.W.I.N.O. checkout)."
    ),
) -> None:
    """Publish the artifact. Refuses without --authorize."""
    _guarded("publish", authorize, root)


@release_app.command("push")
def push_command(
    authorize: bool = typer.Option(
        False, "--authorize", help="Explicit, separate authorization to push."
    ),
    root: str | None = typer.Option(
        None, "--root", help="Artifact root to verify (default: this A.W.I.N.O. checkout)."
    ),
) -> None:
    """Push the artifact. Refuses without --authorize."""
    _guarded("push", authorize, root)


@release_app.command("tag")
def tag_command(
    authorize: bool = typer.Option(
        False, "--authorize", help="Explicit, separate authorization to tag."
    ),
    root: str | None = typer.Option(
        None, "--root", help="Artifact root to verify (default: this A.W.I.N.O. checkout)."
    ),
) -> None:
    """Tag the artifact. Refuses without --authorize."""
    _guarded("tag", authorize, root)
