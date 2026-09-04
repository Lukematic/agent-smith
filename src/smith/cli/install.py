"""owns: install, link, install-mode, mode-status, install-status, pointer, skills, skills-status, install-refresh, scaffold

Getting A.W.I.N.O. into a harness and confirming it is really there. Detection
rather than assumption: every path is read from the machine, never guessed.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from smith import (
    harness,
    modes,
)
from smith.cli import (
    _echo,
    _ledger,
    _paths,
    _skill_catalog,
    _workspace,
    app,
)


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


@app.command("skills-status")
def skills_status_command(
    as_json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show whether each installed skill copy still matches source, by content
    hash.

    A substring search for a retired filename produces false positives against
    legitimate prose that explains it no longer exists. This compares bytes.
    """
    workspace = _workspace()
    smith_home = workspace.home.root
    targets = [t for t in harness.detected(workspace.project.root) if t.harness.supports_skills]

    if as_json_output:
        payload = {
            f"{t.harness}/{t.scope}": [
                {"skill": d.skill, "path": str(d.path), "state": d.state}
                for d in harness.skill_drift(smith_home, t)
            ]
            for t in targets
        }
        _echo(json.dumps(payload, indent=2))
        return

    total_drifted = 0
    for target in targets:
        drift = harness.skill_drift(smith_home, target)
        label = f"{target.harness.label} ({target.scope})"
        _echo(label)
        for item in drift:
            _echo(f"  {item.state:<14} {item.skill}")
            if item.state == "drifted":
                total_drifted += 1
        if not drift:
            _echo("  (no skills installed here)")
        _echo("")

    if total_drifted:
        _echo(
            f"DRIFTED  {total_drifted} installer-owned copy(ies) out of date; run: awino install-refresh"
        )
    else:
        _echo("CURRENT  every installer-owned copy matches source")


@app.command("install-refresh")
def install_refresh_command(
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Also replace copies ownership cannot otherwise resolve"
    ),
) -> None:
    """Repair drifted installer-owned skill copies. Human-modified copies are
    reported and preserved, never overwritten."""
    del (
        overwrite
    )  # reserved for parity with install --overwrite; refresh never force-replaces human edits
    workspace = _workspace()
    smith_home = workspace.home.root
    targets = [t for t in harness.detected(workspace.project.root) if t.harness.supports_skills]

    any_failed = False
    for target in targets:
        for action in harness.refresh_skills(smith_home, target):
            _echo(f"{action.outcome:<10} {action.path}  {action.detail}")
            any_failed = any_failed or action.failed

    if any_failed:
        raise typer.Exit(1)


@app.command()
def scaffold() -> None:
    """Create any missing directories A.W.I.N.O. expects."""
    created = _paths().ensure_scaffold()
    for path in created:
        _echo(f"  created {path}")
    _echo(f"SCAFFOLD  {len(created)} directories created")


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
