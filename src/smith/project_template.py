"""Generic project scaffolding: pyproject.toml and a justfile, generated fresh
into a target project - never storing any real project's name, dependencies,
or description in A.W.I.N.O.'s own repository or any shared location.

Modelled on a real, working reference project's own pyproject.toml/justfile
(a uv-managed FastAPI service with a Windows+POSIX justfile), reduced to
generic, uv-based, project-name-agnostic scaffolding. Only ever writes into
the target project; nothing here is project-specific.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# Same real markers ProjectPaths.discover() already uses to recognize a
# single project boundary. Reused here for a related but distinct question:
# not "is this folder a project", but "does something inside it already
# have its own independent identity that this folder does not share."
_SUBPROJECT_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod")


def independent_subprojects(root: Path) -> list[Path]:
    """Immediate subdirectories that carry their own project marker.

    Live-caught bug: the real incident folder (a loose workspace of many
    unrelated topic folders, ai_explained/) has no .git of its own and 21
    subdirectories, but only ONE of them (.smith) has a real marker - a
    first version of this check required two marker-bearing siblings before
    refusing, so it silently passed here. A single genuinely independent
    subproject living inside a folder that itself declares no project
    identity is exactly the case that matters: that subproject did not ask
    to be absorbed into whatever the parent folder becomes.
    """
    if not root.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if any((child / marker).exists() for marker in _SUBPROJECT_MARKERS):
            found.append(child)
    # A dotted directory (.smith, .git-alike tooling installs) can itself be
    # a real, independent project even though the general loop above skips
    # dot-prefixed names as noise (.venv, .pytest_cache, .ruff_cache). Check
    # those specifically rather than blanket-including every dotfile.
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not child.name.startswith("."):
            continue
        if child.name in {".venv", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache"}:
            continue
        if any((child / marker).exists() for marker in _SUBPROJECT_MARKERS):
            found.append(child)
    return found


def is_multi_project_container(root: Path) -> bool:
    """Does ``root`` look like a folder holding one or more independent
    projects that did not ask to be treated as part of ``root`` itself?

    ``root`` having no project marker of its own (no .git, no
    pyproject.toml) while containing at least one subdirectory that does is
    enough: that subdirectory has its own declared identity, and scaffolding
    at the parent level would silently absorb it into an unrelated project
    it never opted into. When ``root`` itself already has a real marker, one
    nested subproject (a vendored dependency, a git submodule) is normal and
    must not trip this - only a genuine sibling collision does, which is a
    rarer, harder call left to a human rather than guessed at here.
    """
    subprojects = independent_subprojects(root)
    if not subprojects:
        return False
    root_has_its_own_marker = any((root / marker).exists() for marker in _SUBPROJECT_MARKERS)
    if not root_has_its_own_marker:
        return True
    return len(subprojects) >= 2


def slugify(name: str) -> str:
    """A safe, PEP 503-ish project slug: lowercase, hyphenated, ascii."""
    lowered = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "project"


@dataclass(frozen=True)
class ScaffoldResult:
    path: Path
    outcome: str  # "written" | "skipped"
    detail: str


PYPROJECT_TEMPLATE = """[project]
name = "{name}"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.6"]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
"""

JUSTFILE_TEMPLATE = """set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Install project dependencies.
install:
    uv sync --all-groups

# Run the test suite.
test:
    uv run pytest -q

# Lint and format check.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-fix lint and formatting.
fmt:
    uv run ruff format .
    uv run ruff check --fix .
"""


def render_pyproject(name: str, description: str = "") -> str:
    """A generic, valid pyproject.toml body. ``name`` is slugified; nothing
    else about the target project is assumed or required."""
    slug = slugify(name)
    if not _NAME_PATTERN.match(slug):
        slug = "project"
    safe_description = (description or "A project managed with A.W.I.N.O.").replace('"', "'")
    return PYPROJECT_TEMPLATE.format(name=slug, description=safe_description)


def render_justfile() -> str:
    """A generic justfile with install/test/lint/fmt. Nothing project-specific."""
    return JUSTFILE_TEMPLATE


def scaffold(
    root: Path, name: str, *, description: str = "", overwrite: bool = False
) -> list[ScaffoldResult]:
    """Write pyproject.toml and a justfile into ``root`` if they do not
    already exist. Never overwrites an existing file unless ``overwrite`` is
    explicitly passed - scaffolding a fresh project must not silently
    clobber one that already has its own configuration.
    """
    results: list[ScaffoldResult] = []
    for filename, content in (
        ("pyproject.toml", render_pyproject(name, description)),
        ("justfile", render_justfile()),
    ):
        target = root / filename
        if target.is_file() and not overwrite:
            results.append(
                ScaffoldResult(target, "skipped", f"{filename} already exists; not overwritten")
            )
            continue
        target.write_text(content, encoding="utf-8", newline="")
        results.append(ScaffoldResult(target, "written", f"{filename} scaffolded"))
    return results
