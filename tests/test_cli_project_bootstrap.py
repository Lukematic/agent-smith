from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AWINO_PROJECT"] = str(project)
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_read_only_report_does_not_create_project_state(tmp_path: Path) -> None:
    result = run_cli(tmp_path, "project-bootstrap", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"].startswith("BOOTSTRAP_REQUIRED")
    assert not (tmp_path / ".smith").exists()


def test_confirm_requires_all_explicit_decisions(tmp_path: Path) -> None:
    result = run_cli(tmp_path, "project-bootstrap", "--environment", "skip", "--confirm")
    assert result.returncode == 2
    assert "requires --environment, --tracker, and --runner" in result.stdout


def test_confirm_and_current_round_trip(tmp_path: Path) -> None:
    confirmed = run_cli(
        tmp_path,
        "project-bootstrap",
        "--environment",
        "not-applicable",
        "--tracker",
        "skip",
        "--runner",
        "use-native",
        "--confirm",
        "--by",
        "reviewer",
    )
    assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
    current = run_cli(tmp_path, "project-bootstrap")
    assert current.returncode == 0
    assert "BOOTSTRAP_CURRENT" in current.stdout
    assert "confirmed_by=reviewer" in current.stdout


def test_gate_open_snapshots_bootstrap(tmp_path: Path) -> None:
    confirmed = run_cli(
        tmp_path,
        "project-bootstrap",
        "--environment",
        "not-applicable",
        "--tracker",
        "skip",
        "--runner",
        "use-native",
        "--confirm",
    )
    assert confirmed.returncode == 0
    opened = run_cli(tmp_path, "gate", "open", "question", "inspect")
    assert opened.returncode == 0, opened.stdout + opened.stderr
    run_id = opened.stdout.split()[1]
    artifacts = tmp_path / ".smith" / "run" / run_id / "artifacts.jsonl"
    rows = [
        json.loads(ln) for ln in artifacts.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    payload = next(r for r in rows if r["kind"] == "bootstrap")
    assert payload["kind"] == "bootstrap"
    assert payload["payload"]["confirmed_by"] == "human"


def test_install_missing_refuses_when_nothing_is_declared_missing(tmp_path: Path) -> None:
    # No justfile/Makefile at all: there is nothing installing a tool could
    # fix here, so the command must refuse rather than doing nothing silently.
    result = run_cli(
        tmp_path,
        "project-bootstrap",
        "--environment",
        "not-applicable",
        "--tracker",
        "skip",
        "--runner",
        "install-missing",
        "--confirm",
    )
    assert result.returncode == 1
    assert "no declared task-runner file is missing its binary" in result.stdout


def test_inspection_surfaces_the_missing_binary_and_why_it_matters(tmp_path: Path) -> None:
    # A justfile with no just installed anywhere in PATH for this subprocess -
    # simulate by pointing PATH at an empty directory so 'just' genuinely
    # cannot be found, matching a real missing-binary machine state.
    (tmp_path / "justfile").write_text("test:\n    echo hi\n", encoding="utf-8")
    empty_path_dir = tmp_path / "empty-path"
    empty_path_dir.mkdir()
    env = os.environ.copy()
    env["AWINO_PROJECT"] = str(tmp_path)
    env["PATH"] = str(empty_path_dir)
    env.pop("VIRTUAL_ENV", None)
    result = subprocess.run(
        [sys.executable, "-m", "smith.cli", "project-bootstrap"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "just is declared by this project but not installed" in result.stdout
    assert "why install it" in result.stdout


def test_project_scaffold_refuses_against_a_real_multi_project_container(
    tmp_path: Path,
) -> None:
    """End-to-end replay of the exact live incident: running the CLI against
    a folder shaped like the real ai_explained workspace (several
    independent-looking subdirectories) must refuse, not silently write a
    pyproject.toml at the wrong level - which is exactly what happened
    before this guard existed."""
    (tmp_path / "sandbox").mkdir()
    (tmp_path / "sandbox" / ".git").mkdir()
    (tmp_path / "research_idea").mkdir()
    (tmp_path / "smith-install").mkdir()
    (tmp_path / "smith-install" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    result = run_cli(tmp_path, "project-scaffold")

    assert result.returncode == 1
    assert "MULTI_PROJECT_CONTAINER" in result.stdout
    assert not (tmp_path / "pyproject.toml").exists()
    assert not (tmp_path / "justfile").exists()


def test_project_scaffold_proceeds_with_explicit_override(tmp_path: Path) -> None:
    (tmp_path / "sandbox").mkdir()
    (tmp_path / "sandbox" / ".git").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    result = run_cli(tmp_path, "project-scaffold", "--i-know-this-is-the-right-folder")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "pyproject.toml").is_file()


def test_project_scaffold_is_unaffected_for_a_genuine_single_project(tmp_path: Path) -> None:
    result = run_cli(tmp_path, "project-scaffold")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "pyproject.toml").is_file()
