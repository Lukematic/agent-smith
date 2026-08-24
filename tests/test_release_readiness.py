from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from smith import cli
from smith.harness import Harness, Target, install, status
from smith.paths import SmithPaths

runner = CliRunner()


def test_canonical_constitution_and_legacy_pointer_exist() -> None:
    canonical = Path("AWINO.md").read_text(encoding="utf-8")
    legacy = Path("AGENT_SMITH.md").read_text(encoding="utf-8")

    assert canonical.startswith("# A.W.I.N.O.")
    assert "## 16. Self-healing" in canonical
    assert "Deprecated compatibility pointer" in legacy
    assert "AWINO.md" in legacy
    assert "## 2. Non-negotiable principles" not in legacy


def test_source_discovery_selects_canonical_constitution() -> None:
    paths = SmithPaths.discover(Path.cwd())
    assert paths.constitution == Path.cwd() / "AWINO.md"


def test_active_personas_load_only_canonical_constitution() -> None:
    for relative_path in ("agents/awino.md", "agents/agent-smith.md"):
        persona = Path(relative_path).read_text(encoding="utf-8")
        assert "$AWINO/AWINO.md" in persona
        assert "$AWINO/AGENT_SMITH.md" not in persona


def test_built_wheel_bundles_canonical_and_legacy_constitutions(tmp_path: Path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel_path = next(tmp_path.glob("awino_harness-*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
    assert "smith/_bundle/AWINO.md" in names
    assert "smith/_bundle/AGENT_SMITH.md" in names


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


def test_bootstrap_prefers_awino_environment_with_legacy_fallback() -> None:
    powershell = Path("bootstrap.ps1").read_text(encoding="utf-8")
    shell = Path("bootstrap.sh").read_text(encoding="utf-8")

    for canonical, legacy in (
        ("AWINO_REPO", "SMITH_REPO"),
        ("AWINO_DIR", "SMITH_DIR"),
        ("AWINO_REF", "SMITH_REF"),
    ):
        assert powershell.index(canonical) < powershell.index(legacy)
        assert shell.index(canonical) < shell.index(legacy)


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
