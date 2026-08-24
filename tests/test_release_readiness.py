from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from smith import cli
from smith.harness import Harness, Target, install, status

runner = CliRunner()


def test_canonical_and_deprecated_entry_points_are_declared() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "awino-harness"' in pyproject
    assert 'awino = "smith.cli:app"' in pyproject
    assert 'smith = "smith.cli:deprecated_smith_entry"' in pyproject


def test_canonical_version_flag() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == "awino 0.3.0\n"


def test_installer_and_release_automation_use_canonical_command() -> None:
    paths = [
        Path("install.ps1"),
        Path("install.sh"),
        Path("bootstrap.ps1"),
        Path("bootstrap.sh"),
        Path("justfile"),
        Path(".github/workflows/ci.yml"),
        Path(".github/workflows/publish.yml"),
    ]
    forbidden = ("uv run smith", " smith onboard", " smith plan", " smith doctor")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not any(item in text for item in forbidden), path


def test_copilot_status_reports_no_skills_mechanism(tmp_path: Path) -> None:
    smith_home = tmp_path / "source"
    (smith_home / "agents").mkdir(parents=True)
    (smith_home / "agents" / "awino.md").write_text(
        "---\nname: awino\ndescription: test\n---\nbody\n", encoding="utf-8"
    )
    target = Target(Harness.COPILOT, tmp_path / "prompts", "global")

    actions = install(smith_home, target)
    row = next(item for item in status(tmp_path, targets=[target]) if item[0] == target)

    assert any("has no skills mechanism" in action.detail for action in actions)
    assert row[1]
    assert row[2] == "persona; GitHub Copilot has no skills mechanism"


def test_fetch_404_is_a_clean_cli_error(monkeypatch) -> None:
    class MissingStore:
        def __init__(self, paths):
            pass

        def fetch(self, path, source, force=False):
            from smith.knowledge import FetchError

            raise FetchError(source, path, 404)

    monkeypatch.setattr(cli, "KnowledgeStore", MissingStore)
    result = runner.invoke(cli.app, ["fetch", "chapters/missing.md"])

    assert result.exit_code == 1
    assert "FETCH_FAILED" in result.stdout
    assert "404" in result.stdout
    assert "Traceback" not in result.stdout


def test_delegate_malformed_json_prints_schema_help(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    plan = project / "plan.json"
    plan.write_text('{"assignments": [{"id": "worker"}]}', encoding="utf-8")
    monkeypatch.setenv("SMITH_PROJECT", str(project))

    result = runner.invoke(cli.app, ["delegate", str(plan), "--dry-run"])

    assert result.exit_code == 2
    assert "INVALID_PLAN" in result.stdout
    assert '"objective"' in result.stdout
    assert '"verify"' in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_alias_warning_behavior_from_isolated_python(tmp_path: Path) -> None:
    script = (
        "import os; from smith import cli; "
        "cli.app=lambda: print('ran'); cli.deprecated_smith_entry()"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"PATH": str(Path(sys.executable).parent)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ran"
    assert "DEPRECATED" in result.stderr
