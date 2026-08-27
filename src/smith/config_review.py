"""Read-only project configuration audit.

A.W.I.N.O. runs inside other people's repositories, and a tool that rewrites
config on their behalf is a liability, not an audit. Every check in this module
only reads files and reports what it finds: severity, category, exact citation
(file, and line number when the finding is line-addressable), a human message,
and, where a check can be reproduced by a human, the shell command that proves
it. Nothing here ever opens a file for writing.

The checks intentionally respect whichever tooling the project already chose.
A Just-only project is never told to adopt Make, and vice versa; the only
cross-runner check is a genuine conflict (the same task name defined
differently in both files), which is a real hazard regardless of preference.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"""(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['"][^'"${}\s]{6,}['"]""",
        re.IGNORECASE,
    ),
)

GITIGNORE_ENV_PATTERNS = frozenset({".env", "*.env", "/.env", ".env*", "**/.env"})


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class Category(StrEnum):
    PYPROJECT = "pyproject"
    LOCKFILE = "lockfile"
    TASK_RUNNER = "task-runner"
    CI = "ci"
    HARNESS = "harness"
    ENV_FILE = "env-file"
    README_DRIFT = "readme-drift"
    PERMISSIONS = "permissions"


@dataclass(frozen=True)
class Finding:
    """One audit result. Always cited, never a vague impression."""

    severity: Severity
    category: Category
    path: Path
    message: str
    line: int | None = None
    suggested_command: str | None = None

    def citation(self, root: Path) -> str:
        rel = self.path.relative_to(root) if root in self.path.parents else self.path
        return f"{rel}:{self.line}" if self.line is not None else str(rel)

    def as_dict(self, root: Path) -> dict:
        return {
            "severity": str(self.severity),
            "category": str(self.category),
            "citation": self.citation(root),
            "message": self.message,
            "suggested_command": self.suggested_command,
        }


def _finding(
    severity: Severity,
    category: Category,
    path: Path,
    message: str,
    *,
    line: int | None = None,
    suggested_command: str | None = None,
) -> Finding:
    return Finding(severity, category, path, message, line, suggested_command)


# ── pyproject.toml ────────────────────────────────────────────────────────────


def check_pyproject(root: Path) -> list[Finding]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return [
            _finding(
                Severity.ERROR,
                Category.PYPROJECT,
                path,
                f"pyproject.toml does not parse: {exc}",
                suggested_command="python -c \"import tomllib,pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text())\"",
            )
        ]

    findings: list[Finding] = []
    project = data.get("project", {})
    if not project.get("requires-python"):
        findings.append(
            _finding(
                Severity.WARN,
                Category.PYPROJECT,
                path,
                "no requires-python declared; interpreter versions can silently diverge across machines",
                suggested_command="grep -n requires-python pyproject.toml",
            )
        )
    if not data.get("tool"):
        findings.append(
            _finding(
                Severity.INFO,
                Category.PYPROJECT,
                path,
                "no [tool.*] sections configured; lint, format, and test tooling are undeclared",
            )
        )
    return findings


# ── uv.lock ────────────────────────────────────────────────────────────────────


def check_lockfile(root: Path) -> list[Finding]:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return []
    uses_uv = "uv" in data.get("tool", {})
    lock = root / "uv.lock"

    if not lock.is_file():
        if uses_uv:
            return [
                _finding(
                    Severity.WARN,
                    Category.LOCKFILE,
                    pyproject,
                    "[tool.uv] is configured but no uv.lock is committed; installs are not reproducible",
                    suggested_command="uv lock",
                )
            ]
        return []

    try:
        stale = lock.stat().st_mtime < pyproject.stat().st_mtime
    except OSError:
        return []
    if stale:
        return [
            _finding(
                Severity.WARN,
                Category.LOCKFILE,
                lock,
                "uv.lock is older than pyproject.toml; it may not reflect current dependencies",
                suggested_command="uv lock --check",
            )
        ]
    return []


# ── task runners: Makefile / Justfile ─────────────────────────────────────────


@dataclass(frozen=True)
class _Recipe:
    name: str
    line: int
    body: str


def _parse_make_targets(text: str) -> list[_Recipe]:
    lines = text.splitlines()
    recipes: list[_Recipe] = []
    for index, line in enumerate(lines):
        if line.startswith("\t") or line.startswith(" "):
            continue
        match = re.match(r"^([a-zA-Z0-9][\w./-]*)\s*:(?!=)", line)
        if not match:
            continue
        name = match.group(1)
        if name in {".PHONY", ".DEFAULT", ".SUFFIXES"}:
            continue
        body_lines = []
        cursor = index + 1
        while cursor < len(lines) and (lines[cursor].startswith("\t") or lines[cursor] == ""):
            if lines[cursor].strip():
                body_lines.append(lines[cursor].strip())
            cursor += 1
        recipes.append(_Recipe(name, index + 1, " ; ".join(body_lines)))
    return recipes


def _parse_just_recipes(text: str) -> list[_Recipe]:
    lines = text.splitlines()
    recipes: list[_Recipe] = []
    for index, line in enumerate(lines):
        if line.startswith((" ", "\t")):
            continue
        match = re.match(r"^([a-z_][a-z0-9_-]*)(?:\s+[\w$*+]+)*\s*:(?!=)", line)
        if not match:
            continue
        name = match.group(1)
        body_lines = []
        cursor = index + 1
        while cursor < len(lines) and (
            lines[cursor].startswith((" ", "\t")) or lines[cursor] == ""
        ):
            if lines[cursor].strip():
                body_lines.append(lines[cursor].strip())
            cursor += 1
        recipes.append(_Recipe(name, index + 1, " ; ".join(body_lines)))
    return recipes


def _load_runner_recipes(root: Path) -> dict[str, tuple[Path, _Recipe]]:
    """Combined recipe map. Later entries in this order do not overwrite earlier
    ones; duplicate detection is handled by the caller, which needs both."""
    found: dict[str, list[tuple[Path, _Recipe]]] = {}
    for name, parser in (("Makefile", _parse_make_targets), ("makefile", _parse_make_targets)):
        path = root / name
        if path.is_file():
            for recipe in parser(path.read_text(encoding="utf-8")):
                found.setdefault(recipe.name, []).append((path, recipe))
            break
    for name in ("justfile", "Justfile", ".justfile"):
        path = root / name
        if path.is_file():
            for recipe in _parse_just_recipes(path.read_text(encoding="utf-8")):
                found.setdefault(recipe.name, []).append((path, recipe))
            break
    # Keep only the first occurrence per file for the plain lookup used by other
    # checks; conflict detection below inspects the full list separately.
    return {name: entries[0] for name, entries in found.items()}, found  # type: ignore[return-value]


def check_task_runners(root: Path) -> list[Finding]:
    """Duplicate recipe names within one file, and cross-file name conflicts.

    A duplicate Makefile target silently keeps only the last definition, which
    is a real hazard `make` itself does not warn about. A Makefile and a
    Justfile defining the same task name with different bodies is worse: two
    entry points disagree about what "test" means, and whichever one a human
    happens to run first decides the outcome.
    """
    findings: list[Finding] = []
    per_file: dict[Path, list[_Recipe]] = {}
    for names, parser in (
        (("Makefile", "makefile"), _parse_make_targets),
        (("justfile", "Justfile", ".justfile"), _parse_just_recipes),
    ):
        for name in names:
            path = root / name
            if path.is_file():
                per_file[path] = parser(path.read_text(encoding="utf-8"))
                break

    for path, recipes in per_file.items():
        seen: dict[str, _Recipe] = {}
        for recipe in recipes:
            prior = seen.get(recipe.name)
            if prior is not None and prior.body != recipe.body:
                findings.append(
                    _finding(
                        Severity.WARN,
                        Category.TASK_RUNNER,
                        path,
                        f"duplicate target '{recipe.name}' redefined with a different body "
                        f"(first defined at line {prior.line})",
                        line=recipe.line,
                        suggested_command=f"grep -n '^{recipe.name}' {path.name}",
                    )
                )
            seen[recipe.name] = recipe

    files = list(per_file.items())
    if len(files) == 2:
        (path_a, recipes_a), (path_b, recipes_b) = files
        by_name_a = {r.name: r for r in recipes_a}
        by_name_b = {r.name: r for r in recipes_b}
        for name in sorted(set(by_name_a) & set(by_name_b)):
            recipe_a, recipe_b = by_name_a[name], by_name_b[name]
            if recipe_a.body != recipe_b.body:
                findings.append(
                    _finding(
                        Severity.ERROR,
                        Category.TASK_RUNNER,
                        path_a,
                        f"task '{name}' is defined differently in {path_a.name}:{recipe_a.line} "
                        f"and {path_b.name}:{recipe_b.line}; only one runner's definition can win",
                        line=recipe_a.line,
                        suggested_command=f"diff <(sed -n '{recipe_a.line}p' {path_a.name}) "
                        f"<(sed -n '{recipe_b.line}p' {path_b.name})",
                    )
                )
    return findings


# ── CI config ──────────────────────────────────────────────────────────────────

_LINT_PATTERN = re.compile(r"\b(lint|ruff|eslint|flake8|pylint|black --check)\b", re.IGNORECASE)
_TEST_PATTERN = re.compile(
    r"\b(pytest|npm test|npm run test|jest|go test|cargo test|unittest)\b", re.IGNORECASE
)


def check_ci(root: Path) -> list[Finding]:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []
    findings: list[Finding] = []
    workflow_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    for path in workflow_files:
        text = path.read_text(encoding="utf-8")
        if not _TEST_PATTERN.search(text):
            findings.append(
                _finding(
                    Severity.WARN,
                    Category.CI,
                    path,
                    "no recognizable test step (pytest, npm test, cargo test, ...)",
                )
            )
        if not _LINT_PATTERN.search(text):
            findings.append(
                _finding(
                    Severity.INFO,
                    Category.CI,
                    path,
                    "no recognizable lint step (ruff, eslint, flake8, ...)",
                )
            )
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "${{" in line:
                # A templated secrets reference is the correct pattern, not a leak.
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        _finding(
                            Severity.ERROR,
                            Category.CI,
                            path,
                            f"line matches a hardcoded-secret pattern: {line.strip()[:80]!r}",
                            line=lineno,
                            suggested_command=f"sed -n '{lineno}p' {path.relative_to(root)}",
                        )
                    )
                    break
    return findings


# ── editor / harness config ───────────────────────────────────────────────────

_NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
_SLUG_RE = re.compile(r"^slug:\s*(.+)$", re.MULTILINE)


def _frontmatter_name(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    frontmatter = parts[1]
    match = _NAME_RE.search(frontmatter) or _SLUG_RE.search(frontmatter)
    return match.group(1).strip().strip("'\"") if match else None


def check_harness_config(root: Path) -> list[Finding]:
    """Conflicting or duplicate persona/mode declarations across harness dirs.

    Two independent hazards: `AGENTS.md` and `CLAUDE.md` both present with
    different bodies (which the agent to trust?), and two authored persona or
    mode files under `.kilo/` or `.claude/` declaring the same name with
    different content (which one loads?).
    """
    findings: list[Finding] = []
    agents_md = root / "AGENTS.md"
    claude_md = root / "CLAUDE.md"
    if agents_md.is_file() and claude_md.is_file():
        agents_text = agents_md.read_text(encoding="utf-8").strip()
        claude_text = claude_md.read_text(encoding="utf-8").strip()
        if agents_text and claude_text and agents_text != claude_text:
            findings.append(
                _finding(
                    Severity.WARN,
                    Category.HARNESS,
                    agents_md,
                    "AGENTS.md and CLAUDE.md both exist with different content; "
                    "confirm they do not declare conflicting instructions",
                    suggested_command="diff AGENTS.md CLAUDE.md",
                )
            )

    declared: dict[str, Path] = {}
    candidate_dirs = (
        root / ".kilo" / "agent",
        root / ".kilo" / "modes",
        root / ".kilo" / "agents",
        root / ".claude" / "agents",
    )
    for directory in candidate_dirs:
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob("*.md")):
            name = _frontmatter_name(file) or file.stem
            prior = declared.get(name)
            if prior is not None and prior != file:
                findings.append(
                    _finding(
                        Severity.WARN,
                        Category.HARNESS,
                        file,
                        f"persona/mode name '{name}' is also declared in "
                        f"{prior.relative_to(root)}; only one can be authoritative",
                        suggested_command=f"diff {prior.relative_to(root)} {file.relative_to(root)}",
                    )
                )
            else:
                declared[name] = file
    return findings


# ── environment files ─────────────────────────────────────────────────────────


def check_env_files(root: Path) -> list[Finding]:
    """Presence and gitignore status only. Values are never read."""
    env_file = root / ".env"
    env_example = root / ".env.example"
    findings: list[Finding] = []

    if not env_file.is_file():
        return findings

    findings.append(
        _finding(
            Severity.INFO,
            Category.ENV_FILE,
            env_file,
            ".env is present (values not read by this audit)",
        )
    )

    gitignore = root / ".gitignore"
    ignored = False
    if gitignore.is_file():
        patterns = {line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()}
        ignored = bool(patterns & GITIGNORE_ENV_PATTERNS)
    if not ignored:
        findings.append(
            _finding(
                Severity.ERROR,
                Category.ENV_FILE,
                env_file,
                ".env is not listed in .gitignore; secret values risk being committed",
                suggested_command="git check-ignore -v .env",
            )
        )

    if not env_example.is_file():
        findings.append(
            _finding(
                Severity.WARN,
                Category.ENV_FILE,
                env_file,
                "no .env.example alongside .env; new contributors have no template to follow",
            )
        )
    return findings


# ── README / task command drift ───────────────────────────────────────────────


def check_readme_drift(root: Path) -> list[Finding]:
    """Task runner recipes with no corresponding mention in README.md."""
    readme = root / "README.md"
    if not readme.is_file():
        return []
    body = readme.read_text(encoding="utf-8")
    documented = set(re.findall(r"\b(?:make|just)\s+([a-zA-Z0-9][\w-]*)", body))

    findings: list[Finding] = []
    for names, parser in (
        (("Makefile", "makefile"), _parse_make_targets),
        (("justfile", "Justfile", ".justfile"), _parse_just_recipes),
    ):
        for name in names:
            path = root / name
            if not path.is_file():
                continue
            for recipe in parser(path.read_text(encoding="utf-8")):
                if recipe.name not in documented:
                    findings.append(
                        _finding(
                            Severity.INFO,
                            Category.README_DRIFT,
                            path,
                            f"task '{recipe.name}' is not mentioned in README.md",
                            line=recipe.line,
                            suggested_command=f"grep -n {recipe.name} README.md",
                        )
                    )
            break
    return findings


# ── kilo.json permissions ─────────────────────────────────────────────────────

_BROAD_PERMISSION_RE = re.compile(r'"\*"\s*:\s*"allow"')


def check_permissions(root: Path) -> list[Finding]:
    path = root / "kilo.json"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return [
            _finding(
                Severity.ERROR,
                Category.PERMISSIONS,
                path,
                f"kilo.json does not parse: {exc}",
                suggested_command="python -m json.tool kilo.json",
            )
        ]

    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _BROAD_PERMISSION_RE.search(line):
            findings.append(
                _finding(
                    Severity.ERROR,
                    Category.PERMISSIONS,
                    path,
                    'overly broad permission pattern \'"*": "allow"\' grants unrestricted access',
                    line=lineno,
                    suggested_command=f"sed -n '{lineno}p' kilo.json",
                )
            )
    return findings


# ── composition ────────────────────────────────────────────────────────────────

CHECKS = (
    check_pyproject,
    check_lockfile,
    check_task_runners,
    check_ci,
    check_harness_config,
    check_env_files,
    check_readme_drift,
    check_permissions,
)


def review(root: Path) -> list[Finding]:
    """Run every read-only detector against a project root. Never writes."""
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(root))
    return findings


def as_json(root: Path, findings: list[Finding]) -> str:
    counts = {severity.value: 0 for severity in Severity}
    for finding in findings:
        counts[str(finding.severity)] += 1
    return json.dumps(
        {
            "root": str(root),
            "count": len(findings),
            "summary": counts,
            "findings": [f.as_dict(root) for f in findings],
        },
        indent=2,
    )
