from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
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


def test_seeds_show_wrapper_is_parsed() -> None:
    payload = {
        "success": True,
        "command": "show",
        "issue": {
            "id": "ISSUE-1",
            "title": "Tracked",
            "status": "open",
            "type": "task",
            "priority": 2,
        },
    }

    issues = cli.seeds.Seeds._issues_from(payload)

    assert [issue.id for issue in issues] == ["ISSUE-1"]


def test_skills_route_and_gate_are_truthful_subprocess_workflows(tmp_path: Path) -> None:
    skill = tmp_path / ".kilo" / "skills" / "awino-local" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: awino-local\ndescription: Analyze local widgets\n---\n",
        encoding="utf-8",
    )
    opened = run_cli(tmp_path, "gate", "open", "question", "widget work")
    assert opened.returncode == 0, opened.stdout + opened.stderr

    routed = run_cli(tmp_path, "skills", "--route", "analyze widgets", "--json")
    assert routed.returncode == 0, routed.stdout + routed.stderr
    assert '"name": "awino-local"' in routed.stdout
    status = run_cli(tmp_path, "gate", "status")
    assert "skills loaded: none recorded" in status.stdout

    unknown = run_cli(tmp_path, "gate", "skill", "not-real", "--state", "used")
    assert unknown.returncode == 2
    assert "unknown skill" in unknown.stdout

    used = run_cli(
        tmp_path,
        "gate",
        "skill",
        "awino-local",
        "--state",
        "used",
        "--reason",
        "analyzed widgets",
    )
    assert used.returncode == 0, used.stdout + used.stderr
    assert "SKILL_USED  awino-local" in used.stdout


def test_nuclear_battery_deliverable_substitution_incident_is_refused(tmp_path: Path) -> None:
    """Real regression for the incident this feature exists to prevent.

    An agent was asked to run 10 indicator scans for Nuclear Battery. It ran
    3, invented the status "honesty_boundary" to describe the other 7, and
    reported the run as implemented_and_tested. Both the escape-hatch
    vocabulary and the partial-as-complete report must now be mechanically
    refused end-to-end through the real CLI, not just the ledger unit tests.
    """
    opened = run_cli(
        tmp_path, "gate", "open", "research", "run 10 indicator scans for Nuclear Battery"
    )
    assert opened.returncode == 0, opened.stdout + opened.stderr

    escape_hatch = run_cli(
        tmp_path,
        "gate",
        "record",
        "researched",
        "--attest",
        "7 of 10 indicators marked honesty_boundary - cannot relabel TRISO evidence",
    )
    assert escape_hatch.returncode == 1
    assert "ESCAPE_HATCH_TERM" in escape_hatch.stdout

    honest_attempt = run_cli(
        tmp_path,
        "gate",
        "record",
        "researched",
        "--attest",
        "ran 3 of 10 indicator scans; remaining 7 not yet attempted",
    )
    assert honest_attempt.returncode == 0, honest_attempt.stdout + honest_attempt.stderr

    completeness = run_cli(
        tmp_path,
        "gate",
        "record-completeness",
        "--achieved",
        "3",
        "--stated",
        "10",
        "--unit",
        "indicator(s)",
    )
    assert completeness.returncode == 1
    assert "DELIVERABLE_INCOMPLETE" in completeness.stdout

    close_attempt = run_cli(tmp_path, "gate", "close")
    assert close_attempt.returncode == 1
    assert "REFUSED  DELIVERABLE_INCOMPLETE 3/10 indicator(s) achieved" in close_attempt.stdout
    assert "run 10 indicator scans for Nuclear Battery" in close_attempt.stdout
    assert "You may not report this work as complete." in close_attempt.stdout

    completed = run_cli(
        tmp_path,
        "gate",
        "record-completeness",
        "--achieved",
        "10",
        "--stated",
        "10",
        "--unit",
        "indicator(s)",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "COMPLETENESS_MET  10/10 indicator(s)" in completed.stdout

    closed = run_cli(tmp_path, "gate", "close")
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert "COMPLETE" in closed.stdout
    assert "run 10 indicator scans for Nuclear Battery" in closed.stdout


def test_three_strikes_then_pending_decision_then_pause_requires_human(
    tmp_path: Path,
) -> None:
    """Terminal-state workflow, exercised through the real subprocess CLI.

    open run -> record a failing gate 3 times -> a checkpoint with a pending
    decision now exists automatically -> attempting close is refused ->
    resolving the decision -> gate pause requires --by -> gate status shows
    terminal_state=paused.
    """
    opened = run_cli(tmp_path, "gate", "open", "research", "flaky investigation")
    assert opened.returncode == 0, opened.stdout + opened.stderr

    for expected_attempt in range(1, 4):
        failed = run_cli(
            tmp_path,
            "gate",
            "record",
            "researched",
            "--cmd",
            f'"{sys.executable}" -c "import sys; sys.exit(1)"',
        )
        assert failed.returncode == 1
        assert f"attempt={expected_attempt}" in failed.stdout

    resumed = run_cli(tmp_path, "resume")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "PENDING" in resumed.stdout
    assert "researched" in resumed.stdout

    blocked = run_cli(tmp_path, "gate", "block")
    assert blocked.returncode == 0, blocked.stdout + blocked.stderr
    assert "BLOCKED" in blocked.stdout

    status_blocked = run_cli(tmp_path, "gate", "status")
    assert "terminal_state: blocked" in status_blocked.stdout

    close_attempt = run_cli(tmp_path, "gate", "close")
    assert close_attempt.returncode == 1
    assert "REFUSED" in close_attempt.stdout
    assert "THREE_STRIKES" in close_attempt.stdout

    resolved = run_cli(tmp_path, "gate", "decide", "retry_with_new_run", "--by", "reviewer")
    assert resolved.returncode == 0, resolved.stdout + resolved.stderr

    missing_by = run_cli(tmp_path, "gate", "pause", "--by", "", "--reason", "waiting")
    assert missing_by.returncode == 1
    assert "PAUSE_REQUIRES_HUMAN" in missing_by.stdout

    paused = run_cli(
        tmp_path, "gate", "pause", "--by", "human-reviewer", "--reason", "waiting on input"
    )
    assert paused.returncode == 0, paused.stdout + paused.stderr
    assert "PAUSED" in paused.stdout
    assert "human-reviewer" in paused.stdout

    status = run_cli(tmp_path, "gate", "status")
    assert status.returncode == 0, status.stdout + status.stderr
    assert "terminal_state: paused" in status.stdout


def test_gate_close_refuses_open_seed_then_succeeds_after_work_close(tmp_path: Path) -> None:
    """A COMPLETE terminal state requires the linked Seed to actually be closed.

    open run linked to a real disposable Seed -> satisfy all gates -> gate
    close is refused with the Seed still open -> work-close closes the Seed
    -> gate close now succeeds with terminal_state=complete.
    """
    if (
        subprocess.run(["sd", "--version"], capture_output=True, check=False).returncode != 0
    ):  # pragma: no cover - environment without the optional tool
        pytest.skip("live Seed-linkage workflow requires the optional sd executable")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["sd", "init", "--json"], cwd=tmp_path, capture_output=True, check=True)
    created = subprocess.run(
        ["sd", "create", "--title", "Seed for terminal-state test", "--type", "task", "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    issue_id = json.loads(created.stdout)["id"]

    opened = run_cli(
        tmp_path, "gate", "open", "research", "verify seed linkage", "--issue", issue_id
    )
    assert opened.returncode == 0, opened.stdout + opened.stderr
    run_id = opened.stdout.split()[1]
    assert f"issue: {issue_id}" in opened.stdout

    recorded = run_cli(
        tmp_path,
        "gate",
        "record",
        "researched",
        "--cmd",
        f'"{sys.executable}" -c "print(1)"',
        "--run",
        run_id,
    )
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr

    close_before = run_cli(tmp_path, "gate", "close", "--run", run_id)
    assert close_before.returncode == 1
    assert "REFUSED" in close_before.stdout
    assert "SEED_NOT_CLOSED" in close_before.stdout
    assert issue_id in close_before.stdout
    assert "work-close" in close_before.stdout

    work_closed = run_cli(tmp_path, "work-close", "--run", run_id)
    assert work_closed.returncode == 0, work_closed.stdout + work_closed.stderr
    assert f"CLOSED  {issue_id}" in work_closed.stdout

    close_after = run_cli(tmp_path, "gate", "close", "--run", run_id)
    assert close_after.returncode == 0, close_after.stdout + close_after.stderr
    assert "COMPLETE" in close_after.stdout

    status = run_cli(tmp_path, "gate", "status", "--run", run_id)
    assert "terminal_state: complete" in status.stdout


def test_gate_close_with_no_linked_issue_only_requires_gate_close(tmp_path: Path) -> None:
    """COMPLETE with no linked Seed requires only that gate close succeed."""
    opened = run_cli(tmp_path, "gate", "open", "question", "no tracker involved")
    assert opened.returncode == 0, opened.stdout + opened.stderr

    closed = run_cli(tmp_path, "gate", "close")
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert "COMPLETE" in closed.stdout

    status = run_cli(tmp_path, "gate", "status")
    assert "terminal_state: complete" in status.stdout
