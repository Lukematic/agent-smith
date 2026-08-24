"""Regression tests for `smith gate` commands acting on the correct project.

A live sandbox test (chemical/mechanical/nuclear/physicist/software-engineer
persona walkthroughs) found that `smith gate record --cmd` and
`smith gate check --diff-base` both resolved their working directory to Smith's
own home (`_paths().root`) instead of the target project
(`_workspace().project.root`). This silently ran Smith's own test suite and
reported PASS for a project whose own tests actually failed, and treated a
failed `git diff` (no repo) as "no weakening found" = pass, a false positive.

These tests invoke the real installed CLI via subprocess against a throwaway
project directory, because the bug only manifests through the actual command
resolution path (env vars, cwd, subprocess), not through calling internal
functions directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_smith(
    args: list[str], project_root: Path, smith_home: Path
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["SMITH_PROJECT"] = str(project_root)
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _open_code_run(project: Path, smith_home: Path, objective: str, scope: str):
    plan = project / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    opened = _run_smith(
        ["gate", "open", "code-change", objective, "--scope", scope, "--plan", str(plan)],
        project,
        smith_home,
    )
    if opened.returncode == 0:
        approved = _run_smith(["gate", "plan", "approve", "--by", "test"], project, smith_home)
        assert approved.returncode == 0, approved.stdout + approved.stderr
    return opened


@pytest.fixture
def smith_home() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def toy_project(tmp_path: Path) -> Path:
    """A minimal, real project with its own passing test, distinct from Smith's."""
    project = tmp_path / "toy-project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "toy"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
        "[tool.pytest.ini_options]\n[tool.ruff]\n",
        encoding="utf-8",
    )
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_toy.py").write_text(
        "def test_toy_passes():\n    assert True\n",
        encoding="utf-8",
    )
    return project


class TestGateRecordUsesTargetProject:
    """`smith gate record --cmd` must run in the project being worked on."""

    def test_tested_gate_runs_the_projects_own_tests_not_smiths(
        self, toy_project: Path, smith_home: Path
    ) -> None:
        opened = _open_code_run(toy_project, smith_home, "toy change", "tests/test_toy.py")
        assert opened.returncode == 0, opened.stdout + opened.stderr

        recorded = _run_smith(
            ["gate", "record", "tested", "--cmd", "python -m pytest -q"],
            toy_project,
            smith_home,
        )
        # If the bug regresses, this would silently run Smith's own ~295-test
        # suite instead of the toy project's single test.
        assert "1 passed" in recorded.stdout, recorded.stdout + recorded.stderr
        assert "PASS" in recorded.stdout

    def test_a_real_failure_in_the_target_project_is_reported_as_a_real_failure(
        self, tmp_path: Path, smith_home: Path
    ) -> None:
        """The exact failure mode the sandbox surfaced: a broken import."""
        project = tmp_path / "broken-project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "broken"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
            "[tool.pytest.ini_options]\n",
            encoding="utf-8",
        )
        (project / "tests").mkdir()
        (project / "tests" / "test_broken.py").write_text(
            "from nonexistent_module import thing\n\ndef test_x():\n    assert thing\n",
            encoding="utf-8",
        )

        opened = _open_code_run(project, smith_home, "broken change", "tests/test_broken.py")
        assert opened.returncode == 0, opened.stdout + opened.stderr

        recorded = _run_smith(
            ["gate", "record", "tested", "--cmd", "python -m pytest -q"],
            project,
            smith_home,
        )
        assert recorded.returncode == 1
        assert "FAIL" in recorded.stdout
        assert "ModuleNotFoundError" in recorded.stdout or "nonexistent_module" in recorded.stdout


class TestGateCheckFailsLoudlyWithoutGit:
    """A failed git command must never be silently treated as 'no weakening found'."""

    def test_diff_base_without_a_git_repo_fails_the_gate_check(
        self, toy_project: Path, smith_home: Path
    ) -> None:
        # toy_project deliberately has no .git directory.
        opened = _open_code_run(toy_project, smith_home, "toy change", "tests/test_toy.py")
        assert opened.returncode == 0, opened.stdout + opened.stderr

        checked = _run_smith(["gate", "check", "--diff-base", "HEAD"], toy_project, smith_home)
        # Before the fix, this exited 0 and printed "TESTS_NOT_WEAKENED ok" /
        # "SCOPE_RESPECTED ok" for a command that never actually ran.
        assert checked.returncode == 1, checked.stdout + checked.stderr
        assert "GIT_DIFF_FAILED" in checked.stdout
        assert "TESTS_NOT_WEAKENED  ok" not in checked.stdout
        assert "SCOPE_RESPECTED  ok" not in checked.stdout

    def test_diff_base_with_a_real_git_repo_and_no_changes_passes_cleanly(
        self, toy_project: Path, smith_home: Path
    ) -> None:
        subprocess.run(["git", "init", "-q"], cwd=str(toy_project), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(toy_project), check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "init"],
            cwd=str(toy_project),
            check=True,
        )

        opened = _open_code_run(toy_project, smith_home, "toy change", "tests/test_toy.py")
        assert opened.returncode == 0, opened.stdout + opened.stderr

        checked = _run_smith(["gate", "check", "--diff-base", "HEAD"], toy_project, smith_home)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert "GIT_DIFF_FAILED" not in checked.stdout
