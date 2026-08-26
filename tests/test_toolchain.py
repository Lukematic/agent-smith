"""Toolchain detection must describe the target project, not Smith's own process."""

from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from smith import cli
from smith.toolchain import Manager, Runner, Toolchain


class TestEnvironmentIsolation:
    def test_external_active_venv_is_not_attributed_to_project(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname="x"\nversion="0.1"\nrequires-python=">=3.12"\n',
            encoding="utf-8",
        )
        external = tmp_path / "smith-home" / ".venv"
        external.mkdir(parents=True)
        monkeypatch.setenv("VIRTUAL_ENV", str(external))
        chain = Toolchain(project)
        assert chain.active_venv is None
        assert chain.manager[0] is Manager.UV
        assert "defaulting" in chain.manager[1]

    def test_project_local_active_venv_is_respected(self, tmp_path: Path, monkeypatch) -> None:
        project = tmp_path / "project"
        project.mkdir()
        local = project / ".venv"
        local.mkdir()
        monkeypatch.setenv("VIRTUAL_ENV", str(local))
        chain = Toolchain(project)
        assert chain.active_venv == local.resolve()
        assert chain.manager[0] is Manager.VENV


class TestManagerPrecedence:
    def test_existing_uv_lock_beats_active_venv(self, tmp_path: Path, monkeypatch) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "uv.lock").write_text("", encoding="utf-8")
        local = project / ".venv"
        local.mkdir()
        monkeypatch.setenv("VIRTUAL_ENV", str(local))
        chain = Toolchain(project)
        assert chain.manager[0] is Manager.UV

    def test_existing_poetry_declaration_is_not_replaced_by_uv(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text("[tool.poetry]\nname='x'\n", encoding="utf-8")

        # Make manager availability deterministic instead of depending on the
        # developer's machine.
        from smith import toolchain

        monkeypatch.setattr(toolchain, "_have", lambda name: name in {"poetry", "uv"})
        chain = Toolchain(project)
        assert chain.manager[0] is Manager.POETRY

    def test_in_project_venv_beats_generic_uv_default(self, tmp_path: Path, monkeypatch) -> None:
        project = tmp_path / "project"
        (project / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
        (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        from smith import toolchain

        monkeypatch.setattr(toolchain, "_have", lambda name: name in {"uv", "python"})
        assert Toolchain(project).manager[0] is Manager.VENV

    def test_pdm_declaration_is_detected(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pdm]\ndistribution=true\n", encoding="utf-8"
        )
        from smith import toolchain

        monkeypatch.setattr(toolchain, "_have", lambda name: name in {"pdm", "uv"})
        assert Toolchain(tmp_path).manager[0] is Manager.PDM

    def test_non_python_project_never_uses_system_python(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "package.json").write_text(
            '{"scripts":{"test":"node test.js"}}', encoding="utf-8"
        )
        from smith import toolchain

        monkeypatch.setattr(toolchain, "_have", lambda name: name == "python")
        assert Toolchain(tmp_path).manager[0] is Manager.NONE

    def test_unavailable_just_falls_through_to_make(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "justfile").write_text("test:\n", encoding="utf-8")
        (tmp_path / "Makefile").write_text("test:\n", encoding="utf-8")
        from smith import toolchain

        monkeypatch.setattr(toolchain, "_have", lambda name: name == "make")
        assert Toolchain(tmp_path).runner[0] is Runner.MAKE

    def test_makefile_requires_make_executable(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "Makefile").write_text("test:\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            '{"scripts":{"test":"node test.js"}}', encoding="utf-8"
        )
        from smith import toolchain

        monkeypatch.setattr(toolchain, "_have", lambda name: False)
        assert Toolchain(tmp_path).runner[0] is Runner.NPM_SCRIPTS


class TestCommands:
    def test_uv_default_produces_uv_commands(self, tmp_path: Path, monkeypatch) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            """
[project]
name = "x"
version = "0.1"
requires-python = ">=3.12"
[tool.pytest.ini_options]
[tool.ruff]
""".strip(),
            encoding="utf-8",
        )
        from smith import toolchain

        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.delenv("CONDA_PREFIX", raising=False)
        monkeypatch.setattr(toolchain, "_have", lambda name: name in {"uv", "python"})
        chain = Toolchain(project)
        assert chain.install_command.command == "uv sync --all-groups"
        assert chain.test_command.command == "uv run pytest -q"
        assert chain.lint_command.command == "uv run ruff check ."

    def test_locked_uv_project_without_venv_reports_sync_without_creating_it(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname="x"\nversion="0.1"\nrequires-python=">=3.12"\n',
            encoding="utf-8",
        )
        (project / "uv.lock").write_text("version = 1\nrevision = 3\n", encoding="utf-8")
        monkeypatch.setenv("AWINO_PROJECT", str(project))

        result = CliRunner().invoke(cli.app, ["setup", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "command: uv sync --all-groups" in result.output
        assert "DRY_RUN  nothing executed" in result.output
        assert not (project / ".venv").exists()

    def test_env_explains_uv_with_manager_specific_guidance(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname="x"\nversion="0.1"\nrequires-python=">=3.12"\n',
            encoding="utf-8",
        )
        (project / "uv.lock").write_text("version = 1\nrevision = 3\n", encoding="utf-8")
        (project / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
        monkeypatch.setenv("AWINO_PROJECT", str(project))

        result = CliRunner().invoke(cli.app, ["env"])

        assert result.exit_code == 0, result.output
        assert "manager: uv" in result.output
        assert f"environment: {project / '.venv'}" in result.output
        assert "Run commands with 'uv run'" in result.output
        assert "shell activation is optional" in result.output
