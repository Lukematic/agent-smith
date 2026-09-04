"""owns: route, fetch, drift

The knowledge harness: route a question without spending budget, fetch one file
with provenance, and diff the local registry against upstream.
"""

from __future__ import annotations

import typer

from smith import (
    cli,
)
from smith.cli import (
    _echo,
    _paths,
    app,
)
from smith.knowledge import BudgetExceeded, FetchError


@app.command()
def fetch(
    path: str = typer.Argument(
        ..., help="Registry path, for example chapters/6-harnesses/1-what-is-a-harness.md"
    ),
    source: str = typer.Option("book", help="Source id from SOURCES.yaml"),
    force: bool = typer.Option(False, "--force", help="Refetch even if the cache is fresh"),
) -> None:
    """Fetch one knowledge file into the cache with provenance."""
    store = cli.KnowledgeStore(_paths())
    try:
        target, status = store.fetch(path, source, force=force)
    except BudgetExceeded as exc:
        _echo(f"BUDGET_EXCEEDED  {exc}")
        raise typer.Exit(2) from exc
    except FetchError as exc:
        _echo(f"FETCH_FAILED  {exc}")
        raise typer.Exit(1) from exc
    entry = store.manifest.get(source, path)
    sha = entry.sha if entry else "unknown"
    size = entry.bytes if entry else 0
    _echo(f"{status}  {target.name}  sha={sha}  bytes={size}")


@app.command()
def route(question: str = typer.Argument(..., help="The question to route")) -> None:
    """Show which chapters a question routes to, without fetching anything."""
    store = cli.KnowledgeStore(_paths())
    keys = store.route(question)
    if not keys:
        _echo("NO_ROUTE  no registry match. Run 'awino update' in case upstream added a chapter.")
        raise typer.Exit(1)
    _echo(f"routing: {question!r}")
    for key in keys[: store.budget]:
        _echo(f"  {key}  {store.path_for_key(key) or '(key not in registry)'}")
    if len(keys) > store.budget:
        deferred = ", ".join(keys[store.budget :])
        _echo(f"  deferred beyond budget {store.budget}: {deferred}")


@app.command()
def drift() -> None:
    """Diff the local registry against upstream and write a drift report."""
    store = cli.KnowledgeStore(_paths())
    result = store.drift()
    report = store.write_drift_report(result)
    _echo(
        f"UPSTREAM={result['upstream_count']}  REGISTRY={result['registry_count']}  "
        f"ADDED={len(result['added'])}  REMOVED={len(result['removed'])}"
    )
    for path in result["added"][:20]:
        _echo(f"  + {path}")
    for path in result["removed"][:20]:
        _echo(f"  - {path}")
    _echo(f"REPORT  {report}")
    if result["added"] or result["removed"]:
        raise typer.Exit(1)
