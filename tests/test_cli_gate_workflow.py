from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from smith import cli
from smith.seeds import Issue, SeedsResult, SeedsState


def run_cli(
    project: Path, *args: str, path_prefix: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SMITH_PROJECT"] = str(project)
    env.pop("VIRTUAL_ENV", None)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_planned_contract_requires_plan_and_guards_execution(tmp_path: Path) -> None:
    missing = run_cli(tmp_path, "gate", "open", "code-change", "change")
    assert missing.returncode == 2
    assert "PLAN_REQUIRED" in missing.stdout

    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    opened = run_cli(tmp_path, "gate", "open", "code-change", "change", "--plan", str(plan))
    assert opened.returncode == 0, opened.stdout + opened.stderr

    blocked = run_cli(
        tmp_path, "gate", "record", "tested", "--cmd", f'"{sys.executable}" -c "print(1)"'
    )
    assert blocked.returncode == 1
    assert "PLAN_INVALID: plan has no decision" in blocked.stdout
    assert "Traceback" not in blocked.stderr

    approved = run_cli(tmp_path, "gate", "plan", "approve", "--by", "reviewer")
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "PLAN_APPROVED" in approved.stdout

    recorded = run_cli(
        tmp_path, "gate", "record", "tested", "--cmd", f'"{sys.executable}" -c "print(1)"'
    )
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    assert "PASS" in recorded.stdout


def test_checkpoint_decision_and_resume_round_trip(tmp_path: Path) -> None:
    opened = run_cli(tmp_path, "gate", "open", "question", "choose storage")
    assert opened.returncode == 0
    checkpoint = run_cli(
        tmp_path,
        "gate",
        "checkpoint",
        "--phase",
        "planning",
        "--summary",
        "tradeoff found",
        "--next",
        "wait for review",
        "--pending",
        "Choose storage",
        "--option",
        "json",
        "--option",
        "sqlite",
    )
    assert checkpoint.returncode == 0, checkpoint.stdout + checkpoint.stderr

    resumed = run_cli(tmp_path, "resume")
    assert "PENDING  Choose storage" in resumed.stdout
    assert "next: wait for review" in resumed.stdout

    invalid = run_cli(tmp_path, "gate", "decide", "postgres", "--by", "reviewer")
    assert invalid.returncode == 1
    assert "not a declared option" in invalid.stdout
    assert "Traceback" not in invalid.stderr

    decided = run_cli(tmp_path, "gate", "decide", "sqlite", "--by", "reviewer")
    assert decided.returncode == 0
    resumed = run_cli(tmp_path, "resume")
    assert "decision: sqlite by reviewer" in resumed.stdout


def test_issue_is_validated_linked_and_started_on_first_execution(
    tmp_path: Path, monkeypatch
) -> None:
    started: list[str] = []

    class FakeSeeds:
        def __init__(self, project_root: Path) -> None:
            self.root = project_root

        def state(self):
            return SeedsState.READY, "test tracker"

        def show(self, issue_id: str):
            if issue_id != "ISSUE-1":
                return None
            return Issue("ISSUE-1", "Tracked", "open", "task", 2)

        def start(self, issue_id: str):
            started.append(issue_id)
            return SeedsResult(True, "update", "started")

    monkeypatch.setattr(cli.seeds, "Seeds", FakeSeeds)
    monkeypatch.setenv("SMITH_PROJECT", str(tmp_path))
    runner = CliRunner()

    missing = runner.invoke(cli.app, ["gate", "open", "research", "work", "--issue", "MISSING"])
    assert missing.exit_code == 2
    assert "ISSUE_NOT_FOUND" in missing.stdout

    opened = runner.invoke(cli.app, ["gate", "open", "research", "work", "--issue", "ISSUE-1"])
    assert opened.exit_code == 0, opened.stdout
    run_id = opened.stdout.split()[1]
    run = cli._ledger().load(run_id)
    assert run.issue_id == "ISSUE-1"
    assert run.issue_started_at is None
    assert started == []

    recorded = runner.invoke(
        cli.app,
        ["gate", "record", "researched", "--cmd", f'"{sys.executable}" -c "print(1)"'],
    )
    assert recorded.exit_code == 0, recorded.stdout
    assert cli._ledger().load(run_id).issue_started_at is not None
    assert started == ["ISSUE-1"]
