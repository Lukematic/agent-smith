"""Project health gates: the ledger turned on the repository itself.

Clean directories, clean docs, working lint, a present justfile, a valid
pyproject, a live uv environment, and a reachable seeds tracker are all things
that decay silently. Asking an agent to "keep the repo clean" is a wish. Each of
these is a check that returns a verdict, so ``awino doctor`` can refuse.

Every check returns the same shape so the CLI renders them uniformly and the
gate ledger can consume them as evidence.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from smith.paths import SmithPaths

REQUIRED_RUFF_RULES = frozenset({"E", "F", "I"})
REQUIRED_JUST_RECIPES = frozenset({"install", "check", "lint", "test", "fmt", "tidy", "update"})
MAX_DOC_ORPHAN_RATIO = 0.0


class Health(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Result:
    name: str
    health: Health
    detail: str
    remedy: str = ""

    @property
    def blocking(self) -> bool:
        return self.health is Health.FAIL


def _ok(name: str, detail: str) -> Result:
    return Result(name, Health.OK, detail)


def _fail(name: str, detail: str, remedy: str) -> Result:
    return Result(name, Health.FAIL, detail, remedy)


def _warn(name: str, detail: str, remedy: str = "") -> Result:
    return Result(name, Health.WARN, detail, remedy)


def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True, cwd=str(cwd), check=False)
    except (OSError, FileNotFoundError) as exc:
        return 127, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


# ── toolchain ────────────────────────────────────────────────────────────────


def check_skill_registry(paths: SmithPaths) -> Result:
    """Every skill directory must be indexed, and the index must not invent skills.

    A skill on disk that no index mentions is invisible: nothing routes to it. An
    index entry with no directory behind it is a router that lies. Both are
    detectable by comparing two lists, so neither should ever survive a commit.
    """
    on_disk = {p.parent.name for p in paths.skills.glob("awino-*/SKILL.md")}
    if not on_disk:
        return _warn("skill_registry", "no skills found", "author one")

    bare = sorted(
        d.name
        for d in paths.skills.glob("awino-*")
        if d.is_dir() and not (d / "SKILL.md").is_file()
    )
    if bare:
        return _fail(
            "skill_registry",
            f"directory without SKILL.md: {', '.join(bare)}",
            "add SKILL.md or remove the directory",
        )

    index = paths.docs / "skills.md"
    if not index.is_file():
        return _fail(
            "skill_registry",
            f"{len(on_disk)} skill(s) on disk with no docs/skills.md index",
            "just fix",
        )
    body = index.read_text(encoding="utf-8")
    listed = set(re.findall(r"\[`([a-z0-9-]+)`\]", body))
    missing = sorted(on_disk - listed)
    phantom = sorted(listed - on_disk)
    if missing or phantom:
        parts = []
        if missing:
            parts.append(f"not indexed: {', '.join(missing)}")
        if phantom:
            parts.append(f"indexed but absent: {', '.join(phantom)}")
        return _fail("skill_registry", "; ".join(parts), "just fix")

    manifest = paths.root / "plugin.json"
    if manifest.is_file():
        try:
            declared = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return _fail("skill_registry", f"plugin.json does not parse: {exc}", "fix the JSON")
        if not declared.get("name"):
            return _fail("skill_registry", "plugin.json has no name", "add a name field")

    return _ok("skill_registry", f"{len(on_disk)} canonical skill(s), all indexed and manifested")


def check_clone_freshness(paths: SmithPaths) -> Result:
    """Warn when this A.W.I.N.O. clone has diverged from its own remote.

    Multiple clones of the same installation can silently drift: a lesson,
    fix, or feature committed in one clone is invisible to every other
    clone until it is pushed and pulled. This exists so that drift is a
    visible warning, not a discovery made by manually diffing clones.
    """
    if shutil.which("git") is None:
        return _ok("clone_freshness", "git not on PATH, skipping")
    code, _ = _run(["git", "rev-parse", "--git-dir"], paths.root)
    if code != 0:
        return _ok("clone_freshness", "not a git checkout, skipping")

    fetch_code, _fetch_out = _run(["git", "fetch"], paths.root)
    if fetch_code != 0:
        return _warn(
            "clone_freshness",
            "could not reach the remote to compare",
            "check network access or remote credentials",
        )

    status_code, status_out = _run(["git", "status", "--porcelain=v1", "-uno"], paths.root)
    dirty = status_code == 0 and status_out.strip() != ""

    ahead_code, ahead_out = _run(["git", "rev-list", "--count", "@{u}..HEAD"], paths.root)
    behind_code, behind_out = _run(["git", "rev-list", "--count", "HEAD..@{u}"], paths.root)
    if ahead_code != 0 or behind_code != 0:
        return _ok("clone_freshness", "no upstream tracking branch configured, skipping")

    ahead = int(ahead_out.strip() or 0)
    behind = int(behind_out.strip() or 0)

    if ahead == 0 and behind == 0 and not dirty:
        return _ok("clone_freshness", "in sync with its remote")

    problems = []
    if ahead:
        problems.append(f"{ahead} commit(s) ahead, not yet pushed")
    if behind:
        problems.append(f"{behind} commit(s) behind, not yet pulled")
    if dirty:
        problems.append("uncommitted tracked changes")
    return _warn(
        "clone_freshness",
        "; ".join(problems),
        "git push and/or git pull so this clone matches its remote before relying on it",
    )


def check_uv(paths: SmithPaths) -> Result:
    """uv must be installed and the project environment must exist and be synced."""
    if shutil.which("uv") is None:
        return _fail(
            "uv_installed",
            "uv is not on PATH",
            "install from https://docs.astral.sh/uv/ then run 'just install'",
        )
    venv = paths.root / ".venv"
    if not venv.is_dir():
        return _fail("uv_env", "no .venv in the project", "just install")
    code, out = _run(["uv", "sync", "--all-groups", "--frozen", "--quiet"], paths.root)
    if code != 0:
        # --frozen fails when the lock is out of date, which is the real signal.
        return _fail(
            "uv_env",
            f"environment out of sync with the lockfile: {out.strip().splitlines()[-1] if out.strip() else code}",
            "just install",
        )
    return _ok("uv_env", "uv present and environment synced against the lockfile")


def check_python_pin(paths: SmithPaths) -> Result:
    pin = paths.root / ".python-version"
    if not pin.is_file():
        return _warn(
            "python_pinned",
            "no .python-version, so the interpreter can drift between machines",
            "echo 3.12 > .python-version",
        )
    return _ok("python_pinned", f"pinned to {pin.read_text(encoding='utf-8').strip()}")


def check_justfile_shim(paths: SmithPaths) -> Result:
    """The justfile is the discoverable command surface. Missing recipes hide work."""
    justfile = paths.root / "justfile"
    if not justfile.is_file():
        return _fail("justfile", "no justfile", "add one so commands are discoverable")
    text = justfile.read_text(encoding="utf-8")
    recipes = set(re.findall(r"^([a-z][a-z0-9-]*)(?:\s+[A-Z_]+)*:", text, re.MULTILINE))
    missing = REQUIRED_JUST_RECIPES - recipes
    if missing:
        return _fail(
            "justfile",
            f"missing recipes: {', '.join(sorted(missing))}",
            "add them so agents and humans use one entry point",
        )
    if shutil.which("just") is None:
        return _warn("justfile", f"{len(recipes)} recipes but just is not on PATH", "install just")
    return _ok("justfile", f"{len(recipes)} recipes, all required ones present")


def check_pyproject(paths: SmithPaths) -> Result:
    """pyproject must parse and must configure ruff and pytest, not just deps."""
    pyproject = paths.root / "pyproject.toml"
    if not pyproject.is_file():
        return _fail("pyproject", "no pyproject.toml", "add one")
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return _fail("pyproject", f"does not parse: {exc}", "fix the syntax")

    problems: list[str] = []
    tool = data.get("tool", {})
    if "ruff" not in tool:
        problems.append("no [tool.ruff]")
    else:
        selected = set(tool["ruff"].get("lint", {}).get("select", []))
        gaps = REQUIRED_RUFF_RULES - selected
        if gaps:
            problems.append(f"ruff missing rule families {sorted(gaps)}")
    if "pytest" not in tool:
        problems.append("no [tool.pytest.ini_options]")
    if not data.get("project", {}).get("requires-python"):
        problems.append("no requires-python")
    if problems:
        return _fail(
            "pyproject", "; ".join(problems), "configure ruff and pytest in pyproject.toml"
        )
    return _ok("pyproject", "parses with ruff and pytest configured")


# ── quality ──────────────────────────────────────────────────────────────────


def check_lint(paths: SmithPaths) -> Result:
    code, out = _run(
        ["uv", "run", "ruff", "check", "src", "tests", "--output-format", "concise"], paths.root
    )
    if code == 0:
        return _ok("lint", "ruff check clean")
    count = len([ln for ln in out.splitlines() if ":" in ln and " " in ln])
    return _fail("lint", f"{count} lint error(s)", "just fmt then just lint")


def check_format(paths: SmithPaths) -> Result:
    code, out = _run(["uv", "run", "ruff", "format", "--check", "src", "tests"], paths.root)
    if code == 0:
        return _ok("format", "formatting consistent")
    return _fail(
        "format", out.strip().splitlines()[-1] if out.strip() else "unformatted files", "just fmt"
    )


def check_tests(paths: SmithPaths) -> Result:
    code, out = _run(["uv", "run", "pytest", "-q"], paths.root)
    summary = next(
        (ln for ln in reversed(out.splitlines()) if "passed" in ln or "failed" in ln), ""
    )
    if code == 0:
        return _ok("tests", summary.strip() or "suite passed")
    return _fail("tests", summary.strip() or f"pytest exit {code}", "fix the code, never the test")


# ── structure and docs ───────────────────────────────────────────────────────


def check_structure(paths: SmithPaths) -> Result:
    """Clutter at the root is the first symptom of a project going feral."""
    from smith.tidy import Finding, Tidier

    items = Tidier(paths).scan()
    structural = [i for i in items if i.kind in {Finding.STRAY_ROOT_FILE, Finding.STRAY_ROOT_DIR}]
    dupes = [i for i in items if i.kind is Finding.DUPLICATE_CONTENT]
    if structural or dupes:
        names = ", ".join(i.path.name for i in (structural + dupes)[:5])
        return _fail(
            "structure",
            f"{len(structural)} stray at root, {len(dupes)} duplicated: {names}",
            "just tidy",
        )
    disposable = [i for i in items if i.kind is Finding.DISPOSABLE]
    if disposable:
        return _warn("structure", f"{len(disposable)} regenerable artifact(s)", "just clean")
    return _ok("structure", "no clutter")


def check_docs(paths: SmithPaths) -> Result:
    """Docs must exist, live in docs/, and be reachable from the README.

    An unreferenced doc is sediment: it costs cognitive load and nobody reads it.
    """
    readme = paths.root / "README.md"
    if not readme.is_file():
        return _fail("docs", "no README.md", "write one")
    if not paths.docs.is_dir():
        return _warn("docs", "no docs/ directory", "move long-form docs into docs/")

    body = readme.read_text(encoding="utf-8")
    # A directory README documents its own folder; it is not a topic doc that the
    # root README should index. Requiring a link to it produces a self-reference.
    docs = sorted(p for p in paths.docs.glob("*.md") if p.name != "README.md")
    orphans = [d.name for d in docs if f"docs/{d.name}" not in body]
    if orphans:
        return _fail(
            "docs",
            f"{len(orphans)} doc(s) not linked from README: {', '.join(orphans)}",
            "link them from README or archive them",
        )
    broken: list[str] = []
    for link in re.findall(r"\]\((docs/[^)#]+\.md)\)", body):
        if not (paths.root / link).is_file():
            broken.append(link)
    if broken:
        return _fail(
            "docs",
            f"README links to missing file(s): {', '.join(broken)}",
            "fix or remove the link",
        )
    return _ok("docs", f"{len(docs)} doc(s) in docs/, all linked from README")


def check_artifacts(paths: SmithPaths) -> Result:
    """Every authored skill and agent must pass the validator."""
    from smith.validate import discover, validate_file

    files = discover([paths.skills, paths.agents, paths.emitted])
    if not files:
        return _warn("artifacts", "no skills or agents found", "author some")
    blocked = [f.name for f in files if not validate_file(f).ok()]
    if blocked:
        return _fail(
            "artifacts",
            f"{len(blocked)} of {len(files)} invalid",
            "awino validate skills agents -v",
        )
    return _ok("artifacts", f"{len(files)} artifact(s) valid")


# ── context and memory ───────────────────────────────────────────────────────


def check_folder_docs(paths: SmithPaths) -> Result:
    """Every meaningful directory must carry a FAIR README.

    An agent opening an undocumented directory infers its purpose from filenames,
    guesses wrong, and writes something plausible in the wrong place. A README is
    the cheapest guide available: it constrains the action space before the agent
    acts, which is feedforward control rather than after-the-fact correction.
    """
    from smith import fair

    statuses = [s for s in fair.audit(paths.root) if not s.exempt]
    if not statuses:
        return _warn("folder_docs", "no documentable directories found")

    undocumented = [s for s in statuses if not s.ok]
    if undocumented:
        names = ", ".join(s.directory.name for s in undocumented[:5])
        return _fail(
            "folder_docs",
            f"{len(undocumented)} of {len(statuses)} directories undocumented: {names}",
            "just fix",
        )

    remaining = fair.stubs(paths.root)
    if remaining:
        return _warn(
            "folder_docs",
            f"{len(statuses)} documented, {len(remaining)} still generated stubs with TODOs",
            "replace the TODOs with real answers",
        )
    return _ok("folder_docs", f"{len(statuses)} directories documented, FAIR sections present")


def check_capability_claims(paths: SmithPaths) -> Result:
    """Every documented capability claim must be backed by a passing probe.

    This is the check that was missing when the persona claimed A.W.I.N.O. "spawns
    scoped subagents" while no spawn code existed. Prose describing a capability is
    indistinguishable from prose describing an aspiration, so the claim is checked
    against a probe and the probe wins.
    """
    from smith import capability

    rows = capability.audit_claims(paths)
    false_claims = [(claim, cap) for claim, cap in rows if cap.state is capability.State.ABSENT]
    if false_claims:
        names = ", ".join(claim for claim, _ in false_claims)
        return _fail(
            "capability_claims",
            f"{len(false_claims)} documented claim(s) unsupported: {names}",
            "write the code or change the document, then re-run awino limits --claims",
        )

    caveated = [c for _, c in rows if c.state is capability.State.DEGRADED]
    if caveated:
        return _warn(
            "capability_claims",
            f"{len(rows)} claims probed, {len(caveated)} need a stated limit",
            "state the limit whenever making these claims",
        )
    return _ok("capability_claims", f"{len(rows)} documented claims all backed by a passing probe")


def check_seeds(paths: SmithPaths, project_root: Path | None = None) -> Result:
    """A worklist should exist and must not be duplicated.

    Seeds is **optional**. A.W.I.N.O. runs in repositories it does not own, so an
    absent tracker is a degraded capability, never a failure. The one genuine
    failure is two worklists at once: a markdown checklist beside a real tracker
    means nobody knows which is authoritative, which is ``COMPETING_TRACKER``.
    """
    from smith.seeds import Seeds, SeedsState

    root = project_root or paths.root
    seeds = Seeds(root)
    state, reason = seeds.state()

    tasks_dir = root / "tasks"
    markdown_lists = list(tasks_dir.glob("todo.md")) if tasks_dir.is_dir() else []

    if state is SeedsState.READY and markdown_lists:
        return _warn(
            "seeds",
            "both a seeds tracker and a legacy markdown todo exist; Seeds is authoritative",
            "migrate the legacy list into Seeds, then archive it after human review",
        )

    if state is SeedsState.READY:
        open_issues = seeds.list_open(limit=200)
        verify = [i for i in open_issues if i.wants_verification]
        detail = f"{reason}, {len(open_issues)} open"
        if verify:
            detail += f", {len(verify)} awaiting verification"
        return _ok("seeds", detail)

    if markdown_lists:
        return _warn(
            "seeds",
            f"tracking via markdown; {reason}",
            "optional: adopt seeds for dependency tracking",
        )

    return _warn("seeds", reason, "optional, no action required")


def _strip_code_fences(text: str) -> str:
    """Remove fenced blocks so format templates are not mistaken for content.

    ``lessons.md`` documents its own line format inside a fence. Reading that as a
    real lesson made the check fail on a correct file, which is the
    ``LINTER_FALSE_POSITIVE`` trap.
    """
    return re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)


def check_memory(paths: SmithPaths) -> Result:
    """The lessons ledger must exist and stay append-only in shape."""
    if not paths.lessons.is_file():
        return _fail("memory", "no memory/lessons.md", "awino scaffold")
    prose = _strip_code_fences(paths.lessons.read_text(encoding="utf-8"))
    rules = re.findall(r"^- \[(\d{4}-\d{2}-\d{2})\]", prose, re.MULTILINE)
    malformed = [
        ln
        for ln in prose.splitlines()
        if ln.startswith("- ") and not re.match(r"^- \[\d{4}-\d{2}-\d{2}\]", ln)
    ]
    if malformed:
        return _fail(
            "memory",
            f"{len(malformed)} lesson(s) missing a date stamp: {malformed[0][:50]}",
            "use '- [YYYY-MM-DD] `MODE` rule (surface: x)'",
        )
    if not rules:
        return _warn("memory", "no lessons recorded yet", "record one after the next failure")
    return _ok("memory", f"{len(rules)} dated lesson(s)")


def check_knowledge(paths: SmithPaths) -> Result:
    from smith.knowledge import KnowledgeStore

    store = KnowledgeStore(paths)
    if not store.registry():
        return _fail("knowledge", "REGISTRY.yaml missing or unparseable", "fix the YAML")
    indexed = len(store.registry_paths())
    age = store.manifest.newest_age_days
    if age is None:
        return _ok("knowledge", f"{indexed} chapters indexed, cache cold which is fine")
    if age >= store.stale_days:
        return _warn("knowledge", f"{indexed} indexed, cache {age}d old", "just update")
    return _ok("knowledge", f"{indexed} chapters indexed, cache {age}d old")


def check_harness_install(_paths: SmithPaths) -> Result:
    """Is A.W.I.N.O. actually reachable as a plugin and a persona?"""
    home = Path.home()
    plugin = home / ".agents" / "plugins" / "agent-smith"
    persona = home / ".agents" / "agents" / "agent-smith.md"
    have_plugin = plugin.exists() or plugin.is_symlink()
    if have_plugin and persona.is_file():
        return _ok("harness", "plugin linked and persona installed")
    missing = [n for n, ok in (("plugin", have_plugin), ("persona", persona.is_file())) if not ok]
    return _warn("harness", f"not installed: {', '.join(missing)}", "just link")


# ── the suite ────────────────────────────────────────────────────────────────

FAST_CHECKS: tuple[Callable[[SmithPaths], Result], ...] = (
    check_uv,
    check_python_pin,
    check_justfile_shim,
    check_pyproject,
    check_structure,
    check_docs,
    check_skill_registry,
    check_folder_docs,
    check_capability_claims,
    check_artifacts,
    check_seeds,
    check_memory,
    check_knowledge,
    check_harness_install,
    check_clone_freshness,
)

SLOW_CHECKS: tuple[Callable[[SmithPaths], Result], ...] = (
    check_lint,
    check_format,
    check_tests,
)


def run_all(paths: SmithPaths, *, fast: bool = False) -> list[Result]:
    checks = FAST_CHECKS if fast else FAST_CHECKS + SLOW_CHECKS
    return [check(paths) for check in checks]


def summarise(results: list[Result]) -> dict[str, int]:
    return {
        "ok": sum(1 for r in results if r.health is Health.OK),
        "warn": sum(1 for r in results if r.health is Health.WARN),
        "fail": sum(1 for r in results if r.health is Health.FAIL),
    }


def as_json(results: list[Result]) -> str:
    return json.dumps(
        {
            "summary": summarise(results),
            "checks": [
                {"name": r.name, "health": str(r.health), "detail": r.detail, "remedy": r.remedy}
                for r in results
            ],
        },
        indent=2,
    )
