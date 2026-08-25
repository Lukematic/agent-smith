from __future__ import annotations

import subprocess
from pathlib import Path

from smith.onboarding import ProjectIntent, WorkflowPolicy
from smith.project_guard import pre_tool_decision, project_context
from smith.session_state import bind_run, start


def intent() -> ProjectIntent:
    return ProjectIntent(
        mission="Ship traceable fixes.",
        goals=["close the linked issue"],
        tenets=["No issue, no branch"],
        expectations=["update release notes"],
        source="confirmed",
        workflow=WorkflowPolicy(
            one_task_per_session=True,
            planning_interview="adaptive-grill",
            issue_required=True,
            issue_pattern=r"TREAD-\d+",
            base_branch="develop",
            branch_pattern=r"(?:bugfix|feature)/TREAD-\d+-[a-z0-9-]+",
            changelog_file="CHANGELOG.md",
        ),
    )


def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "develop"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changes\n", encoding="utf-8")
    subprocess.run(["git", "add", "CHANGELOG.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
    )
    return tmp_path


def payload(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_context_injects_confirmed_project_memory() -> None:
    text = project_context(intent())
    assert "No issue, no branch" in text
    assert "one task only" in text
    assert "adaptive-grill" in text


def test_branch_guard_blocks_wrong_name(tmp_path: Path) -> None:
    project = git_repo(tmp_path)
    result = pre_tool_decision(intent(), payload("git switch -c quick-fix"), project)
    assert result is not None
    assert "does not match" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_branch_guard_allows_matching_name_from_base(tmp_path: Path) -> None:
    project = git_repo(tmp_path)
    result = pre_tool_decision(
        intent(), payload("git switch -c bugfix/TREAD-1512-county-fix develop"), project
    )
    assert result is None


def test_commit_guard_requires_changelog(tmp_path: Path) -> None:
    project = git_repo(tmp_path)
    subprocess.run(
        ["git", "switch", "-c", "bugfix/TREAD-1512-county-fix"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    (project / "code.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "code.py"], cwd=project, check=True)
    result = pre_tool_decision(intent(), payload('git commit -m "fix"'), project)
    assert result is not None
    assert "CHANGELOG.md" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_one_task_per_session_rejects_second_run(tmp_path: Path) -> None:
    start(tmp_path, "session-1")
    bind_run(tmp_path, "run-1", enforce_one_task=True)
    try:
        bind_run(tmp_path, "run-2", enforce_one_task=True)
    except RuntimeError as exc:
        assert "start a new Claude Code session" in str(exc)
    else:
        raise AssertionError("second task was accepted in one session")
