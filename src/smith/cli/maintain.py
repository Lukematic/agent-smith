"""owns: doctor, update, update-preflight, rollback, knowledge-update, tidy, clean, fix, validate, hook, heal, watch, watch-add, watch-remove, watch-list, config-review, review-doc, delegate, registry-json, status, pit

Keeping A.W.I.N.O.'s own installation healthy, current, and tidy, plus the
hook adapter and the judgement-free repair paths.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import typer

from smith import (
    cli,
    config_review,
    dispatch,
    doc_review,
    fix,
    guard,
    harness,
    health,
    models,
    modes,
    onboarding,
    project_guard,
    provision,
    seeds,
    session_log,
    session_state,
    spawn,
    stance,
    updater,
    watch,
)
from smith.cli import (
    _detect_claude_plugin,
    _echo,
    _ledger,
    _paths,
    _skill_catalog,
    _version,
    _workspace,
    app,
)
from smith.enforce import (
    Gate,
    LedgerError,
)
from smith.tidy import Finding, Tidier
from smith.validate import BROKEN_SELFTEST, Status, discover, validate_file, validate_text


@app.command("knowledge-update")
def knowledge_update() -> None:
    """Refresh stale knowledge cache entries and report registry drift.

    This refreshes the fetched book/chapter cache, not A.W.I.N.O.'s own
    installed version. To update A.W.I.N.O. itself, use 'awino update'.
    """
    paths = _paths()
    store = cli.KnowledgeStore(paths, budget=10_000)
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


@app.command("update")
def update_command() -> None:
    """Update A.W.I.N.O. itself the right way for how it is installed here.

    Detects whether this machine uses the Claude Code plugin or a standalone
    clone, and runs the matching update path automatically - so the human
    does not need to remember two different procedures. Always ends by
    printing the version that is actually active afterward.
    """
    if _detect_claude_plugin():
        _echo("DETECTED  Claude Code plugin install")
        marketplace = subprocess.run(
            ["claude", "plugin", "marketplace", "update", "awino"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if marketplace.returncode != 0:
            _echo(
                f"FAILED  marketplace update: {marketplace.stderr.strip() or marketplace.stdout.strip()}"
            )
            raise typer.Exit(1)
        _echo(marketplace.stdout.strip())
        plugin_update = subprocess.run(
            ["claude", "plugin", "update", "awino@awino"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if plugin_update.returncode != 0:
            _echo(
                f"FAILED  plugin update: {plugin_update.stderr.strip() or plugin_update.stdout.strip()}"
            )
            raise typer.Exit(1)
        _echo(plugin_update.stdout.strip())
        _echo("Restart Claude Code (or /reload-plugins) for the update to take effect.")
        _echo(f"VERSION  {_version()}")
        return

    _echo("DETECTED  standalone clone")
    workspace = _workspace()
    mode_paths = [target.path for target in modes.discover(workspace.project.root)]
    harness_paths = mode_paths + [
        target.persona_path for target in harness.discover(workspace.project.root)
    ]
    try:
        backup = updater.update_preflight(
            workspace.home.root, workspace.project.root, harness_paths
        )
    except updater.PreflightError as exc:
        _echo(f"BACKUP  {exc.backup}")
        _echo(f"REFUSED  {exc}")
        _echo(f"VERSION  {_version()}  (unchanged)")
        raise typer.Exit(1) from exc
    _echo(f"BACKUP  {backup}")
    _echo("UPDATED  source is clean and fast-forwarded")

    workspace.ensure_state()
    # Rebase-style second half: project state was snapshotted and restored above;
    # now re-provision the environment it lives in. Auto-steps only - a question
    # mid-update would block unattended updates, so those are reported instead.
    for step in provision.plan(workspace.project.root, workspace.state_root):
        if step.needs_question:
            _echo(f"MISSING  {step.kind.value}: {step.reason} (run 'awino start --fix')")
            continue
        for action in provision.apply_steps(workspace.project.root, [step], ask=lambda _q: False):
            _echo(f"{action.outcome:<9} {action.kind.value}  {action.detail}")
    detected_targets = [
        t for t in harness.detected(workspace.project.root) if t.harness.supports_skills
    ]
    refreshed_total = 0
    for target in detected_targets:
        for action in harness.refresh_skills(workspace.home.root, target):
            if action.outcome == "REFRESHED":
                refreshed_total += 1
    if detected_targets:
        _echo(
            f"HARNESS  refreshed {refreshed_total} skill copy(ies) across {len(detected_targets)} detected target(s)"
        )

    health_results = health.run_all(_paths(), fast=True)
    failing_health = [r for r in health_results if r.blocking]
    if failing_health:
        _echo(
            f"HEALTH  {len(failing_health)} gate(s) failing: {', '.join(r.name for r in failing_health)}"
        )
    else:
        _echo("HEALTH  ok")

    _echo(f"VERSION  {_version()}")


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
    store = cli.KnowledgeStore(paths)
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
    store = cli.KnowledgeStore(paths)
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
    store = cli.KnowledgeStore(paths)
    tidier = Tidier(paths)
    items = tidier.scan(orphaned_cache=store.orphaned_cache())
    removed = tidier.clean(items)
    for path in removed:
        _echo(f"  removed {path.name}")
    _echo(f"CLEAN  removed {len(removed)} disposable items")


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
    session_id = str(payload.get("session_id") or "unknown")
    if event == "session-start":
        if intent and intent.source == "confirmed":
            session_state.start(workspace.state_root, session_id)
            _echo(project_guard.project_context(intent))
        _hook_freshness()
        block = _resume_block(workspace, session_id)
        if block:
            _echo(project_guard.emit(project_guard.prompt_context("RESUME\n" + block)))
        return
    if event == "pre-compact":
        block = _resume_block(workspace, session_id) or "(nothing unresolved)"
        _echo(
            project_guard.emit(
                project_guard.prompt_context("PRECOMPACT - preserve verbatim:\n" + block)
            )
        )
        return
    if event == "prompt":
        if intent and intent.source == "confirmed":
            context = project_guard.project_context(intent)
            _echo(project_guard.emit(project_guard.prompt_context(context)))
        prompt_text = str(payload.get("prompt") or "").strip()
        if prompt_text:
            # Routing + stance, injected as context so the persona sees what
            # dispatch would do and which posture the words call for. Advisory
            # only: the hook never spawns - budget confirmation is mandatory
            # and a hook has nobody to confirm it.
            decision = dispatch.decide(prompt_text, _skill_catalog())
            detected = stance.detect(prompt_text)
            current = stance.load_default(workspace.project.root)
            lines = []
            if decision.confidence == "high" and decision.skill is not None:
                lines.append(
                    f"[awino] MATCHED {decision.skill.name} conf=high - run "
                    f"'awino dispatch' or 'awino floor open' to execute"
                )
            elif decision.question:
                lines.append(f"[awino] ROUTING {decision.confidence}: {decision.question}")
            if detected is not None and detected.name != current:
                lines.append(f"[awino] STANCE -> {detected.name} ({detected.trigger_description})")
            if lines:
                _echo(project_guard.emit(project_guard.prompt_context("\n".join(lines))))
            if session_id == "unknown":
                session = session_state.load(workspace.state_root)
                session_id = session.session_id if session else "unknown"
            # UserPromptSubmit only sees what the human typed, never what the
            # agent asked - so this cannot detect "the agent repeated its own
            # question." What it can detect, directly and mechanically, is
            # the costlier symptom the user actually reported: having to
            # restate something because the agent did not retain it.
            repeat = session_log.find_duplicate_question(
                workspace.state_root, session_id, prompt_text
            )
            session_log.append(workspace.state_root, session_id, "user_turn", prompt_text)
            if repeat is not None:
                _echo(
                    project_guard.emit(
                        project_guard.prompt_context(
                            f"[awino] this looks similar to what you said at turn "
                            f"{repeat.turn}: {repeat.text[:120]!r}. If the agent is asking "
                            "you to repeat an instruction it should already have, that is "
                            "the exact failure this note exists to surface."
                        )
                    )
                )
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
    store = cli.KnowledgeStore(paths)
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


@app.command("fix")
def fix_command(
    aggressive: bool = typer.Option(
        False, "--aggressive", help="Also archive stray root files, which moves them"
    ),
    check_after: bool = typer.Option(
        True, "--check/--no-check", help="Re-run the doctor afterwards"
    ),
) -> None:
    """Repair what is mechanically fixable in A.W.I.N.O.'s own installation.

    This writes to A.W.I.N.O.'s home directory, not the project you are working
    in - the same scope as 'awino doctor', which this re-runs afterward by
    default. Safe repairs regenerate derived files and remove build artifacts.
    Anything requiring prose or a real verification command is reported
    instead, because a synthesised gate passes the validator and means
    nothing.
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
    """Check A.W.I.N.O.'s own installation health. Refuses when any gate fails.

    This is the ledger turned on A.W.I.N.O.'s own source tree - clean structure,
    linked docs, working lint, a real justfile, a valid pyproject, a synced uv
    environment, a well-formed lessons ledger - not the project you are working
    in. The one exception is the 'seeds' line, which is deliberately swapped to
    report the current project's own tracker, since Seeds belongs to whatever
    project you are working on, not to A.W.I.N.O. itself. For your project's own
    configuration, task-runner conflicts, and drift, use 'awino config-review'.
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
    path: Path = typer.Option(
        None, "--path", help="Directory to audit; defaults to the resolved project root"
    ),
) -> None:
    """Audit project configuration for drift, conflicts, and unsafe defaults.

    Read-only: this never rewrites pyproject.toml, Makefile, Justfile, CI
    workflows, harness config, .env files, or kilo.json. Every finding cites
    the exact file (and line, when addressable) it came from.
    """
    workspace = _workspace()
    project = path.resolve() if path is not None else workspace.project.root
    if not project.is_dir():
        _echo(f"REFUSED  not a directory: {project}")
        raise typer.Exit(2)
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
def registry_json() -> None:
    """Dump the registry index as JSON for programmatic routing."""
    _echo(json.dumps(cli.KnowledgeStore(_paths()).registry(), indent=2))


@app.command("push")
def push_command(
    remote: str = typer.Option("origin", "--remote"),
    branch: str = typer.Option("main", "--branch"),
) -> None:
    """Push A.W.I.N.O.'s own clone, refusing when this checkout is not the
    canonical root or its origin is not the canonical remote.

    The stale-duplicate-clone incident happened twice: a lesson committed in a
    checkout nobody reads. `git push` cannot tell; this can, and it refuses
    before the network is touched.
    """
    workspace = _workspace()
    home = workspace.home.root
    verdict = guard.check_push_identity(home, canonical_root=guard.canonical_root_for(home))
    if not verdict.ok:
        _echo(f"REFUSED  {verdict.reason}")
        raise typer.Exit(1)
    _echo(f"IDENTITY  {verdict.reason}")
    completed = subprocess.run(
        ["git", "push", remote, branch],
        cwd=home,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    _echo(
        (completed.stdout + completed.stderr).strip().splitlines()[-1]
        if (completed.stdout + completed.stderr).strip()
        else "pushed"
    )
    if completed.returncode != 0:
        raise typer.Exit(completed.returncode)
    marker = guard.record_canonical_root(home)
    _echo(f"CANONICAL  {marker}")


def _resume_block(workspace, session_id: str) -> str:
    """Everything a fresh context must not lose: unresolved agent questions,
    user corrections, the last few user turns, and the carried intent.

    Data on disk that nobody re-reads is not memory. This is echoed into
    context automatically at session-start (which is also what fires after a
    compaction) so recovery does not depend on the model remembering to look.
    """
    from smith import playbook

    lines: list[str] = []
    state = workspace.state_root
    try:
        for ask in session_log.unresolved_questions(state, session_id)[-5:]:
            lines.append(f"UNRESOLVED  turn {ask.turn}: {ask.text[:160]}")
        for ask in session_log.corrections(state, session_id)[-5:]:
            lines.append(f"CORRECTION  turn {ask.turn}: {ask.text[:160]}")
        path = session_log.log_path(state, session_id)
        if path.is_file():
            turns = [a for a in session_log._read_all(path) if a.kind == "user_turn"][-3:]
            for ask in turns:
                lines.append(f"USER  turn {ask.turn}: {ask.text[:160]}")
    except Exception as exc:  # resume must never crash startup
        lines.append(f"NOTE  could not read session log: {exc}")
    from smith import machine as _machine

    trip = _machine.load(state)
    if trip.node is not _machine.Node.IDLE:
        lines.append(
            f"TRIP  node={trip.node.value} loop={trip.loop} floor={trip.floor} - continue with: awino best"
        )
    carried = playbook.load_intent(state)
    if carried:
        lines.append(f"CARRYING  floor={carried['floor']}  request={carried['request'][:120]}")
    return "\n".join(lines)
