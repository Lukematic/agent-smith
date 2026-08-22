"""Toolchain detection must describe the target project, not Smith's own process."""

from __future__ import annotations

from pathlib import Path

from smith.toolchain import Manager, Toolchain


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
