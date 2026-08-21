"""Agent Smith command line interface.

Every command here is deterministic. The model calls these rather than doing the
work in prose, which is the ``MODEL_DOES_DETERMINISM`` guard applied to Smith.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from smith import capability, fix, harness, health, mission, models, modes, seeds, spawn
from smith.enforce import (
    CONTRACTS,
    Gate,
    Ledger,
    TaskClass,
    adjudicate,
    detect_scope_violations,
    detect_test_weakening,
)
from smith.knowledge import BudgetExceeded, KnowledgeStore
from smith.paths import SmithPaths, Workspace
from smith.tidy import Finding, Tidier
from smith.toolchain import Toolchain
from smith.validate import BROKEN_SELFTEST, Status, discover, validate_file, validate_text

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Agent Smith: knowledge harness, artifact validation, and folder hygiene.",
)
gate_app = typer.Typer(
    no_args_is_help=True, help="Run ledger. Completion is computed, never claimed."
)
app.add_typer(gate_app, name="gate")


def _paths() -> SmithPaths:
    return SmithPaths.discover()


def _workspace() -> Workspace:
    return Workspace.discover()


def _toolchain(workspace: Workspace | None = None) -> Toolchain:
    """Toolchain of the project under work, not of Smith."""
    return Toolchain((workspace or _workspace()).project.root)


def _echo(message: str) -> None:
    typer.echo(message)


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
        _echo("NO_ROUTE  no registry match. Run 'smith update' in case upstream added a chapter.")
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
    _echo(f"budget: {store.budget} files per task")


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
    """Find clutter and archive it. Archiving is reversible; deleting is not.

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
    """Delete disposable artifacts. Cache is disposable; memory never is."""
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
    home = Path.home()
    plugin_link = home / ".agents" / "plugins" / "agent-smith"
    agent_target = home / ".agents" / "agents" / "agent-smith.md"
    agent_source = paths.agents / "agent-smith.md"

    _echo(f"plugin: {plugin_link} -> {paths.root}")
    _echo(f"agent:  {agent_target} <- {agent_source}")
    if dry_run:
        _echo("DRY_RUN  nothing changed")
        return

    plugin_link.parent.mkdir(parents=True, exist_ok=True)
    agent_target.parent.mkdir(parents=True, exist_ok=True)
    if plugin_link.exists() or plugin_link.is_symlink():
        _echo("SKIP  plugin link already exists")
    else:
        try:
            plugin_link.symlink_to(paths.root, target_is_directory=True)
            _echo("LINKED  plugin")
        except OSError as exc:
            _echo(f"FAILED  symlink needs Developer Mode or admin on Windows: {exc}")
            raise typer.Exit(1) from exc

    agent_target.write_text(agent_source.read_text(encoding="utf-8"), encoding="utf-8")
    _echo("INSTALLED  persona")
    _echo("Next: start a session and ask '@agent-smith what is a harness?'")


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
    expect the persona somewhere different, so Smith adapts instead of demanding
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

    _echo(f"smith home: {smith_home}")
    _echo(f"project:    {workspace.project.root}")
    _echo("")

    if not chosen:
        _echo(f"No {scope_wanted} harness directories found. Candidates:")
        for target in targets:
            _echo(f"  {target.describe()}")
        _echo("")
        _echo("Create one, or name it explicitly:")
        _echo("  smith install --harness claude")
        raise typer.Exit(1)

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

    if pointer and not dry_run:
        marker = "## Agent Smith"
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = workspace.project.root / name
            if not path.is_file():
                continue
            body = path.read_text(encoding="utf-8")
            if marker in body:
                _echo(f"POINTER    {name} already references Smith")
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

    _echo("")
    _echo("Verify with:  smith install-status")
    _echo("Then ask your agent:  what is a harness?")


@app.command("install-mode")
def install_mode_command(
    editor: str = typer.Option(None, "--editor", help="kilo, roo, or zoo. Default: all detected."),
    scope: str = typer.Option("global", "--scope", help="global or project"),
    project: bool = typer.Option(False, "--project", help="Shorthand for --scope project"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing Smith mode"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
    emit_json: bool = typer.Option(False, "--json", help="Print the mode definitions instead"),
) -> None:
    """Install Smith as a selectable mode in Kilo, Roo, or a fork.

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

    _echo(f"smith home: {smith_home}")
    _echo("")

    if not chosen:
        _echo(f"No {scope_wanted} editor found. Candidates:")
        for target in targets:
            _echo(f"  {target.describe()}")
        _echo("")
        _echo("Name one explicitly:  smith install-mode --editor kilo")
        raise typer.Exit(1)

    built = modes.build_modes(smith_home)
    invalid = [(m.slug, m.validate()) for m in built if m.validate()]
    if invalid:
        for slug, problems in invalid:
            _echo(f"INVALID  {slug}: {'; '.join(problems)}")
        raise typer.Exit(1)

    for target in chosen:
        _echo(f"TARGET  {target.describe()}")
        for mode in built:
            if dry_run:
                _echo(f"  would add  {mode.slug:<22} groups={mode.groups}")
                continue
            outcome, detail = modes.install(mode, target, force=force)
            _echo(f"  {outcome:<10} {mode.slug:<22} {detail}")

    if dry_run:
        _echo("")
        _echo("DRY_RUN  nothing changed")
        return

    _echo("")
    _echo("Reload the editor window, then pick the mode from the selector.")
    _echo("Verify with:  smith mode-status")


@app.command("mode-status")
def mode_status_command() -> None:
    """Show where the Smith modes are installed."""
    workspace = _workspace()
    rows = modes.status(workspace.project.root, "agent-smith")
    installed = [t for t, present in rows if present]

    if installed:
        _echo("INSTALLED")
        for target in installed:
            _echo(f"  {target.label:<14} {target.scope:<8} {target.path}")
    else:
        _echo("No Smith mode installed. Run: smith install-mode")

    absent = [t for t, present in rows if not present and (t.exists or t.parent_exists)]
    if absent:
        _echo("")
        _echo("EDITOR PRESENT BUT MODE ABSENT")
        for target in absent:
            _echo(f"  {target.label:<14} {target.scope:<8} {target.path}")
        _echo("")
        _echo("  smith install-mode")


@app.command("install-status")
def install_status_command() -> None:
    """Show where Smith is installed and where it is not."""
    workspace = _workspace()
    rows = harness.status(workspace.project.root)
    installed = [r for r in rows if r[1]]

    _echo(f"smith home: {workspace.home.root}")
    _echo("")
    if installed:
        _echo("INSTALLED")
        for target, _, detail in installed:
            _echo(f"  {target.harness.label:<22} {target.scope:<8} {detail}")
    else:
        _echo("Not installed anywhere. Run: smith install")

    missing = [r for r in rows if not r[1] and r[0].exists]
    if missing:
        _echo("")
        _echo("HARNESS PRESENT BUT SMITH ABSENT")
        for target, _, _ in missing:
            _echo(f"  {target.harness.label:<22} {target.scope:<8} {target.root}")
        _echo("")
        _echo("  smith install")


@app.command("pointer")
def pointer_command() -> None:
    """Print the AGENTS.md block that makes Smith discoverable in a project."""
    _echo(harness.pointer_text(_workspace().home.root))


@app.command()
def scaffold() -> None:
    """Create any missing directories Smith expects."""
    created = _paths().ensure_scaffold()
    for path in created:
        _echo(f"  created {path}")
    _echo(f"SCAFFOLD  {len(created)} directories created")


@app.command()
def hook() -> None:
    """SessionStart staleness verdict. Wired from hooks/hooks.json."""
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except (OSError, ValueError):
        pass
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
        _echo(f"[agent-smith] cache cold, fetch on demand | lessons: {lessons}")
        return
    verdict = "STALE, run 'just update'" if age >= store.stale_days else "fresh"
    _echo(
        f"[agent-smith] knowledge {age}d old ({verdict}) | cached: {len(store.manifest.entries)} | lessons: {lessons}"
    )


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

    if plan.anti_patterns:
        _echo("ANTI-PATTERNS")
        for hit in plan.anti_patterns:
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
) -> None:
    """List every skill with its absolute path.

    This is how Smith learns where its own skills live at load time rather than
    hardcoding a directory that moves when the install location changes.
    """
    paths = _paths()
    found = []
    for skill in sorted(paths.skills.glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        name_match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        desc_match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        found.append(
            {
                "name": (name_match.group(1).strip() if name_match else skill.parent.name),
                "namespaced": f"agent-smith:{name_match.group(1).strip() if name_match else skill.parent.name}",
                "path": str(skill),
                "description": (desc_match.group(1).strip() if desc_match else ""),
            }
        )

    if as_json_output:
        _echo(json.dumps({"root": str(paths.root), "count": len(found), "skills": found}, indent=2))
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
        _echo(f"  {item['name']:<{width}}  {summary}")
    _echo("")
    _echo("Load one by name in a session, or record usage with: smith gate skill <name>")


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
                _ledger().record(run_id, Gate.REVIEWED, "uv run smith doctor --fast")
            else:
                _ledger().attest(run_id, Gate.REVIEWED, note)
            _echo(f"RECORDED  {note}")

    if failing:
        _echo("")
        _echo(f"REFUSED  {len(failing)} gate(s) failing: {', '.join(r.name for r in failing)}")
        raise typer.Exit(1)


@app.command("work")
def work_command(
    verify_only: bool = typer.Option(False, "--verify", help="Only issues that describe a check"),
    limit: int = typer.Option(15, "--limit", help="How many to show"),
) -> None:
    """Show tracked work that is ready to start, from the project's own tracker.

    Seeds is optional. When absent this reports that and stops, rather than
    inventing a worklist Smith would then be the only one aware of.
    """
    workspace = _workspace()
    tracker = seeds.Seeds(workspace.project.root)
    state, reason = tracker.state()

    _echo(f"project: {workspace.project.root}")
    _echo(f"tracker: {state} ({reason})")

    if not state.usable:
        _echo("")
        _echo("No tracker, so work is untracked. Smith will not create one unasked.")
        _echo(f"  install: {seeds.INSTALL_HINT}")
        _echo(f"  init:    {seeds.INIT_HINT}   (or: smith work-init --confirm)")
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
    _echo(f'  smith gate open code-change "<objective>" --issue {issues[0].id}')


@app.command("work-init")
def work_init_command(
    confirm: bool = typer.Option(False, "--confirm", help="Skip the prompt and initialize"),
    no_input: bool = typer.Option(
        False, "--no-input", help="Never prompt, for non-interactive use"
    ),
) -> None:
    """Offer to initialize a seeds tracker, then do it if the human agrees.

    Refusing silently leaves the user stuck. Acting silently mutates a repository
    Smith may not own. So Smith states exactly what would change, asks, and abides
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
        _echo("Smith does not install global tooling on your behalf.")
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
    _echo("Next: smith work")


@app.command("work-close")
def work_close_command(
    issue_id: str = typer.Argument(..., help="Issue to close"),
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
    if resolved:
        run = ledger.load(resolved)
        evidence = ledger.evidence(resolved)
        verdict = adjudicate(run, evidence)

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

    Smith never invents a mission. An agent acting confidently on a fabricated
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
    _echo("HOW SMITH WILL CALIBRATE")
    for item in found.advice():
        _echo(f"  - {item}")


@app.command("limits")
def limits_command(
    claims: bool = typer.Option(False, "--claims", help="Audit documented claims against reality"),
    strict: bool = typer.Option(
        False, "--strict", help="Exit nonzero if any documented claim is false"
    ),
) -> None:
    """Report what Smith can actually do, probed rather than claimed.

    This exists because of a real failure: the persona said Smith "spawns scoped
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
    payload = json.loads(plan_file.read_text(encoding="utf-8"))
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
        for item in payload["assignments"]
    ]

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


@app.command()
def context() -> None:
    """Show what Smith considers home, what it considers the project, and the toolchain.

    Run this first in an unfamiliar repository. Every gate command Smith records
    comes from this resolution, so a wrong answer here makes every later green gate
    meaningless.
    """
    workspace = _workspace()
    chain = _toolchain(workspace)

    _echo("IDENTITY")
    _echo(f"  smith home    {workspace.home.root}")
    _echo(f"  project       {workspace.project.root}")
    _echo(
        f"  layout        {'working on Smith itself' if workspace.working_on_self else workspace.describe()}"
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


@app.command()
def setup(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the command without running it"),
) -> None:
    """Install the project's dependencies using whatever manager it already uses.

    Smith does not impose uv. It reproduces a committed lockfile when one exists,
    respects an active environment when one is present, and falls back to pip only
    when nothing better is declared.
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
    """Ledger for the project under work, never for Smith home.

    Runs are project history. A shared Smith install that accumulated every
    project's runs would make attribution impossible.
    """
    workspace = _workspace()
    workspace.ensure_state()
    return Ledger(workspace.state_root)


def _resolve_run(run_id: str | None) -> str:
    ledger = _ledger()
    resolved = run_id or ledger.current_id()
    if not resolved:
        _echo('NO_RUN  open one first: smith gate open <task-class> "<objective>"')
        raise typer.Exit(2)
    return resolved


@gate_app.command("open")
def gate_open(
    task_class: TaskClass = typer.Argument(..., help="What kind of work this is"),
    objective: str = typer.Argument(..., help="One sentence describing the goal"),
    scope: list[str] = typer.Option(None, "--scope", help="File this run may write. Repeatable."),
    also: list[Gate] = typer.Option(
        None, "--also", help="Extra gate beyond the contract. Repeatable."
    ),
) -> None:
    """Open a run and print the gates it must satisfy before it can close."""
    run = _ledger().open(
        task_class, objective, file_scope=list(scope or []), extra_gates=list(also or [])
    )
    _echo(f"RUN {run.run_id}  class={run.task_class}")
    _echo(f"objective: {run.objective}")
    if run.file_scope:
        _echo(f"scope: {', '.join(run.file_scope)}")
    if not run.required:
        _echo("gates: none for this class")
        return
    _echo("gates required before close:")
    for gate in run.required:
        _echo(f"  [ ] {gate}")
    _echo("")
    _echo('Record each with: smith gate record <gate> --cmd "<command>"')


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

    if attest:
        item = ledger.attest(resolved, gate, attest)
        _echo(f"ATTESTED  {gate}  {attest}")
        _echo("Attestations are weaker than executed evidence and are reported as such.")
        return

    item = ledger.record(resolved, gate, cmd, cwd=_paths().root)
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
    run = ledger.load(resolved)
    root = _paths().root
    problems = 0

    if diff_base:
        diff = subprocess.run(
            ["git", "diff", diff_base, "--"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            check=False,
        ).stdout
        names = subprocess.run(
            ["git", "diff", "--name-only", diff_base, "--"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            check=False,
        ).stdout.split()

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


@gate_app.command("close")
def gate_close(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Attempt to close a run. Refuses unless every gate is satisfied by evidence."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    run = ledger.load(resolved)
    verdict = adjudicate(run, ledger.evidence(resolved))

    _echo(f"RUN {verdict.run_id}  class={verdict.task_class}")
    for gate in run.required:
        if gate in verdict.satisfied:
            mark = "attested" if gate in verdict.attested_only else "executed"
            _echo(f"  [x] {gate}  ({mark})")
        elif gate in verdict.failing:
            _echo(f"  [!] {gate}  FAILING")
        else:
            _echo(f"  [ ] {gate}  no evidence")

    if not verdict.can_close:
        _echo("")
        _echo(f"REFUSED  {verdict.blocked_reason}")
        _echo("You may not report this work as complete.")
        raise typer.Exit(1)

    run.closed_at = datetime.now(UTC).isoformat()
    run.verdict = "COMPLETE"
    ledger.save(run)
    _echo("")
    _echo(f"COMPLETE  {len(verdict.satisfied)} gate(s) satisfied")
    if verdict.attested_only:
        _echo(f"NOTE  attested rather than executed: {', '.join(verdict.attested_only)}")


@gate_app.command("status")
def gate_status(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Show gate progress for a run without attempting to close it."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    run = ledger.load(resolved)
    evidence = ledger.evidence(resolved)
    verdict = adjudicate(run, evidence)
    _echo(f"RUN {run.run_id}  class={run.task_class}  objective: {run.objective}")
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
    name: str = typer.Argument(..., help="Skill that was loaded"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Record that a skill was loaded, so skill usage is auditable after the fact."""
    resolved = _resolve_run(run_id)
    run = _ledger().note_skill(resolved, name)
    _echo(f"SKILL_LOADED  {name}  (total {len(run.skills_loaded)})")


@gate_app.command("contracts")
def gate_contracts() -> None:
    """Show which gates each task class requires."""
    for task_class, gates in CONTRACTS.items():
        _echo(f"{task_class}:")
        for gate in gates or ():
            _echo(f"  - {gate}")
        if not gates:
            _echo("  (none)")


if __name__ == "__main__":
    app()
