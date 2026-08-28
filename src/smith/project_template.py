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
