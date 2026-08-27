"""A.W.I.N.O. (Agentic Workflow Intelligence & Navigation Orchestrator) command line interface.

Working history: this project shipped as "A.W.I.N.O."; the package, module path,
and `smith` command are kept for compatibility (see docs/MISSION.md). Every
command here is deterministic. The model calls these rather than doing the
work in prose, which is the ``MODEL_DOES_DETERMINISM`` guard applied to A.W.I.N.O.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import typer

from smith import (
    capability,
    completion_review,
    config_review,
    debugging,
    doc_review,
    fix,
    harness,
    health,
    mission,
    models,
    modes,
    onboarding,
    project_guard,
    seeds,
    session_state,
    skill_catalog,
    spawn,
    updater,
    watch,
)
from smith.enforce import (
    CONTRACTS,
    Gate,
    Ledger,
    LedgerError,
    PlanDecision,
    ReviewVerdict,
    TaskClass,
    adjudicate,
    detect_scope_violations,
    detect_test_weakening,
    score_run,
)
from smith.knowledge import BudgetExceeded, FetchError, KnowledgeStore
from smith.paths import SmithPaths, Workspace
from smith.tidy import Finding, Tidier
from smith.toolchain import Manager, Toolchain, tool_install_command
from smith.validate import BROKEN_SELFTEST, Status, discover, validate_file, validate_text

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


@debug_app.command("begin")
def debug_begin(
    symptom: str = typer.Argument(..., help="Observed bug, error, or failing test"),
    scope: list[str] = typer.Option(None, "--scope", help="Production path this fix may edit"),
    issue: str = typer.Option(None, "--issue", help="Open Seeds issue served by this debug run"),
    actor: str = typer.Option("agent", "--actor"),
) -> None:
    """Open a bugfix run in the reproduce phase."""
    ledger = _ledger()
    linked_issue = _validate_issue(issue) if issue else None
    run = ledger.open(
        TaskClass.BUGFIX,
        symptom,
        file_scope=scope or [],
        issue_id=linked_issue.id if linked_issue else None,
    )
    debugging.DebugSession.begin(ledger, run.run_id, symptom, actor)
    _echo(f"DEBUG_BEGIN  {run.run_id}  phase={debugging.DebugPhase.REPRODUCE}")


@debug_app.command("evidence")
def debug_evidence(
    kind: str = typer.Argument(...),
    detail: str = typer.Argument(...),
    actor: str = typer.Option("agent", "--actor"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    session.add_evidence(kind, detail, actor)
    _echo(f"DEBUG_EVIDENCE  phase={session.phase}")


@debug_app.command("hypothesize")
def debug_hypothesize(
    statement: str = typer.Argument(...),
    actor: str = typer.Option("agent", "--actor"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    try:
        session.add_hypothesis(statement, actor)
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"DEBUG_HYPOTHESIS  phase={session.phase}")


@debug_app.command("authorize-fix")
def debug_authorize_fix(
    by: str = typer.Option(..., "--by", help="Person or agent authorizing the evidence-backed fix"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    try:
        session.authorize_fix(by)
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"DEBUG_FIX_AUTHORIZED  phase={session.phase}")


@debug_app.command("attempt")
def debug_attempt(
    approach: str = typer.Argument(...),
    output: str = typer.Argument(...),
    succeeded: bool = typer.Option(False, "--succeeded/--failed"),
    actor: str = typer.Option("agent", "--actor"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    try:
        session.record_attempt(approach, output, succeeded=succeeded, actor=actor)
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"DEBUG_ATTEMPT  assessment={session.assessment}")


@debug_app.command("verify")
def debug_verify(
    command: str = typer.Option(..., "--cmd", help="Regression command to execute and record"),
    actor: str = typer.Option("agent", "--actor"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    try:
        evidence = session.ledger.record(
            session.run_id, Gate.TESTED, command, _workspace().project.root
        )
        session.verify(
            command,
            evidence.output_head,
            succeeded=evidence.passed,
            actor=actor,
        )
    except (LedgerError, ValueError) as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    verdict = "DEBUG_VERIFIED" if evidence.passed else "DEBUG_VERIFY_FAILED"
    _echo(f"{verdict}  phase={session.phase}  assessment={session.assessment}")
    _echo(evidence.output_head)
    if not evidence.passed:
        raise typer.Exit(1)


# ── knowledge ────────────────────────────────────────────────────────────────


@app.command()
def fetch(
    path: str = typer.Argument(
        ..., help="Registry path, for example chapters/6-harnesses/1-what-is-a-harness.md"
    ),
    source: str = typer.Option("book", help="Source id from SOURCES.yaml"),
    force: bool = typer.Option(False, "--force", help="Refetch even if the cache is fresh"),
) -> None:
    """Fetch one knowledge file into the cache with provenance."""
    store = KnowledgeStore(_paths())
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
    store = KnowledgeStore(_paths())
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
    store = KnowledgeStore(_paths())
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


@app.command()
def update() -> None:
    """Refresh stale cache entries and report registry drift."""
    paths = _paths()
    store = KnowledgeStore(paths, budget=10_000)
    refreshed = 0
    for entry in store.manifest.entries:
        if entry.is_stale(store.stale_days):
            store.fetch(entry.path, entry.source_id, force=True, charge=False)
            refreshed += 1
    result = store.drift()
    store.write_drift_report(result)
    orphans = store.orphaned_cache()
    _echo(f"refreshed={refreshed}  orphaned_cache={len(orphans)}")
    _echo(
        f"UPSTREAM={result['upstream_count']}  REGISTRY={result['registry_count']}  "
        f"ADDED={len(result['added'])}  REMOVED={len(result['removed'])}"
    )
    if result["added"]:
        _echo("Curate each ADDED path one at a time with tags and use_when. Never bulk-add.")


@app.command("update-preflight")
def update_preflight_command(
    pull: bool = typer.Option(True, "--pull/--no-pull", help="Fetch and fast-forward after checks"),
) -> None:
    """Snapshot user state and safely fast-forward a clean source clone."""
    workspace = _workspace()
    mode_paths = [target.path for target in modes.discover(workspace.project.root)]
    harness_paths = mode_paths + [
        target.persona_path for target in harness.discover(workspace.project.root)
    ]
    if not pull:
        backup = updater.snapshot(workspace.home.root, workspace.project.root, harness_paths)
        _echo(f"BACKUP  {backup}")
        return
    try:
        backup = updater.update_preflight(
            workspace.home.root, workspace.project.root, harness_paths
        )
    except updater.PreflightError as exc:
        _echo(f"BACKUP  {exc.backup}")
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from exc
    _echo(f"BACKUP  {backup}")
    _echo("UPDATED  source is clean and fast-forwarded")


@app.command("rollback")
def rollback_command(
    backup: Path = typer.Argument(..., help="BACKUP path from update-preflight"),
    include_harness: bool = typer.Option(
        False,
        "--include-harness",
        help="Also restore detected harness files; project state is the safe default.",
    ),
) -> None:
    """Restore user-owned project and harness state from a preflight backup."""
    workspace = _workspace()
    harness_paths: list[Path] = []
    if include_harness:
        mode_paths = [target.path for target in modes.discover(workspace.project.root)]
        harness_paths = mode_paths + [
            target.persona_path for target in harness.discover(workspace.project.root)
        ]
    try:
        restored = updater.restore(backup, workspace.project.root, harness_paths)
    except FileNotFoundError as exc:
        _echo(f"ROLLBACK_FAILED  {exc}")
        raise typer.Exit(1) from exc
    for path in restored:
        _echo(f"RESTORED  {path}")
    _echo(f"ROLLBACK_COMPLETE  restored={len(restored)}")


@app.command("watch")
def watch_command(
    seed: bool = typer.Option(
        False, "--seed", help="Also create a review seed for each changed source (requires sd)"
    ),
    as_json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Poll every configured and watchlisted source for upstream changes.

    This is change *detection*, not autonomous learning. It answers "did the
    tree sha change since last check" for the book, overstory, warren, seeds
    repos, and anything added via `awino watch add`. Nothing is fetched into the
    registry and no knowledge is integrated automatically — see docs
    for why that boundary is deliberate.
    """
    paths = _paths()
    findings = watch.scan_all(paths)

    if as_json_output:
        _echo(watch.as_json(findings))
    else:
        _echo(watch.as_report(findings))

    if seed:
        tracker = seeds.Seeds(_workspace().project.root)
        state, reason = tracker.state()
        if not state.usable:
            _echo(f"\nSKIP seed creation: {reason}")
        else:
            for payload in watch.seed_titles_for_changes(findings):
                result = tracker.create(
                    payload["title"],
                    issue_type="task",
                    priority=3,
                    description=payload["description"],
                    labels=payload["labels"],
                )
                _echo(f"  seed: {'OK' if result.ok else 'FAILED'} {result.detail}")


@app.command("watch-add")
def watch_add_command(
    repo: str = typer.Argument(..., help="owner/repo, e.g. someuser/some-agent-skills"),
    ref: str = typer.Option("main", "--ref", help="Branch or tag to watch"),
    note: str = typer.Option("", "--note", help="Why this repo is worth watching"),
) -> None:
    """Add a repo to the watchlist. This is the 'check out this repo' command.

    Adding a repo here does not fetch or integrate anything. It only means the
    repo's tree sha is checked on the next `awino watch`.
    """
    if "/" not in repo:
        _echo("REFUSED: expected owner/repo")
        raise typer.Exit(2)
    owner, _, name = repo.partition("/")
    paths = _paths()
    try:
        entry = watch.add_watched_repo(paths, owner, name, ref=ref, note=note)
    except ValueError as exc:
        _echo(f"REFUSED: {exc}")
        raise typer.Exit(1) from exc
    _echo(f"WATCHING  {entry.id}  ({entry.html_url})")


@app.command("watch-remove")
def watch_remove_command(
    repo: str = typer.Argument(..., help="owner/repo to stop watching"),
) -> None:
    """Remove a repo from the watchlist."""
    removed = watch.remove_watched_repo(_paths(), repo)
    _echo("REMOVED" if removed else "NOT FOUND, nothing removed")


@app.command("watch-list")
def watch_list_command() -> None:
    """Show every source and watchlisted repo A.W.I.N.O. checks for changes."""
    paths = _paths()
    watched = watch.load_watchlist(paths)
    _echo("CONFIGURED SOURCES (knowledge/SOURCES.yaml, checked by 'awino watch')")
    import yaml as _yaml

    if paths.sources.is_file():
        data = _yaml.safe_load(paths.sources.read_text(encoding="utf-8")) or {}
        for source in data.get("sources", []):
            has_tree = "tree_api" in source
            _echo(
                f"  {source.get('id'):<12} {'watched' if has_tree else 'no tree to diff':<16} {source.get('name', '')}"
            )
    if watched:
        _echo("")
        _echo("USER-ADDED WATCHLIST (knowledge/watchlist.yaml)")
        for entry in watched:
            _echo(f"  {entry.id:<30} {entry.note or '(no note)'}")
    else:
        _echo("")
        _echo("No user-added repos yet. Add one with: awino watch-add owner/repo")


@app.command()
def status() -> None:
    """Show cache age, size, and binding lesson count."""
    paths = _paths()
    store = KnowledgeStore(paths)
    age = store.manifest.newest_age_days
    lessons = 0
    if paths.lessons.is_file():
        lessons = len(
            re.findall(
                r"^- \[\d{4}-\d{2}-\d{2}\]", paths.lessons.read_text(encoding="utf-8"), re.MULTILINE
            )
        )
    verdict = "cold" if age is None else ("STALE" if age >= store.stale_days else "fresh")
    freshness = "no cache" if age is None else f"newest {age}d"
    _echo(f"root: {paths.root}")
    _echo(f"knowledge: {len(store.manifest.entries)} cached, {freshness} ({verdict})")
    _echo(f"registry: {len(store.registry_paths())} chapters indexed")
    _echo(f"memory: {lessons} binding lessons")
    _echo(
        f"knowledge budget: {store.budget} fetched book files per task; project files do not count"
    )


# ── validation ───────────────────────────────────────────────────────────────


@app.command()
def validate(
    targets: list[Path] = typer.Argument(None, help="Files or directories to validate"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show every check, not just problems"
    ),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as failures"),
    selftest: bool = typer.Option(
        False, "--selftest", help="Prove the validator blocks a broken artifact"
    ),
) -> None:
    """Validate authored skills and agents. Exits nonzero when any FAIL is present."""
    if selftest:
        report = validate_text(Path("selftest/bad-orchestrator.md"), BROKEN_SELFTEST)
        caught = [c.name for c in report.failures]
        _echo(f"selftest failures caught: {len(caught)}")
        for name in caught:
            _echo(f"  FAIL  {name}")
        expected = {
            "no_colon_in_description",
            "orchestrator_unarmed",
            "section_failure_modes",
            "section_completion",
            "cites_book",
            "completion_has_evidence",
            "body_not_empty",
        }
        missing = expected - set(caught)
        if missing:
            _echo(f"SELFTEST FAILED, validator missed: {sorted(missing)}")
            raise typer.Exit(1)
        _echo("SELFTEST OK, validator blocks broken artifacts")
        return

    paths = _paths()
    resolved = [p if p.is_absolute() else paths.root / p for p in (targets or [])] or [
        paths.skills,
        paths.agents,
        paths.emitted,
    ]
    files = discover([p for p in resolved if p.exists()])
    if not files:
        _echo("no artifacts found")
        raise typer.Exit(1)

    reports = [validate_file(f) for f in files]
    for report in reports:
        rel = (
            report.path.relative_to(paths.root)
            if paths.root in report.path.parents
            else report.path
        )
        _echo("")
        _echo(f"=== {rel}  [{report.kind}] ===")
        shown = (
            report.checks
            if verbose
            else [c for c in report.checks if c.status in {Status.FAIL, Status.WARN}]
        )
        for check in shown:
            _echo(f"  {check.status:<4}  {check.name}: {check.detail}")
        _echo(
            f"  PASS={report.count(Status.PASS)} FAIL={report.count(Status.FAIL)} "
            f"WARN={report.count(Status.WARN)} SKIP={report.count(Status.SKIP)}  "
            f"=> {'OK' if report.ok(strict=strict) else 'BLOCKED'}"
        )

    blocked = [r for r in reports if not r.ok(strict=strict)]
    _echo("")
    _echo(
        f"SUMMARY  files={len(reports)}  ok={len(reports) - len(blocked)}  blocked={len(blocked)}"
    )
    if blocked:
        _echo("")
        _echo("BLOCKING:")
        for report in blocked:
            for check in report.failures or report.warnings:
                _echo(f"  {report.path.name} :: {check.name} - {check.detail}")
        raise typer.Exit(1)
    _echo("VALIDATE OK")


# ── hygiene ──────────────────────────────────────────────────────────────────


@app.command()
def tidy(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report clutter without moving anything"),
    strict: bool = typer.Option(
        False, "--strict", help="Also fail on regenerable caches, not just real clutter"
    ),
) -> None:
    """Find clutter in A.W.I.N.O.'s own installation and archive it.

    This inspects A.W.I.N.O.'s home directory, not the project you are working
    in. AWINO_PROJECT and the current project's toolchain have no effect
    here. A target project's own clutter is checked by 'awino gate review'
    instead, scoped to that project. Archiving is reversible; deleting is not.

    Regenerable caches are reported but do not fail by default. A ship gate that
    fails because `__pycache__` exists trains you to ignore it, which is worse than
    having no gate at all.
    """
    paths = _paths()
    store = KnowledgeStore(paths)
    tidier = Tidier(paths)
    items = tidier.scan(orphaned_cache=store.orphaned_cache())

    if not items:
        _echo("CLEAN  no clutter found")
        return

    regenerable = {Finding.DISPOSABLE, Finding.EMPTY_DIR, Finding.ORPHANED_CACHE}
    real = [i for i in items if i.kind not in regenerable]
    caches = [i for i in items if i.kind in regenerable]

    for item in items:
        rel = item.path.relative_to(paths.root) if paths.root in item.path.parents else item.path
        action = "archive" if item.archivable else "delete via 'just clean'"
        _echo(f"  {item.kind:<18} {rel}  ({item.detail}) -> {action}")

    if dry_run:
        _echo("")
        if real:
            _echo(
                f"DRY_RUN  {len(real)} real finding(s), {len(caches)} regenerable. Run 'just tidy'."
            )
            raise typer.Exit(1)
        _echo(f"CLEAN  {len(caches)} regenerable artifact(s) only. Run 'just clean' to remove.")
        if strict:
            raise typer.Exit(1)
        return

    destination, moved = tidier.archive(items)
    _echo("")
    _echo(f"ARCHIVED  {len(moved)} items -> {destination}")
    remaining = [i for i in items if not i.archivable]
    if remaining:
        _echo(f"REMAINING  {len(remaining)} disposable items, run 'just clean'")


@app.command()
def clean() -> None:
    """Delete disposable artifacts from A.W.I.N.O.'s own installation.

    This operates on A.W.I.N.O.'s home directory, not the project you are
    working in; AWINO_PROJECT has no effect here. Cache is disposable; memory
    never is.
    """
    paths = _paths()
    store = KnowledgeStore(paths)
    tidier = Tidier(paths)
    items = tidier.scan(orphaned_cache=store.orphaned_cache())
    removed = tidier.clean(items)
    for path in removed:
        _echo(f"  removed {path.name}")
    _echo(f"CLEAN  removed {len(removed)} disposable items")


# ── install ──────────────────────────────────────────────────────────────────


@app.command()
def link(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be linked"),
) -> None:
    """Symlink this repo as a global agent plugin and install the persona."""
    paths = _paths()
    target = harness.Target(harness.Harness.AGENTS, Path.home() / ".agents", "global")

    _echo(f"plugin: {target.plugin_path} -> {paths.root}")
    _echo(f"agent:  {target.persona_path} <- {paths.agents / 'awino.md'}")
    if dry_run:
        _echo("DRY_RUN  nothing changed")
        return

    actions = harness.install(paths.root, target)
    for action in actions:
        _echo(f"{action.outcome:<10} {action.path}  {action.detail}")
    if any(action.failed for action in actions):
        raise typer.Exit(1)
    _echo("Next: start a session and ask '@awino what is a harness?'")


@app.command("install")
def install_command(
    which: str = typer.Option(
        None, "--harness", help="claude, agents, kilo, or cursor. Default: all detected."
    ),
    scope: str = typer.Option("global", "--scope", help="global or project"),
    project: bool = typer.Option(False, "--project", help="Shorthand for --scope project"),
    pointer: bool = typer.Option(False, "--pointer", help="Also write an AGENTS.md pointer block"),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        "--force",
        help="Replace existing links and skills rather than skipping",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
) -> None:
    """Install the persona and skills into every harness found on this machine.

    Detection rather than assumption: Claude Code, Goose, Kilo, and Cursor each
    expect the persona somewhere different, so A.W.I.N.O. adapts instead of demanding
    one layout.
    """
    workspace = _workspace()
    smith_home = workspace.home.root
    scope_wanted = "project" if project else scope

    targets = [
        t
        for t in harness.discover(workspace.project.root)
        if t.scope == scope_wanted and (which is None or str(t.harness) == which)
    ]
    present = [t for t in targets if t.exists]
    chosen = present or ([t for t in targets if which] if which else [])

    _echo(f"A.W.I.N.O. home: {smith_home}")
    _echo(f"project:    {workspace.project.root}")
    _echo("")

    if not chosen:
        _echo(f"No {scope_wanted} harness directories found. Candidates:")
        for target in targets:
            _echo(f"  {target.describe()}")
        _echo("")
        _echo("Create one, or name it explicitly:")
        _echo("  awino install --harness claude")
        raise typer.Exit(1)

    failed = False
    for target in chosen:
        _echo(f"TARGET  {target.describe()}")
        if dry_run:
            _echo(f"  would write  {target.persona_path}")
            if target.harness.supports_skills:
                where = target.plugin_path if target.harness.uses_plugins else target.skills_root
                _echo(f"  would link   {where}")
            continue
        for action in harness.install(smith_home, target, overwrite=overwrite):
            _echo(f"  {action.outcome:<10} {action.path.name}  {action.detail}")
            if action.failed:
                failed = True

    if pointer and not dry_run:
        markers = ("## A.W.I.N.O.", "## A.W.I.N.O.")
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = workspace.project.root / name
            if not path.is_file():
                continue
            body = path.read_text(encoding="utf-8")
            if any(marker in body for marker in markers):
                _echo(f"POINTER    {name} already references A.W.I.N.O.")
                continue
            path.write_text(
                body.rstrip() + "\n\n" + harness.pointer_text(smith_home), encoding="utf-8"
            )
            _echo(f"POINTER    appended to {name}")
            break
        else:
            target_file = workspace.project.root / "AGENTS.md"
            target_file.write_text(harness.pointer_text(smith_home), encoding="utf-8")
            _echo(f"POINTER    created {target_file.name}")

    if dry_run:
        _echo("")
        _echo("DRY_RUN  nothing changed")
        return

    if failed:
        raise typer.Exit(1)

    _echo("")
    _echo("Verify with:  awino install-status")
    _echo("Then ask your agent:  what is a harness?")


@app.command("install-mode")
def install_mode_command(
    editor: str = typer.Option(None, "--editor", help="kilo, roo, or zoo. Default: all detected."),
    scope: str = typer.Option("global", "--scope", help="global or project"),
    project: bool = typer.Option(False, "--project", help="Shorthand for --scope project"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing A.W.I.N.O. mode"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
    emit_json: bool = typer.Option(False, "--json", help="Print the mode definitions instead"),
) -> None:
    """Install A.W.I.N.O. as a selectable mode in Kilo, Roo, or a fork.

    A mode is not a persona file. It appears in the mode selector, replaces the
    system prompt, and declares which tool groups it may use, so the restriction is
    enforced by the editor rather than requested in prose.

    Three modes are installed, split by capability rather than by topic:
    the full agent, a read-only consult, and a Markdown-only planner.
    """
    workspace = _workspace()
    smith_home = workspace.home.root

    if emit_json:
        _echo(modes.as_json(smith_home))
        return

    scope_wanted = "project" if project else scope
    targets = [
        t
        for t in modes.discover(workspace.project.root)
        if t.scope == scope_wanted and (editor is None or t.editor == editor)
    ]
    available = [t for t in targets if t.exists or t.parent_exists]
    chosen = available or ([t for t in targets if editor] if editor else [])

    _echo(f"A.W.I.N.O. home: {smith_home}")
    _echo("")

    if not chosen:
        _echo(f"No {scope_wanted} editor found. Candidates:")
        for target in targets:
            _echo(f"  {target.describe()}")
        _echo("")
        _echo("Name one explicitly:  awino install-mode --editor kilo")
        raise typer.Exit(1)

    built = modes.build_modes(smith_home)
    invalid = [(m.slug, m.validate()) for m in built if m.validate()]
    if invalid:
        for slug, problems in invalid:
            _echo(f"INVALID  {slug}: {'; '.join(problems)}")
        raise typer.Exit(1)

    failed = False
    for target in chosen:
        _echo(f"TARGET  {target.describe()}")
        for mode in built:
            if dry_run:
                _echo(f"  would add  {mode.slug:<22} groups={mode.groups}")
                continue
            outcome, detail = modes.install(mode, target, force=force)
            _echo(f"  {outcome:<10} {mode.slug:<22} {detail}")
            failed = failed or outcome == "FAILED"

    if dry_run:
        _echo("")
        _echo("DRY_RUN  nothing changed")
        return

    if failed:
        raise typer.Exit(1)

    _echo("")
    _echo("Reload the editor window, then pick the mode from the selector.")
    _echo("Verify with:  awino mode-status")


@app.command("mode-status")
def mode_status_command() -> None:
    """Show where the A.W.I.N.O. modes are installed."""
    workspace = _workspace()
    expected = [mode.slug for mode in modes.build_modes(workspace.home.root)]
    rows = {slug: modes.status(workspace.project.root, slug) for slug in expected}
    installed_targets: dict[tuple[str, str, str], list[str]] = {}
    missing_targets: dict[tuple[str, str, str], list[str]] = {}
    for slug, statuses in rows.items():
        for target, present in statuses:
            if not (target.exists or target.parent_exists):
                continue
            key = (target.label, target.scope, str(target.path))
            bucket = installed_targets if present else missing_targets
            bucket.setdefault(key, []).append(slug)

    if installed_targets:
        _echo("INSTALLED")
        for (label, scope, path), slugs in installed_targets.items():
            _echo(f"  {label:<14} {scope:<8} {len(slugs)}/{len(expected)} modes  {path}")
            for slug in slugs:
                _echo(f"      + {slug}")
    else:
        _echo("No A.W.I.N.O. mode installed. Run: awino install-mode")

    globally_complete = {
        label
        for (label, scope, _path), slugs in installed_targets.items()
        if scope == "global" and set(slugs) == set(expected)
    }
    incomplete = {
        key: slugs
        for key, slugs in missing_targets.items()
        if slugs and not (key[1] == "project" and key[0] in globally_complete)
    }
    if incomplete:
        _echo("")
        _echo("EDITOR PRESENT BUT MODES INCOMPLETE")
        for (label, scope, path), slugs in incomplete.items():
            _echo(f"  {label:<14} {scope:<8} missing {', '.join(slugs)}")
            _echo(f"      {path}")
        _echo("")
        _echo("  awino install-mode --force")


@app.command("install-status")
def install_status_command() -> None:
    """Show where A.W.I.N.O. is installed and where it is not."""
    workspace = _workspace()
    rows = harness.status(workspace.project.root)
    installed = [r for r in rows if r[1]]

    _echo(f"A.W.I.N.O. home: {workspace.home.root}")
    _echo("")
    if installed:
        _echo("INSTALLED")
        for target, _, detail in installed:
            _echo(f"  {target.harness.label:<22} {target.scope:<8} {detail}")
    else:
        _echo("Not installed anywhere. Run: awino install")

    missing = [r for r in rows if not r[1] and r[0].exists]
    if missing:
        _echo("")
        _echo("HARNESS PRESENT BUT A.W.I.N.O. ABSENT")
        for target, _, _ in missing:
            _echo(f"  {target.harness.label:<22} {target.scope:<8} {target.root}")
        _echo("")
        _echo("  awino install")


@app.command("pointer")
def pointer_command() -> None:
    """Print the AGENTS.md block that makes A.W.I.N.O. discoverable in a project."""
    _echo(harness.pointer_text(_workspace().home.root))


@app.command()
def scaffold() -> None:
    """Create any missing directories A.W.I.N.O. expects."""
    created = _paths().ensure_scaffold()
    for path in created:
        _echo(f"  created {path}")
    _echo(f"SCAFFOLD  {len(created)} directories created")


@app.command()
def hook(event: str = typer.Argument("session-start", help="Hook event adapter")) -> None:
    """Inject confirmed project memory and enforce project workflow guardrails."""
    payload: dict = {}
    try:
        if not sys.stdin.isatty():
            payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError, json.JSONDecodeError):
        payload = {}
    workspace = _workspace()
    project = workspace.project.root
    intent = onboarding.load(project)
    if event == "session-start":
        if intent and intent.source == "confirmed":
            session_state.start(workspace.state_root, str(payload.get("session_id") or "unknown"))
            _echo(project_guard.project_context(intent))
        _hook_freshness()
        return
    if event == "prompt":
        if intent and intent.source == "confirmed":
            context = project_guard.project_context(intent)
            _echo(project_guard.emit(project_guard.prompt_context(context)))
        return
    if event == "pre-tool":
        if intent and intent.source == "confirmed":
            decision = project_guard.pre_tool_decision(intent, payload, project)
            if decision:
                _echo(project_guard.emit(decision))
        return
    _echo(f"unknown hook event {event!r}")
    raise typer.Exit(2)


def _hook_freshness() -> None:
    paths = _paths()
    store = KnowledgeStore(paths)
    age = store.manifest.newest_age_days
    lessons = 0
    if paths.lessons.is_file():
        lessons = len(
            re.findall(
                r"^- \[\d{4}-\d{2}-\d{2}\]", paths.lessons.read_text(encoding="utf-8"), re.MULTILINE
            )
        )
    if age is None:
        _echo(f"[awino] cache cold, fetch on demand | lessons: {lessons}")
        return
    verdict = "STALE, run 'just update'" if age >= store.stale_days else "fresh"
    _echo(
        f"[awino] knowledge {age}d old ({verdict}) | cached: {len(store.manifest.entries)} | lessons: {lessons}"
    )


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

    store = KnowledgeStore(paths)
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


@app.command("pit")
def pit_command(
    easy: str = typer.Option(..., "--easy", help="What happens if nobody is careful"),
    correct: str = typer.Option(..., "--correct", help="What should happen"),
) -> None:
    """Check whether a design relies on someone choosing the harder path.

    This predicts which rules will decay. A rule that costs more to follow than to
    ignore is a wish, not a constraint.
    """
    verdict = models.audit_pit_of_success(easy, correct)
    _echo(f"easy path:    {verdict.easy_path}")
    _echo(f"correct path: {verdict.correct_path}")
    _echo("")
    _echo(f"{'ALIGNED' if verdict.aligned else 'MISALIGNED'}  {verdict.advice}")
    if not verdict.aligned:
        raise typer.Exit(1)


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


@app.command()
def skills(
    paths_only: bool = typer.Option(False, "--paths", help="Print absolute paths, one per line"),
    as_json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
    route: str | None = typer.Option(None, "--route", help="Recommend a skill for this request"),
) -> None:
    """List canonical skills, optionally recommending one for a request.

    This is how A.W.I.N.O. learns where its own skills live at load time rather than
    hardcoding a directory that moves when the install location changes.
    """
    paths = _paths()
    catalog = _skill_catalog()
    found = [
        {
            "name": item.name,
            "namespaced": f"awino:{item.name}",
            "path": str(item.path),
            "description": item.description,
            "source": item.source,
        }
        for item in catalog.skills
    ]
    recommendation = catalog.recommend(route) if route is not None else None
    routed = None
    if recommendation is not None:
        routed = {
            "name": recommendation.skill.name,
            "score": recommendation.score,
            "matched_name": recommendation.matched_name,
            "matched_description": recommendation.matched_description,
        }
        ledger = _ledger()
        current = ledger.inspect_current()
        if current.status == "active" and current.run_id is not None:
            ledger.note_skill(
                current.run_id,
                recommendation.skill.name,
                state="recommended",
                reason=route or "",
            )

    if as_json_output:
        _echo(
            json.dumps(
                {
                    "root": str(paths.root),
                    "count": len(found),
                    "skills": found,
                    "recommendation": routed,
                },
                indent=2,
            )
        )
        return
    if paths_only:
        for item in found:
            _echo(item["path"])
        return

    _echo(f"root: {paths.root}")
    _echo(f"{len(found)} skill(s):")
    width = max((len(i["name"]) for i in found), default=0)
    for item in found:
        summary = item["description"].split(". ")[0][:80]
        _echo(f"  {item['name']:<{width}}  [{item['source']}] {summary}")
    if route is not None:
        _echo("")
        if routed is None:
            _echo("NO_RECOMMENDATION  no positive request-word match")
        else:
            matches = sorted(set(routed["matched_name"]) | set(routed["matched_description"]))
            _echo(
                f"RECOMMENDED  {routed['name']}  score={routed['score']} "
                f"matched={','.join(matches)}"
            )
    _echo("")
    _echo("Load one by name in a session, or record usage with: awino gate skill <name>")


@app.command("fix")
def fix_command(
    aggressive: bool = typer.Option(
        False, "--aggressive", help="Also archive stray root files, which moves them"
    ),
    check_after: bool = typer.Option(
        True, "--check/--no-check", help="Re-run the doctor afterwards"
    ),
) -> None:
    """Repair what is mechanically fixable, report what needs judgement.

    Safe repairs regenerate derived files and remove build artifacts. Anything
    requiring prose or a real verification command is reported instead, because a
    synthesised gate passes the validator and means nothing.
    """
    paths = _paths()
    repairs = fix.run_fixes(paths, aggressive=aggressive)

    width = max(len(r.check) for r in repairs)
    for repair in repairs:
        _echo(f"  {repair.outcome:<8} {repair.check:<{width}}  {repair.detail}")

    fixed = [r for r in repairs if r.outcome is fix.Outcome.FIXED]
    manual = [r for r in repairs if r.outcome is fix.Outcome.MANUAL]
    _echo("")
    _echo(
        f"FIX  fixed={len(fixed)}  manual={len(manual)}  skipped={len(repairs) - len(fixed) - len(manual)}"
    )

    if manual:
        _echo("")
        _echo("NEEDS JUDGEMENT, not automation:")
        for repair in manual:
            _echo(f"  - {repair.check}: {repair.detail}")

    if check_after:
        _echo("")
        _echo("Re-running the doctor:")
        results = health.run_all(paths, fast=True)
        counts = health.summarise(results)
        for result in results:
            if result.health is not health.Health.OK:
                _echo(f"  {result.health:<4}  {result.name}: {result.detail}")
        _echo(f"HEALTH  ok={counts['ok']}  warn={counts['warn']}  fail={counts['fail']}")
        if counts["fail"]:
            raise typer.Exit(1)


@app.command()
def doctor(
    fast: bool = typer.Option(False, "--fast", help="Skip lint, format, and tests"),
    as_json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
    setup_project: bool = typer.Option(
        False,
        "--setup",
        help="Offer/initialize optional project tooling such as Seeds after explicit confirmation.",
    ),
    record: bool = typer.Option(
        False, "--record", help="Record the verdict against the current run"
    ),
) -> None:
    """Check project health. Refuses when any gate fails.

    This is the ledger turned on the repository itself: clean structure, linked
    docs, working lint, a real justfile, a valid pyproject, a synced uv
    environment, a single worklist, and a well-formed lessons ledger.
    """
    paths = _paths()
    results = health.run_all(paths, fast=fast)
    # The standard health suite checks A.W.I.N.O.'s own installation. Seeds belongs to
    # the project being worked on, so replace that one result with a project-aware
    # check rather than reporting the parent workspace's tracker.
    workspace = _workspace()
    if setup_project:
        tracker = seeds.Seeds(workspace.project.root)
        state, reason = tracker.state()
        if not state.usable:
            _echo(f"SEEDS  {reason}")
            if tracker.installed and typer.confirm(
                "Initialize the optional Seeds tracker in this project?", default=False
            ):
                result = tracker.init(confirmed=True)
                _echo(f"  {'OK' if result.ok else 'FAILED'}  {result.detail}")
    project_seeds = health.check_seeds(paths, workspace.project.root)
    results = [project_seeds if result.name == "seeds" else result for result in results]

    if as_json_output:
        _echo(health.as_json(results))
    else:
        width = max(len(r.name) for r in results)
        for result in results:
            _echo(f"  {result.health:<4}  {result.name:<{width}}  {result.detail}")
            if result.remedy and result.health is not health.Health.OK:
                _echo(f"        {' ' * width}  -> {result.remedy}")
        counts = health.summarise(results)
        _echo("")
        _echo(f"HEALTH  ok={counts['ok']}  warn={counts['warn']}  fail={counts['fail']}")

    failing = [r for r in results if r.blocking]

    if record:
        run_id = _ledger().current_id()
        if not run_id:
            _echo("NOTE  no open run, verdict not recorded")
        else:
            note = f"doctor {'ok' if not failing else 'FAILING: ' + ', '.join(r.name for r in failing)}"
            if failing:
                _ledger().record(run_id, Gate.REVIEWED, "uv run awino doctor --fast")
            else:
                _ledger().attest(run_id, Gate.REVIEWED, note)
            _echo(f"RECORDED  {note}")

    if failing:
        _echo("")
        _echo(f"REFUSED  {len(failing)} gate(s) failing: {', '.join(r.name for r in failing)}")
        raise typer.Exit(1)


@app.command("config-review")
def config_review_command(
    as_json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Audit project configuration for drift, conflicts, and unsafe defaults.

    Read-only: this never rewrites pyproject.toml, Makefile, Justfile, CI
    workflows, harness config, .env files, or kilo.json. Every finding cites
    the exact file (and line, when addressable) it came from.
    """
    workspace = _workspace()
    project = workspace.project.root
    findings = config_review.review(project)
    has_error = any(str(finding.severity) == "error" for finding in findings)

    if as_json_output:
        _echo(config_review.as_json(project, findings))
        if has_error:
            raise typer.Exit(1)
        return

    _echo(f"project: {project}")
    if not findings:
        _echo("CLEAN  no configuration findings")
        return

    for finding in findings:
        _echo(
            f"  {finding.severity:<5} {finding.category:<12} {finding.citation(project)}  "
            f"{finding.message}"
        )
        if finding.suggested_command:
            _echo(f"        try: {finding.suggested_command}")

    counts: dict[str, int] = {}
    for finding in findings:
        counts[str(finding.severity)] = counts.get(str(finding.severity), 0) + 1
    _echo("")
    _echo(
        "SUMMARY  "
        + "  ".join(
            f"{severity}={counts.get(severity, 0)}" for severity in ("error", "warn", "info")
        )
    )
    if has_error:
        raise typer.Exit(1)


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
) -> None:
    """Read what the project is for, from its own authored sources.

    A.W.I.N.O. never invents a mission. An agent acting confidently on a fabricated
    purpose is worse than one that asks, because the fabrication propagates into
    every downstream plan.
    """
    workspace = _workspace()
    tracker = seeds.Seeds(workspace.project.root)
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


@app.command("heal")
def heal_command(
    command: str = typer.Argument(..., help="The command that failed"),
    attempts: int = typer.Option(3, "--attempts", help="Maximum diagnose-heal-retry cycles"),
    timeout: int = typer.Option(900, "--timeout", help="Seconds per attempt"),
) -> None:
    """Diagnose a failing command, apply a known remedy, and retry.

    This is real self-healing, not a claim of it: each failure is matched to a
    named signature, and only remedies that are idempotent and require no
    judgement run automatically. A credential problem or a missing test target is
    reported with the human action, never guessed at.
    """
    from smith import healing

    workspace = _workspace()
    run = healing.run_with_healing(
        command, workspace.project.root, max_attempts=attempts, timeout=timeout
    )

    _echo(f"command: {command}")
    for attempt in run.attempts:
        marker = "HEALED " if attempt.healed else "BLOCKED"
        _echo(f"  attempt {attempt.attempt}  {marker}  {attempt.diagnosis.report}")
        if attempt.detail:
            _echo(f"           {attempt.detail[:200]}")

    _echo("")
    if run.succeeded:
        _echo(f"SUCCEEDED  {run.summary()}")
        return

    _echo(f"{'BLOCKED' if run.blocked_on_human else 'EXHAUSTED'}  {run.summary()}")
    if run.final_output:
        _echo("")
        _echo("last output:")
        for line in run.final_output.splitlines()[-10:]:
            _echo(f"  | {line}")
    raise typer.Exit(1)


@app.command("delegate")
def delegate_command(
    plan_file: Path = typer.Argument(..., help="JSON file describing the assignments"),
    runner: str = typer.Option(None, "--runner", help="claude, goose, or codex"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Write prompts and plan waves, spawn nothing"
    ),
    timeout: int = typer.Option(900, "--timeout", help="Seconds per subagent"),
) -> None:
    """Spawn scoped subagents, refusing any plan that would destroy work.

    Refusals are the feature: no overlapping file ownership, no assignment without
    a verification command, no nesting, and no trusting a completion claim without
    re-running the check.
    """
    workspace = _workspace()
    schema_help = (
        '{"assignments":[{"id":"worker","role":"builder","objective":"...",'
        '"scope":["path"],"context":[],"verify":"command","depends_on":[]}]}'
    )
    try:
        payload = json.loads(plan_file.read_text(encoding="utf-8"))
        items = payload["assignments"]
        if not isinstance(items, list) or not items:
            raise ValueError("assignments must be a non-empty array")
        assignments = [
            spawn.Assignment(
                agent_id=item["id"],
                role=spawn.Role(item.get("role", "builder")),
                objective=item["objective"],
                file_scope=item.get("scope", []),
                context_paths=item.get("context", []),
                verification=item.get("verify", ""),
                depends_on=item.get("depends_on", []),
            )
            for item in items
        ]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        _echo(f"INVALID_PLAN  {exc}")
        _echo(f"Expected schema: {schema_help}")
        raise typer.Exit(2) from exc

    invalid = [(a.agent_id, a.problems()) for a in assignments if a.problems()]
    if invalid:
        for agent_id, problems in invalid:
            _echo(f"  REFUSED  {agent_id}: {'; '.join(problems)}")
        _echo("")
        _echo(
            "Every assignment needs an objective, a verification command, and a scope if it writes."
        )
        raise typer.Exit(1)

    conflicts = spawn.check_ownership(assignments)
    if conflicts:
        for conflict in conflicts:
            _echo(f"  CONFLICT  {conflict}")
        _echo("")
        _echo("REFUSED  two subagents writing one file overwrite each other silently.")
        raise typer.Exit(1)

    chosen, reason = spawn.detect_runner(runner)
    waves = spawn.plan_waves(assignments)
    _echo(f"runner: {chosen} ({reason})")
    _echo(f"waves:  {len(waves)}")
    for index, wave in enumerate(waves, start=1):
        _echo(f"  wave {index}: {', '.join(a.agent_id for a in wave)}")
    _echo("")

    results: list[spawn.SpawnResult] = []
    for index, wave in enumerate(waves, start=1):
        _echo(f"WAVE {index}")
        for assignment in wave:
            result = spawn.spawn_one(
                assignment,
                workspace.home.root,
                workspace.project.root,
                chosen,
                timeout=timeout,
                dry_run=dry_run,
            )
            if result.outcome in {"CLAIMED", "NO_SIGNAL"}:
                # A claim is not evidence. Re-run the check ourselves.
                result = spawn.verify(result, assignment, workspace.project.root)
            results.append(result)
            verdict = (
                "verified"
                if result.trustworthy
                else ("unverified" if result.verified is None else "FAILED CHECK")
            )
            _echo(
                f"  {result.outcome:<10} {assignment.agent_id:<18} {result.duration_ms}ms  {verdict}"
            )
            if result.output_tail and result.outcome not in {"PLANNED"}:
                for line in result.output_tail.splitlines()[-4:]:
                    _echo(f"      | {line}")
            if result.outcome in {"FAILED", "TIMEOUT"}:
                # A failed spawn is diagnosed here rather than only reported, so a
                # credential problem reads as "run claude /login" instead of an
                # unexplained FAILED that leaves you guessing.
                from smith import healing

                diagnosis = healing.diagnose(result.output_tail, result.exit_code)
                if diagnosis.self_healable:
                    _echo(f"      diagnosis: {diagnosis.report}")
                else:
                    _echo(f"      diagnosis: {diagnosis.failure} -> {diagnosis.human_action}")

    trusted = [r for r in results if r.trustworthy]
    _echo("")
    _echo(f"DELEGATION  {len(trusted)}/{len(results)} independently verified")
    if dry_run:
        _echo("DRY_RUN  prompts written to .smith/assignments/, nothing spawned")
        return
    if len(trusted) != len(results):
        _echo("Unverified work is not complete work.")
        raise typer.Exit(1)


@app.command("review-doc")
def review_doc_command(
    path: Path = typer.Argument(..., help="Spec or plan document to review"),
    rubric: str = typer.Option(
        ..., "--rubric", help="'spec' (project.yaml-style) or 'plan' (thoughts/plans/*.md-style)"
    ),
    run_loop: bool = typer.Option(
        False,
        "--run-loop",
        help="Loop fix->re-review with the default scorer, capped at 5 iterations",
    ),
    run_id: str = typer.Option(
        None, "--run", help="Run id to attest against. Defaults to the current run if any."
    ),
) -> None:
    """Second-pass rubric review of a spec or plan, before implementation proceeds.

    This does not spawn a real reviewer subagent: that requires the
    orchestrating agent's Task tool, which a CLI subprocess does not have.
    A single pass (the default) scores the document with the deterministic
    rubric heuristics in ``doc_review.score_document`` and prints Issues
    Found, exiting nonzero unless the document is Approved. ``--run-loop``
    additionally loops that same scorer against the document on disk, capped
    at 5 total iterations and 3 iterations of the same recurring rubric
    category, surfacing to the human rather than looping forever. The verdict
    is attested against a run (explicit ``--run`` or the current run) when
    one exists; without a run this still runs standalone and just prints.
    """
    if rubric not in {doc_review.RubricKind.SPEC, doc_review.RubricKind.PLAN}:
        _echo(f"REFUSED  --rubric must be 'spec' or 'plan', got {rubric!r}")
        raise typer.Exit(2)
    resolved_path = path if path.is_absolute() else _workspace().project.root / path
    if not resolved_path.is_file():
        _echo(f"REFUSED  DOCUMENT_NOT_FOUND  {resolved_path}")
        raise typer.Exit(2)

    table = doc_review.RubricKind.rubric(rubric)
    _echo(f"RUBRIC  {rubric}: {', '.join(table)}")

    def _read() -> str:
        return resolved_path.read_text(encoding="utf-8")

    if run_loop:
        outcome = doc_review.run_review_loop(_read, table, doc_review.score_document)
    else:
        result = doc_review.score_document(_read(), table)
        outcome = doc_review.LoopOutcome(
            iterations=[doc_review.IterationRecord(1, result)],
            stopped_reason="single pass" if result.approved else "single pass, issues found",
            surfaced_to_human=False,
        )

    final = outcome.final
    for record in outcome.iterations:
        result = record.result
        _echo(f"ITERATION {record.iteration}  verdict={result.verdict}")
        for issue in result.issues:
            _echo(f"  ! [{issue.category}] {issue.detail}")

    _echo("")
    _echo(f"STOPPED  {outcome.stopped_reason}")

    resolved_run_id = run_id or _ledger().current_id()
    if resolved_run_id:
        try:
            doc_review.attest_review(_ledger(), resolved_run_id, outcome, resolved_path)
            _echo(f"ATTESTED  run={resolved_run_id}")
        except LedgerError as exc:
            _echo(f"REFUSED  {exc}")
            raise typer.Exit(1) from exc

    if outcome.surfaced_to_human:
        _echo("REFUSED  cap reached; a human must decide how to proceed.")
        raise typer.Exit(1)
    if final is None or not final.approved:
        _echo("REFUSED  Issues Found; re-run after fixing, or pass --run-loop.")
        raise typer.Exit(1)
    _echo("APPROVED")


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


@app.command()
def registry_json() -> None:
    """Dump the registry index as JSON for programmatic routing."""
    _echo(json.dumps(KnowledgeStore(_paths()).registry(), indent=2))


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


def _start_linked_issue(ledger: Ledger, run_id: str) -> None:
    run = ledger.load(run_id)
    if run.issue_id is None or run.issue_started_at is not None:
        return
    result = seeds.Seeds(_workspace().project.root).start(run.issue_id)
    if not result.ok:
        raise LedgerError(f"could not start issue {run.issue_id}: {result.detail}")
    run.issue_started_at = datetime.now(UTC).isoformat()
    ledger.save(run)


@gate_app.command("open")
def gate_open(
    task_class: TaskClass = typer.Argument(..., help="What kind of work this is"),
    objective: str = typer.Argument(..., help="One sentence describing the goal"),
    scope: list[str] = typer.Option(None, "--scope", help="File this run may write. Repeatable."),
    also: list[Gate] = typer.Option(
        None, "--also", help="Extra gate beyond the contract. Repeatable."
    ),
    plan: Path = typer.Option(None, "--plan", help="Plan file reviewed before execution."),
    issue: str = typer.Option(None, "--issue", help="Open Seeds issue served by this run."),
) -> None:
    """Open a run and print the gates it must satisfy before it can close."""
    required = list(CONTRACTS[task_class]) + list(also or [])
    if Gate.PLANNED in required and plan is None:
        _echo("PLAN_REQUIRED  schema v2 runs containing the planned gate require --plan")
        raise typer.Exit(2)
    plan_path = None
    if plan is not None:
        plan_path = plan if plan.is_absolute() else _workspace().project.root / plan
        plan_path = plan_path.resolve()
        if not plan_path.is_file():
            _echo(f"PLAN_NOT_FOUND  {plan_path}")
            raise typer.Exit(2)
    intent = onboarding.load(_workspace().project.root)
    if intent and intent.source == "confirmed" and intent.workflow.issue_required:
        if not issue:
            _echo("ISSUE_REQUIRED  this project's workflow requires an issue ID")
            raise typer.Exit(2)
        if (
            intent.workflow.issue_pattern
            and re.fullmatch(intent.workflow.issue_pattern, issue) is None
        ):
            _echo(f"ISSUE_FORMAT  {issue!r} does not match {intent.workflow.issue_pattern!r}")
            raise typer.Exit(2)
    linked_issue = _validate_issue(issue) if issue else None
    ledger = _ledger()
    run = ledger.open(
        task_class,
        objective,
        file_scope=list(scope or []),
        extra_gates=list(also or []),
        plan_path=plan_path,
        issue_id=linked_issue.id if linked_issue else None,
    )
    if intent and intent.bootstrap:
        ledger.append_artifact(
            run.run_id,
            "bootstrap",
            intent.bootstrap.confirmed_by or "unknown",
            asdict(intent.bootstrap),
        )
    if intent and intent.source == "confirmed":
        try:
            session_state.bind_run(
                _workspace().state_root,
                run.run_id,
                enforce_one_task=intent.workflow.one_task_per_session,
            )
        except RuntimeError as exc:
            _echo(f"NEW_SESSION_REQUIRED  {exc}")
            raise typer.Exit(2) from exc
    _echo(f"RUN {run.run_id}  class={run.task_class}")
    _echo(f"objective: {run.objective}")
    if run.file_scope:
        _echo(f"scope: {', '.join(run.file_scope)}")
    if run.plan_path:
        _echo(f"plan: {run.plan_path}")
    if run.issue_id:
        _echo(f"issue: {run.issue_id}")
    if not run.required:
        _echo("gates: none for this class")
        return
    _echo("gates required before close:")
    for gate in run.required:
        _echo(f"  [ ] {gate}")
    _echo("")
    _echo('Record each with: awino gate record <gate> --cmd "<command>"')


def _plan_decision(decision: PlanDecision, run_id: str | None, by: str, reason: str) -> None:
    resolved = _resolve_run(run_id)
    try:
        item = _ledger().decide_plan(resolved, decision, by, reason)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"PLAN_{decision.upper()}  {item.plan_path}")
    _echo(f"sha256: {item.plan_sha256}")
    _echo(f"scope: {', '.join(item.approved_scope) or '(none)'}")


@gate_plan_app.command("approve")
def gate_plan_approve(
    by: str = typer.Option(..., "--by", help="Person approving the plan."),
    reason: str = typer.Option("", "--reason"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Approve the exact current plan bytes and file scope."""
    _plan_decision(PlanDecision.APPROVED, run_id, by, reason)


@gate_plan_app.command("hold")
def gate_plan_hold(
    by: str = typer.Option(..., "--by"),
    reason: str = typer.Option("", "--reason"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Hold a plan until its concerns are resolved."""
    _plan_decision(PlanDecision.HELD, run_id, by, reason)


@gate_plan_app.command("reject")
def gate_plan_reject(
    by: str = typer.Option(..., "--by"),
    reason: str = typer.Option("", "--reason"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Reject the current plan."""
    _plan_decision(PlanDecision.REJECTED, run_id, by, reason)


@gate_plan_app.command("status")
def gate_plan_status(run_id: str = typer.Option(None, "--run")) -> None:
    """Show whether the current plan approval remains valid."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.load(resolved)
        problems = ledger.validate_plan(resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    latest = run.plan_decisions[-1] if run.plan_decisions else None
    _echo(f"PLAN  {run.plan_path or '(none)'}")
    _echo(f"decision: {latest.decision if latest else 'none'}")
    _echo("VALID" if not problems else f"INVALID  {'; '.join(problems)}")


@gate_app.command("score")
def gate_score(run_id: str = typer.Option(None, "--run")) -> None:
    """Print an advisory score computed only from recorded ledger evidence."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    run = ledger.load(resolved)
    evidence = ledger.evidence(resolved)
    verdict = adjudicate(run, evidence)
    score = score_run(run, evidence, verdict)
    _echo(f"SCORE  {score.total}  grade={score.grade}")
    if not score.items:
        _echo("  no scoreable evidence recorded")
        return
    for item in score.items:
        _echo(f"  {item.points:+4d}  {item.name:<25} {item.evidence}")
    _echo("NOTE   advisory only; gate close remains the completion authority")


@gate_app.command("checkpoint")
def gate_checkpoint(
    phase: str = typer.Option(..., "--phase"),
    summary: str = typer.Option(..., "--summary"),
    next_action: str = typer.Option(..., "--next"),
    pending: str = typer.Option(None, "--pending", help="Decision requiring human input."),
    option: list[str] = typer.Option(None, "--option", help="Allowed decision. Repeatable."),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Persist resumable continuation state for a run."""
    resolved = _resolve_run(run_id)
    try:
        item = _ledger().checkpoint(
            resolved,
            phase,
            summary,
            next_action,
            pending_decision=pending,
            options=list(option or []),
        )
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"CHECKPOINT  {item.checkpoint_id} phase={item.phase}")
    if item.pending_decision:
        _echo(f"pending: {item.pending_decision}")
        _echo(f"options: {', '.join(item.options)}")


@gate_app.command("decide")
def gate_decide(
    selection: str = typer.Argument(..., help="One declared checkpoint option."),
    by: str = typer.Option(..., "--by"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Resolve the current checkpoint decision."""
    resolved = _resolve_run(run_id)
    try:
        item = _ledger().resolve_checkpoint(resolved, selection, by)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"DECIDED  {item.checkpoint_id}  {selection}  by={by}")


@gate_app.command("record")
def gate_record(
    gate: Gate = typer.Argument(..., help="Which gate this evidence satisfies"),
    cmd: str = typer.Option(None, "--cmd", help="Command to execute and record"),
    attest: str = typer.Option(None, "--attest", help="Record a non-command gate with a note"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Execute a command and record its real exit code against a gate."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    if bool(cmd) == bool(attest):
        _echo("Provide exactly one of --cmd or --attest")
        raise typer.Exit(2)

    try:
        _require_valid_plan(ledger, resolved)
        if attest:
            item = ledger.attest(resolved, gate, attest)
        else:
            _start_linked_issue(ledger, resolved)
            item = ledger.record(resolved, gate, cmd, cwd=_workspace().project.root)
    except LedgerError as exc:
        _ledger_error(exc)

    if attest:
        _echo(f"ATTESTED  {gate}  {attest}")
        _echo("Attestations are weaker than executed evidence and are reported as such.")
        return

    verdict = "PASS" if item.passed else "FAIL"
    _echo(f"{verdict}  {gate}  exit={item.exit_code}  {item.duration_ms}ms  attempt={item.attempt}")
    _echo(f"command: {item.command}")
    tail = item.output_head.strip().splitlines()[-12:]
    for line in tail:
        _echo(f"  | {line}")
    if not item.passed:
        _echo("")
        _echo(
            f"Gate not satisfied. Fix the cause, then record again (attempt {item.attempt + 1} of 3)."
        )
        raise typer.Exit(1)


@gate_app.command("record-completeness")
def gate_record_completeness(
    achieved: int = typer.Option(..., "--achieved", help="Units actually produced"),
    stated: int = typer.Option(..., "--stated", help="Units the objective claims"),
    unit: str = typer.Option("unit(s)", "--unit", help="What is being counted"),
    accept_reduced_scope: bool = typer.Option(
        False, "--accept-reduced-scope", help="A human explicitly accepts achieved < stated"
    ),
    by: str = typer.Option(None, "--by", help="Who accepted the reduced scope"),
    reason: str = typer.Option(None, "--reason", help="Why the reduced scope is acceptable"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Record achieved-vs-stated counts. close() refuses if achieved < stated.

    A partial deliverable labeled complete is a failed task, not a partial
    success. This exists so that gap cannot be closed by report alone -
    only by a human explicitly recording acceptance of the reduced scope.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        record = ledger.record_completeness(
            resolved,
            achieved=achieved,
            stated=stated,
            unit=unit,
            accept_reduced_scope=accept_reduced_scope,
            accepted_by=by,
            accepted_reason=reason,
        )
    except LedgerError as exc:
        _ledger_error(exc)

    if record.satisfied:
        if record.accepted_reduced_scope:
            _echo(
                f"COMPLETENESS_ACCEPTED  {record.achieved}/{record.stated} {record.unit}  "
                f"reduced scope accepted by {record.accepted_by}: {record.accepted_reason}"
            )
        else:
            _echo(f"COMPLETENESS_MET  {record.achieved}/{record.stated} {record.unit}")
    else:
        _echo(f"DELIVERABLE_INCOMPLETE  {record.achieved}/{record.stated} {record.unit} achieved")
        _echo(
            "A partial deliverable is a failed task, not a partial success. "
            "gate close will refuse until either the remaining units are produced "
            "or a human records --accept-reduced-scope with --by and --reason."
        )
        raise typer.Exit(1)


@gate_app.command("check")
def gate_check(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
    diff_base: str = typer.Option(
        None, "--diff-base", help="Git ref to diff against for independent checks"
    ),
) -> None:
    """Run the independent checks that do not trust the agent's word."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.load(resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    root = _workspace().project.root
    problems = 0

    if diff_base:
        diff_result = subprocess.run(
            ["git", "diff", diff_base, "--"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            check=False,
        )
        names_result = subprocess.run(
            ["git", "diff", "--name-only", diff_base, "--"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            check=False,
        )
        if diff_result.returncode != 0 or names_result.returncode != 0:
            # git failed (no repo, bad ref, etc). An empty diff from a failed
            # command is not evidence of "no weakening" — reporting it as such
            # would silently pass a check that never actually ran.
            problems += 1
            _echo(
                "GIT_DIFF_FAILED  could not diff against "
                f"{diff_base!r}: {(diff_result.stderr or names_result.stderr).strip()}"
            )
            _echo(
                "  Not a git repo, or the ref does not exist. Use --attest instead of --diff-base."
            )
        else:
            diff = diff_result.stdout
            names = names_result.stdout.split()
            weakened = detect_test_weakening(diff)
            if weakened:
                problems += 1
                _echo(f"TESTS_WEAKENED  {len(weakened)} finding(s):")
                for finding in weakened:
                    _echo(f"  ! {finding}")
                _echo("  Fix the code, never the test.")
            else:
                ledger.attest(
                    resolved, Gate.TESTS_NOT_WEAKENED, f"diff vs {diff_base} shows no weakening"
                )
                _echo("TESTS_NOT_WEAKENED  ok")

            violations = detect_scope_violations(names, run.file_scope)
            if violations:
                problems += 1
                _echo(f"SCOPE_VIOLATION  {len(violations)} file(s) outside declared scope:")
                for path in violations:
                    _echo(f"  ! {path}")
            elif run.file_scope:
                ledger.attest(
                    resolved, Gate.SCOPE_RESPECTED, f"{len(names)} changed files all within scope"
                )
                _echo("SCOPE_RESPECTED  ok")
    else:
        _echo("SKIP  pass --diff-base to enable weakening and scope checks")

    if problems:
        raise typer.Exit(1)


@gate_app.command("review")
def gate_review(
    verdict: ReviewVerdict = typer.Option(
        ..., "--verdict", help="approved, changes-requested, or blocked"
    ),
    risks: str = typer.Option(None, "--risks", help="Remaining risk(s), free text. Optional."),
    diff_base: str = typer.Option(
        None, "--diff-base", help="Git ref to diff against for scope/weakening checks"
    ),
    skip_toolchain: bool = typer.Option(
        False, "--skip-toolchain", help="Skip running detected test/lint commands"
    ),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Independent completion review, run before ``gate close``.

    Extracts acceptance criteria from the objective and linked Seed (if any),
    runs the project's own test/lint commands as real evidence, reuses the
    existing scope and test-weakening checks against a diff, and runs
    ``tidy --dry-run`` read-only. The result is recorded as a ProvenanceRecord;
    task classes that require the REVIEWED gate will not close without one.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    root = _workspace().project.root
    try:
        run = ledger.load(resolved)
        _require_valid_plan(ledger, resolved)
    except LedgerError as exc:
        _ledger_error(exc)

    seed_description = ""
    if run.issue_id:
        tracker = seeds.Seeds(root)
        state, _reason = tracker.state()
        if state.usable:
            issue = tracker.show(run.issue_id)
            if issue is not None:
                seed_description = issue.description

    try:
        report = completion_review.review_run(
            ledger,
            run,
            root,
            seed_description=seed_description,
            diff_base=diff_base,
            run_toolchain=not skip_toolchain,
        )
    except RuntimeError as exc:
        _echo(f"REFUSED  GIT_DIFF_FAILED  {exc}")
        raise typer.Exit(1) from exc

    if report.criteria:
        _echo(f"ACCEPTANCE CRITERIA  {len(report.criteria)} found in objective/Seed description")
        for item in report.criteria:
            _echo(f"  - {item.text}")
    else:
        _echo("ACCEPTANCE CRITERIA  none found, skipped")

    if report.toolchain_results:
        _echo("")
        _echo("TOOLCHAIN VERIFICATION")
        for item in report.toolchain_results:
            mark = "PASS" if item.passed else "FAIL"
            _echo(f"  {mark}  {item.gate:<10} exit={item.exit_code}  {item.command}")

    if diff_base:
        _echo("")
        if report.test_weakening:
            _echo(f"TESTS_WEAKENED  {len(report.test_weakening)} finding(s):")
            for finding in report.test_weakening:
                _echo(f"  ! {finding}")
        else:
            _echo("TESTS_NOT_WEAKENED  ok")
            ledger.attest(
                resolved,
                Gate.TESTS_NOT_WEAKENED,
                f"gate review: diff vs {diff_base} shows no weakening",
            )
        if report.scope_violations:
            _echo(
                f"SCOPE_VIOLATION  {len(report.scope_violations)} file(s) outside declared scope:"
            )
            for path in report.scope_violations:
                _echo(f"  ! {path}")
        elif run.file_scope:
            _echo("SCOPE_RESPECTED  ok")
            ledger.attest(
                resolved,
                Gate.SCOPE_RESPECTED,
                f"gate review: {len(report.changed_files)} changed files all within scope",
            )

    _echo("")
    if report.tidy_findings:
        _echo(f"TIDY FINDINGS  {len(report.tidy_findings)} (dry-run, nothing changed)")
        for item in report.tidy_findings:
            rel = (
                item.clutter.path.relative_to(root)
                if root in item.clutter.path.parents
                else item.clutter.path
            )
            level = "BLOCKING" if item.blocking else "WARN"
            _echo(f"  {level:<8} {item.clutter.kind:<18} {rel}  ({item.clutter.detail})")
    else:
        _echo("TIDY FINDINGS  none")

    if not report.can_record:
        _echo("")
        reasons = []
        if report.test_weakening:
            reasons.append("test weakening detected")
        if report.scope_violations:
            reasons.append("scope violation detected")
        if report.blocking_tidy_findings:
            reasons.append(f"{len(report.blocking_tidy_findings)} in-scope clutter finding(s)")
        _echo(f"REFUSED  REVIEW_BLOCKED  {'; '.join(reasons)}")
        raise typer.Exit(1)

    gate_results = completion_review.build_provenance(report)
    record = ledger.record_provenance(
        resolved,
        verdict=verdict,
        gate_results=gate_results,
        changed_files=report.changed_files,
        risks=risks,
    )
    ledger.attest(resolved, Gate.REVIEWED, f"gate review verdict={verdict}")

    _echo("")
    _echo(f"REVIEWED  verdict={record.verdict}")
    _echo(f"  changed_files={len(record.changed_files)}  risks={record.risks or 'none'}")
    if verdict != ReviewVerdict.APPROVED:
        _echo(f"NOTE  verdict is {verdict}; gate close will still require every gate to pass.")


@gate_app.command("close")
def gate_close(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Attempt to close a run. Refuses unless every gate is satisfied by evidence."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.load(resolved)
        _require_valid_plan(ledger, resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    if Gate.REVIEWED in run.required and run.provenance is None:
        _echo("")
        _echo("REFUSED  REVIEW_REQUIRED: run 'gate review' first")
        raise typer.Exit(1)
    verdict = adjudicate(run, ledger.evidence(resolved))

    _echo(f"RUN {verdict.run_id}  class={verdict.task_class}")
    _echo(f"OBJECTIVE (stated at open): {run.objective}")
    for gate in run.required:
        if gate in verdict.satisfied:
            mark = "attested" if gate in verdict.attested_only else "executed"
            _echo(f"  [x] {gate}  ({mark})")
        elif gate in verdict.failing:
            _echo(f"  [!] {gate}  FAILING")
        else:
            _echo(f"  [ ] {gate}  no evidence")

    if run.completeness is not None:
        c = run.completeness
        mark = "x" if c.satisfied else "!"
        _echo(f"  [{mark}] completeness  {c.achieved}/{c.stated} {c.unit}")

    if not verdict.can_close:
        _echo("")
        _echo(f"REFUSED  {verdict.blocked_reason}")
        _echo("You may not report this work as complete.")
        raise typer.Exit(1)

    if run.issue_id:
        tracker = seeds.Seeds(_workspace().project.root)
        state, reason = tracker.state()
        if not state.usable:
            _echo("")
            _echo(f"REFUSED  SEED_UNAVAILABLE  linked issue {run.issue_id} but {reason}")
            _echo("Run 'awino work-close' first, or resolve the tracker, then retry close.")
            raise typer.Exit(1)
        issue = tracker.show(run.issue_id)
        if issue is None or issue.open:
            _echo("")
            status = issue.status if issue is not None else "not found"
            _echo(
                f"REFUSED  SEED_NOT_CLOSED  linked issue {run.issue_id} is still {status}. "
                "Gate evidence is satisfied, but a Seed left open is a lie that survives "
                "in git history."
            )
            _echo(f"Run 'awino work-close' first to close {run.issue_id}, then retry close.")
            raise typer.Exit(1)

    ledger.mark_complete(resolved)
    run = ledger.load(resolved)
    _echo("")
    _echo(f"COMPLETE  {len(verdict.satisfied)} gate(s) satisfied")
    _echo(
        f"Restate the human's exact ask and confirm the pasted evidence above answers it, "
        f'not an adjacent one: "{run.objective}"'
    )
    if verdict.attested_only:
        _echo(f"NOTE  attested rather than executed: {', '.join(verdict.attested_only)}")


@gate_app.command("block")
def gate_block(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Mark a run BLOCKED. Requires a recorded failing gate and a checkpoint

    with an unresolved pending decision (record one first with
    'gate checkpoint --pending ... --option ...'). This does not create the
    decision point itself - it only certifies that both halves of a genuine
    block already exist, so a run cannot be waved to BLOCKED with only a
    failure or only a question.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.mark_blocked(resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"BLOCKED  {run.run_id}")
    pending = next(
        (
            item
            for item in reversed(run.checkpoints)
            if item.pending_decision is not None and item.selected_decision is None
        ),
        None,
    )
    if pending is not None:
        _echo(f"pending: {pending.pending_decision}")
        _echo(f"options: {', '.join(pending.options)}")


@gate_app.command("pause")
def gate_pause(
    by: str = typer.Option(..., "--by", help="The human pausing this run. Required."),
    reason: str = typer.Option(..., "--reason", help="Why this run is being paused."),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Explicitly pause a run. A human decision, never an automatic side effect.

    This is a distinct command on purpose: no other command may set PAUSED as
    a side effect, and this one refuses without a named human.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.pause(resolved, by, reason)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"PAUSED  {run.run_id}  by={by}")
    _echo(f"reason: {reason}")


@gate_app.command("status")
def gate_status(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Show gate progress for a run without attempting to close it."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.load(resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    evidence = ledger.evidence(resolved)
    verdict = adjudicate(run, evidence)
    _echo(f"RUN {run.run_id}  class={run.task_class}  objective: {run.objective}")
    _echo(f"terminal_state: {run.terminal_state or 'active'}")
    _echo(f"skills loaded: {', '.join(run.skills_loaded) or 'none recorded'}")
    _echo(
        f"satisfied={len(verdict.satisfied)} missing={len(verdict.missing)} failing={len(verdict.failing)}"
    )
    for item in evidence:
        flag = "ok " if item.passed else "FAIL"
        _echo(
            f"  {flag} {item.gate:<20} attempt={item.attempt} exit={item.exit_code}  {item.command[:60]}"
        )
    if not verdict.can_close:
        _echo(f"BLOCKED  {verdict.blocked_reason}")


@gate_app.command("skill")
def gate_skill(
    name: str = typer.Argument(..., help="Canonical skill name or legacy alias"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
    state: str = typer.Option("loaded", "--state", help="Truthful state: loaded or used"),
    reason: str = typer.Option("", "--reason", help="Why the skill was loaded or used"),
) -> None:
    """Validate and persist truthful skill loading or usage evidence."""
    if state not in {"loaded", "used"}:
        _echo("REFUSED  --state must be loaded or used")
        raise typer.Exit(2)
    resolution = _skill_catalog().resolve(name)
    if resolution is None:
        _echo(f"REFUSED  unknown skill {name!r}; inspect canonical names with: awino skills")
        raise typer.Exit(2)
    if resolution.deprecated_alias:
        typer.echo(
            f"DEPRECATED_SKILL_ALIAS  {name} -> {resolution.skill.name}",
            err=True,
        )
    resolved = _resolve_run(run_id)
    try:
        run = _ledger().note_skill(resolved, resolution.skill.name, state=state, reason=reason)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"SKILL_{state.upper()}  {resolution.skill.name}  (total {len(run.skills_loaded)})")


@gate_app.command("contracts")
def gate_contracts() -> None:
    """Show which gates each task class requires."""
    for task_class, gates in CONTRACTS.items():
        _echo(f"{task_class}:")
        for gate in gates or ():
            _echo(f"  - {gate}")
        if not gates:
            _echo("  (none)")


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


if __name__ == "__main__":
    app()
