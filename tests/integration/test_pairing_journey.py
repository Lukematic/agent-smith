"""Seven end-to-end CLI acceptance journeys for the A.W.I.N.O. 0.8.1 harness."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from awino import controller

SMITH_ROOT = Path(__file__).resolve().parents[2]


def _cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "awino.cli", *args],
        cwd=cwd,
        env={**dict(os.environ), "PYTHONPATH": str(SMITH_ROOT / "src"), "AWINO_PROJECT": str(cwd)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def _setup_project(tmp_path: Path) -> Path:
    project = tmp_path / "app"
    (project / ".git").mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='app'\nversion='0.1'\ndependencies=['pytest']\n[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (project / ".awino").mkdir(parents=True)
    return project


def test_journey_1_coding_bugfix(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    res = _cli(["best", "pytest is failing with a ValueError in loader.py"], project)
    assert res.returncode == 0
    assert "WAITING" in res.stdout or "NODE: BUDGET" in res.stdout or "BUDGET" in res.stdout


def test_journey_2_research_question(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    res = _cli(["best", "what is a harness and how should context be managed"], project)
    assert res.returncode == 0
    assert "awino-consult" in res.stdout or "BUDGET" in res.stdout


def test_journey_3_presentation_request(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    res = _cli(["best", "make a presentation opening script for the team"], project)
    assert res.returncode == 0
    assert "awino-visualize" in res.stdout or "BUDGET" in res.stdout


def test_journey_4_plan_revision_invalidates_approval(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    state = project / ".awino"
    adapter = controller.for_plan(state, "plan-rev-1")
    aid = adapter.request_approval("approve plan")
    controller.grant_approval(adapter.controller, aid, by="luke", plan_level=True)
    assert adapter.controller.state.approval_state == "approved"

    # Material change invalidates
    adapter.controller.set_scope(["src/new_file.py"])
    assert adapter.controller.state.approval_state == "invalidated"


def test_journey_5_failed_review_blocks_closure(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    state = project / ".awino"
    adapter = controller.for_plan(state, "plan-rev-2")
    aid = adapter.request_approval("approve plan")
    controller.grant_approval(adapter.controller, aid, by="luke", plan_level=True)
    adapter.record_review(verdict="blocked", detail="tests failed in reviewer floor", by="reviewer")

    with pytest.raises(controller.PlanNotClosable, match=r"latest review is 'blocked'"):
        adapter.close(by="luke")


def test_journey_6_exhausted_budget_refuses_execution(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    state = project / ".awino"
    adapter = controller.for_plan(state, "plan-budget-1", budgets={"work_iterations": 1})
    aid = adapter.request_approval("approve plan")
    controller.grant_approval(adapter.controller, aid, by="luke", plan_level=True)
    adapter.charge_budget("work_iterations", 1)

    problems = adapter.preflight()
    assert any("budget exhausted: work_iterations 1/1" in p for p in problems)


def test_journey_7_restart_retains_scope_and_state(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    state = project / ".awino"
    first = controller.for_machine(
        state, "run-restart-1", scope=["src/core.py"], verifier="pytest -q"
    )
    aid = first.request_approval("budget-confirmed")
    controller.grant_approval(first.controller, aid, by="human", plan_level=True)
    first.queue_action("work-1")

    # Load from a fresh process/instance
    second = controller.for_machine(state, "run-restart-1")
    snap = second.controller.status_snapshot()
    assert snap["scope"] == ["src/core.py"]
    assert snap["verifier"] == "pytest -q"
    assert "work-1" in snap["pending_actions"]
    assert snap["approval_state"] == "approved"
