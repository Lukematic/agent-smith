from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cli(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SMITH_PROJECT"] = str(project)
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def assert_ok(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def quoted_python(script: Path) -> str:
    return subprocess.list2cmdline([sys.executable, str(script)])


def make_project(project: Path) -> str:
    if subprocess.run(["sd", "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("live VIP workflow requires the optional sd executable")
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["sd", "init", "--json"], cwd=project, capture_output=True, check=True)
    created = subprocess.run(
        [
            "sd",
            "create",
            "--title",
            "Seed 17fa VIP workflow",
            "--type",
            "task",
            "--priority",
            "1",
            "--labels",
            "verify",
            "--json",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    issue_id = json.loads(created.stdout)["id"]
    (project / "pyproject.toml").write_text(
        """[project]
name = "vip-live-project"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = ["pytest"]

[tool.pytest.ini_options]
pythonpath = ["src"]

[tool.ruff]
target-version = "py312"
""",
        encoding="utf-8",
    )
    source = project / "src" / "vip.py"
    source.parent.mkdir()
    source.write_text(
        "def greet(name: str) -> str:\n    return f'Hello, {name}!'\n", encoding="utf-8"
    )
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_vip.py").write_text(
        "from vip import greet\n\n\ndef test_greet_normalizes_name():\n"
        "    assert greet('  VIP  ') == 'Hello, VIP!'\n",
        encoding="utf-8",
    )
    skill = project / ".kilo" / "skills" / "vip-workflow" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: vip-workflow\ndescription: Run seed 17fa VIP acceptance workflows\n---\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=vip@example.invalid",
            "-c",
            "user.name=VIP Test",
            "commit",
            "-q",
            "-m",
            "baseline",
        ],
        cwd=project,
        check=True,
    )
    return issue_id


def test_seed_17fa_black_box_vip_workflow(tmp_path: Path) -> None:
    project = tmp_path / "vip-project"
    issue_id = make_project(project)

    health = assert_ok(run_cli(project, "doctor", "--fast"))
    assert "HEALTH" in health and "fail=0" in health
    context = assert_ok(run_cli(project, "context"))
    assert f"project       {project}" in context
    routed = assert_ok(run_cli(project, "skills", "--route", "seed 17fa VIP workflow", "--json"))
    assert '"name": "vip-workflow"' in routed
    planned = assert_ok(run_cli(project, "plan", "normalize the VIP greeting"))
    assert "REQUEST  normalize the VIP greeting" in planned

    marker = project / "attempts.txt"
    failing = project / "fail_attempt.py"
    failing.write_text(
        "from pathlib import Path\n"
        "p = Path('attempts.txt')\n"
        "p.write_text(p.read_text() + 'ran\\n' if p.exists() else 'ran\\n')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    forbidden = project / "forbidden_fourth.py"
    forbidden.write_text(
        "from pathlib import Path\n"
        "p = Path('attempts.txt')\n"
        "p.write_text(p.read_text() + 'FOURTH_RAN\\n')\n",
        encoding="utf-8",
    )
    assert_ok(run_cli(project, "gate", "open", "research", "attempt ceiling probe"))
    for expected_attempt in range(1, 4):
        failed = run_cli(
            project,
            "gate",
            "record",
            "researched",
            "--cmd",
            quoted_python(failing),
        )
        assert failed.returncode == 1
        assert f"attempt={expected_attempt}" in failed.stdout
    before_fourth = marker.read_bytes()
    fourth = run_cli(
        project,
        "gate",
        "record",
        "researched",
        "--cmd",
        quoted_python(forbidden),
    )
    assert fourth.returncode == 1
    assert "THREE_STRIKES" in fourth.stdout
    assert marker.read_bytes() == before_fourth

    plan = project / "vip-plan.md"
    approved_plan = (
        "# VIP plan\n\n1. Normalize the supplied name.\n2. Run the existing test and lint.\n"
    )
    plan.write_text(approved_plan, encoding="utf-8")
    opened = assert_ok(
        run_cli(
            project,
            "gate",
            "open",
            "code-change",
            "normalize the VIP greeting",
            "--scope",
            "src/vip.py",
            "--scope",
            "seeds/issues.jsonl",
            "--plan",
            str(plan),
            "--issue",
            issue_id,
        )
    )
    run_id = opened.split()[1]
    assert f"issue: {issue_id}" in opened
    assert_ok(
        run_cli(
            project,
            "gate",
            "skill",
            "vip-workflow",
            "--state",
            "used",
            "--reason",
            "routed acceptance workflow",
        )
    )
    assert_ok(
        run_cli(
            project,
            "gate",
            "checkpoint",
            "--phase",
            "planning",
            "--summary",
            "implementation plan ready",
            "--next",
            "wait for approval",
            "--pending",
            "Approve implementation?",
            "--option",
            "approve",
            "--option",
            "hold",
        )
    )
    resumed = assert_ok(run_cli(project, "resume"))
    assert "PENDING  Approve implementation?" in resumed
    assert f"issue: {issue_id} (linked)" in resumed
    assert_ok(run_cli(project, "gate", "decide", "approve", "--by", "vip-reviewer"))
    approval = assert_ok(run_cli(project, "gate", "plan", "approve", "--by", "vip-reviewer"))
    assert "PLAN_APPROVED" in approval and "sha256:" in approval

    plan.write_text(approved_plan + "3. Unapproved extra step.\n", encoding="utf-8")
    refused = run_cli(
        project,
        "gate",
        "record",
        "tested",
        "--cmd",
        quoted_python(project / "tests" / "test_vip.py"),
    )
    assert refused.returncode == 1
    assert "PLAN_INVALID: approved plan hash no longer matches" in refused.stdout
    plan.write_text(approved_plan, encoding="utf-8")
    assert_ok(run_cli(project, "gate", "plan", "approve", "--by", "vip-reviewer"))

    (project / "src" / "vip.py").write_text(
        "def greet(name: str) -> str:\n    return f'Hello, {name.strip()}!'\n",
        encoding="utf-8",
    )
    tested = assert_ok(
        run_cli(
            project,
            "gate",
            "record",
            "tested",
            "--cmd",
            subprocess.list2cmdline([sys.executable, "-m", "pytest", "-q"]),
        )
    )
    assert "PASS  tested" in tested and "1 passed" in tested
    linted = assert_ok(
        run_cli(
            project,
            "gate",
            "record",
            "linted",
            "--cmd",
            subprocess.list2cmdline([sys.executable, "-m", "ruff", "check", "src", "tests"]),
        )
    )
    assert "PASS  linted" in linted and "All checks passed" in linted
    checked = assert_ok(run_cli(project, "gate", "check", "--diff-base", "HEAD"))
    assert "TESTS_NOT_WEAKENED  ok" in checked
    assert "SCOPE_RESPECTED  ok" in checked

    # A COMPLETE terminal state requires the linked Seed to actually be
    # closed, not merely that gate evidence is satisfied.
    premature_close = run_cli(project, "gate", "close")
    assert premature_close.returncode == 1
    assert "SEED_NOT_CLOSED" in premature_close.stdout
    assert "work-close" in premature_close.stdout

    work_closed = assert_ok(run_cli(project, "work-close", "--run", run_id))
    assert f"CLOSED  {issue_id}" in work_closed
    shown = subprocess.run(
        ["sd", "show", issue_id, "--json"],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    closed_payload = json.loads(shown.stdout)
    if closed_payload.get("success"):
        assert closed_payload["issue"]["status"] == "closed"
    else:
        assert "not found" in (closed_payload.get("error") or "").lower()
    assert "tested=ok/executed" in work_closed

    closed = assert_ok(run_cli(project, "gate", "close"))
    assert "COMPLETE  5 gate(s) satisfied" in closed

    stale = assert_ok(run_cli(project, "resume"))
    assert f"RUN {run_id}  status=stale" in stale
