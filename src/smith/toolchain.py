"""Toolchain detection: adapt to the project, do not dictate to it.

A.W.I.N.O. runs inside other people's repositories. A tool that demands uv in a Poetry
project, or pytest in a Jest project, is not a harness: it is an obstacle. So the
commands A.W.I.N.O. runs for its gates are *discovered* from what the project actually
uses, with a documented preference order and an explicit fallback.

The preference order is not arbitrary. It follows the strongest available
guarantee:

1. A committed lockfile plus its manager, because that reproduces exactly.
2. An activated or in-project virtual environment, because it is already correct.
3. A bare interpreter, which works but reproduces nothing.

Every resolution reports *why* it chose what it chose, because a silent wrong
guess about how to run tests produces a green gate that proves nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from pathlib import Path


class Manager(StrEnum):
    """How Python dependencies are managed in this project."""

    UV = "uv"
    POETRY = "poetry"
    PDM = "pdm"
    HATCH = "hatch"
    PIPENV = "pipenv"
    CONDA = "conda"
    VENV = "venv"
    SYSTEM = "system"
    NONE = "none"

    @property
    def reproducible(self) -> bool:
        """Whether this manager pins transitive dependencies."""
        return self in {Manager.UV, Manager.POETRY, Manager.PDM, Manager.PIPENV}


class Linter(StrEnum):
    RUFF = "ruff"
    FLAKE8 = "flake8"
    PYLINT = "pylint"
    ESLINT = "eslint"
    NONE = "none"


class Tester(StrEnum):
    PYTEST = "pytest"
    UNITTEST = "unittest"
    NPM = "npm"
    CARGO = "cargo"
    GO = "go"
    NONE = "none"


class Runner(StrEnum):
    """Task runner, if the project has one."""

    JUST = "just"
    MAKE = "make"
    NPM_SCRIPTS = "npm-scripts"
    NONE = "none"


@dataclass(frozen=True)
class Tool:
    """A resolved command plus the reason it was chosen."""

    command: str | None
    reason: str
    available: bool

    @property
    def usable(self) -> bool:
        return self.available and bool(self.command)


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


@dataclass
class Toolchain:
    """What this project actually uses, discovered rather than assumed."""

    root: Path

    # ── raw signals ──────────────────────────────────────────────────────────
    @cached_property
    def pyproject(self) -> dict:
        path = self.root / "pyproject.toml"
        if not path.is_file():
            return {}
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return {}

    @cached_property
    def package_json(self) -> dict:
        path = self.root / "package.json"
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def has(self, *names: str) -> bool:
        return any((self.root / name).exists() for name in names)

    @property
    def in_project_venv(self) -> Path | None:
        for name in (".venv", "venv", ".virtualenv"):
            candidate = self.root / name
            marker = candidate / ("Scripts" if os.name == "nt" else "bin")
            if marker.is_dir():
                return candidate
        return None

    @property
    def active_venv(self) -> Path | None:
        value = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX")
        if not value:
            return None
        candidate = Path(value).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:
            # A.W.I.N.O. itself normally runs from its own uv environment. Treating
            # that as the target project's environment caused sparse projects to
            # inherit A.W.I.N.O.'s .venv and fall back to pip. Only an environment
            # inside the target project is evidence about that project.
            return None
        return candidate

    # ── dependency manager ───────────────────────────────────────────────────
    @cached_property
    def manager(self) -> tuple[Manager, str]:
        """Pick the manager by strongest guarantee, not by preference.

        A lockfile is evidence of intent: someone committed it, so reproducing it
        is what the project expects. Only when no lock exists does A.W.I.N.O. fall back
        to whatever environment happens to be present.
        """
        tool = self.pyproject.get("tool", {})

        if self.has("uv.lock") and _have("uv"):
            return Manager.UV, "uv.lock is committed and uv is installed"
        if self.has("poetry.lock") and _have("poetry"):
            return Manager.POETRY, "poetry.lock is committed and poetry is installed"
        if self.has("pdm.lock") and _have("pdm"):
            return Manager.PDM, "pdm.lock is committed and pdm is installed"
        if self.has("Pipfile.lock") and _have("pipenv"):
            return Manager.PIPENV, "Pipfile.lock is committed and pipenv is installed"

        # Declared but unlocked, or locked but the manager is missing.
        if "uv" in tool and _have("uv"):
            return Manager.UV, "[tool.uv] declared in pyproject and uv is installed"
        if "poetry" in tool and _have("poetry"):
            return Manager.POETRY, "[tool.poetry] declared in pyproject and poetry is installed"
        if "hatch" in tool and _have("hatch"):
            return Manager.HATCH, "[tool.hatch] declared in pyproject and hatch is installed"
        if self.has("environment.yml", "environment.yaml") and _have("conda"):
            return Manager.CONDA, "a conda environment file is present"

        # For an uninitialized Python project with a pyproject, uv is the safest
        # default when available: it creates an isolated environment and lockfile
        # instead of installing into the system interpreter. Existing lockfiles
        # and declared managers above always win.
        if self.has("pyproject.toml") and _have("uv"):
            return Manager.UV, "Python project has no manager yet; defaulting to available uv"

        if self.active_venv:
            return Manager.VENV, f"an environment is already active at {self.active_venv.name}"
        if self.in_project_venv:
            return Manager.VENV, f"an in-project environment exists at {self.in_project_venv.name}"

        if self.has("uv.lock") or self.has("poetry.lock"):
            missing = "uv" if self.has("uv.lock") else "poetry"
            return Manager.NONE, f"{missing} lockfile present but {missing} is not installed"

        if _have("python") or _have("python3"):
            return Manager.SYSTEM, "no environment found, falling back to the system interpreter"
        return Manager.NONE, "no Python interpreter found"

    @property
    def run_prefix(self) -> str:
        """How to run a command inside this project's environment."""
        manager, _ = self.manager
        return {
            Manager.UV: "uv run ",
            Manager.POETRY: "poetry run ",
            Manager.PDM: "pdm run ",
            Manager.HATCH: "hatch run ",
            Manager.PIPENV: "pipenv run ",
            Manager.CONDA: "conda run ",
            Manager.VENV: "",
            Manager.SYSTEM: "",
            Manager.NONE: "",
        }[manager]

    @property
    def install_command(self) -> Tool:
        """The command that makes the environment match the project's declaration."""
        manager, reason = self.manager
        table = {
            Manager.UV: "uv sync --all-groups",
            Manager.POETRY: "poetry install --with dev",
            Manager.PDM: "pdm install -G :all",
            Manager.HATCH: "hatch env create",
            Manager.PIPENV: "pipenv install --dev",
            Manager.CONDA: "conda env update --prune",
        }
        if manager in table:
            return Tool(table[manager], reason, available=True)
        if manager is Manager.VENV:
            pip = (
                "pip install -e ."
                if self.has("pyproject.toml", "setup.py")
                else "pip install -r requirements.txt"
            )
            if not self.has("pyproject.toml", "setup.py", "requirements.txt"):
                return Tool(None, "no installable declaration found", available=False)
            return Tool(pip, f"{reason}, using pip", available=True)
        if manager is Manager.SYSTEM:
            return Tool(
                "python -m venv .venv",
                "no environment exists yet, so create one before installing",
                available=True,
            )
        return Tool(None, reason, available=False)

    # ── linter ───────────────────────────────────────────────────────────────
    @cached_property
    def linter(self) -> tuple[Linter, str]:
        """Ruff is preferred, but only when the project has actually adopted it."""
        tool = self.pyproject.get("tool", {})
        if "ruff" in tool:
            return Linter.RUFF, "[tool.ruff] is configured in pyproject"
        if self.has(".ruff.toml", "ruff.toml"):
            return Linter.RUFF, "a ruff config file is present"
        if "flake8" in tool or self.has(".flake8", "setup.cfg", "tox.ini"):
            return Linter.FLAKE8, "flake8 configuration is present"
        if "pylint" in tool or self.has(".pylintrc"):
            return Linter.PYLINT, "pylint configuration is present"
        if self.package_json and self.has(".eslintrc", ".eslintrc.json", "eslint.config.js"):
            return Linter.ESLINT, "eslint configuration is present"
        if self.has("pyproject.toml") and _have("ruff"):
            return Linter.RUFF, "a Python project with no linter configured, and ruff is available"
        return Linter.NONE, "no linter configuration found"

    @property
    def lint_command(self) -> Tool:
        linter, reason = self.linter
        prefix = self.run_prefix
        table = {
            Linter.RUFF: f"{prefix}ruff check .",
            Linter.FLAKE8: f"{prefix}flake8",
            Linter.PYLINT: f"{prefix}pylint .",
            Linter.ESLINT: "npm run lint",
        }
        if linter is Linter.NONE:
            return Tool(None, reason, available=False)
        return Tool(table[linter], reason, available=True)

    @property
    def format_command(self) -> Tool:
        linter, reason = self.linter
        prefix = self.run_prefix
        if linter is Linter.RUFF:
            return Tool(
                f"{prefix}ruff format .",
                f"{reason}, and ruff formats as well as lints",
                available=True,
            )
        if "black" in self.pyproject.get("tool", {}):
            return Tool(f"{prefix}black .", "[tool.black] is configured", available=True)
        if linter is Linter.ESLINT:
            return Tool("npm run format", reason, available=True)
        return Tool(None, "no formatter configured", available=False)

    # ── tests ────────────────────────────────────────────────────────────────
    @cached_property
    def tester(self) -> tuple[Tester, str]:
        if "pytest" in self.pyproject.get("tool", {}):
            return Tester.PYTEST, "[tool.pytest.ini_options] is configured"
        if self.has("pytest.ini", "tox.ini", "conftest.py"):
            return Tester.PYTEST, "pytest configuration is present"
        if self.has("Cargo.toml"):
            return Tester.CARGO, "this is a Rust project"
        if self.has("go.mod"):
            return Tester.GO, "this is a Go project"
        if self.package_json.get("scripts", {}).get("test"):
            return Tester.NPM, "package.json declares a test script"
        if self.has("tests", "test"):
            return Tester.PYTEST if _have("pytest") else Tester.UNITTEST, "a tests directory exists"
        return Tester.NONE, "no test configuration or directory found"

    @property
    def test_command(self) -> Tool:
        tester, reason = self.tester
        prefix = self.run_prefix
        table = {
            Tester.PYTEST: f"{prefix}pytest -q",
            Tester.UNITTEST: f"{prefix}python -m unittest discover",
            Tester.NPM: "npm test",
            Tester.CARGO: "cargo test",
            Tester.GO: "go test ./...",
        }
        if tester is Tester.NONE:
            return Tool(None, reason, available=False)
        return Tool(table[tester], reason, available=True)

    # ── task runner ──────────────────────────────────────────────────────────
    @cached_property
    def runner(self) -> tuple[Runner, str]:
        if self.has("justfile", "Justfile", ".justfile"):
            if _have("just"):
                return Runner.JUST, "a justfile exists and just is installed"
            return Runner.NONE, "a justfile exists but just is not installed"
        if self.has("Makefile", "makefile"):
            return Runner.MAKE, "a Makefile exists"
        if self.package_json.get("scripts"):
            return Runner.NPM_SCRIPTS, "package.json declares scripts"
        return Runner.NONE, "no task runner found"

    @property
    def recipes(self) -> list[str]:
        """Recipe names the runner exposes, for reusing the project's own gates."""
        runner, _ = self.runner
        if runner is Runner.JUST:
            import re

            for name in ("justfile", "Justfile", ".justfile"):
                path = self.root / name
                if path.is_file():
                    text = path.read_text(encoding="utf-8")
                    return sorted(
                        set(re.findall(r"^([a-z][a-z0-9-]*)(?:\s+[A-Z_]+)*:", text, re.MULTILINE))
                    )
        if runner is Runner.NPM_SCRIPTS:
            return sorted(self.package_json.get("scripts", {}))
        if runner is Runner.MAKE:
            import re

            for name in ("Makefile", "makefile"):
                path = self.root / name
                if path.is_file():
                    text = path.read_text(encoding="utf-8")
                    return sorted(set(re.findall(r"^([a-zA-Z][\w-]*):", text, re.MULTILINE)))
        return []

    def gate_command(self, gate: str) -> Tool:
        """Resolve a gate to a command, preferring the project's own recipe.

        If the project defines ``just test``, use it. The project's own entry point
        encodes setup A.W.I.N.O. cannot infer, such as starting a database or setting an
        environment variable.
        """
        runner, runner_reason = self.runner
        recipes = self.recipes
        if gate in recipes:
            invoke = {
                Runner.JUST: f"just {gate}",
                Runner.MAKE: f"make {gate}",
                Runner.NPM_SCRIPTS: f"npm run {gate}",
            }.get(runner)
            if invoke:
                return Tool(
                    invoke, f"{runner_reason} and defines a '{gate}' recipe", available=True
                )

        return {
            "test": self.test_command,
            "lint": self.lint_command,
            "format": self.format_command,
            "install": self.install_command,
        }.get(gate, Tool(None, f"no resolution for gate '{gate}'", available=False))

    # ── reporting ────────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Tool | str]:
        manager, manager_reason = self.manager
        return {
            "manager": Tool(str(manager), manager_reason, available=manager is not Manager.NONE),
            "install": self.install_command,
            "lint": self.gate_command("lint"),
            "format": self.gate_command("format"),
            "test": self.gate_command("test"),
        }

    @property
    def blocking_gaps(self) -> list[str]:
        """What genuinely prevents A.W.I.N.O. from gating work here."""
        gaps: list[str] = []
        manager, reason = self.manager
        if manager is Manager.NONE:
            gaps.append(f"no usable dependency manager: {reason}")
        if not self.gate_command("test").usable:
            gaps.append(f"no test command: {self.test_command.reason}")
        if not self.gate_command("lint").usable:
            gaps.append(f"no lint command: {self.lint_command.reason}")
        return gaps

    @property
    def advice(self) -> list[str]:
        """Concrete next steps, phrased for the project's existing conventions."""
        out: list[str] = []
        manager, _ = self.manager
        if manager is Manager.NONE and (self.has("uv.lock") or self.has("poetry.lock")):
            which = "uv" if self.has("uv.lock") else "poetry"
            out.append(f"install {which} so the committed lockfile can be reproduced")
        if manager is Manager.SYSTEM:
            out.append("create an environment: python -m venv .venv, then install dependencies")
        if not manager.reproducible and manager is not Manager.NONE:
            out.append("no lockfile, so gate results are not reproducible across machines")
        linter, _ = self.linter
        if linter is Linter.NONE and self.has("pyproject.toml"):
            out.append("add [tool.ruff] to pyproject.toml to enable the lint gate")
        if not self.test_command.usable:
            out.append("add tests, because without an executable check autonomy stays supervised")
        runner, _ = self.runner
        if runner is Runner.NONE:
            out.append("consider a justfile so gate commands are discoverable")
        return out
