"""Loop entry points must persist approval and execution facts in PlanController."""

from __future__ import annotations

from pathlib import Path

import pytest

from awino import controller, loops


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".awino").mkdir()
    (root / "src" / "awino").mkdir(parents=True)
    (root / "tests").mkdir()
    for relative in ("src/awino/loops.py", "tests/test_loops.py"):
        (root / relative).write_text("# fixture\n", encoding="utf-8")
    return root


def _skill_path() -> Path:
    return Path(__file__).resolve().parents[1] / "skills" / "awino-rpi" / "SKILL.md"


def test_rpi_loop_creation_and_approval_bind_a_durable_controller(
    project: Path, tmp_path: Path
) -> None:
    """A fresh driver process sees plan approval made by the original driver."""
    driver = loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=_skill_path(),
        open_rpi_run=lambda: "run-123",
        state_root=project / ".awino",
    )
    state = driver.new("connect loop controller")
    bound = controller.for_loop(project / ".awino", state.id)
    assert bound.controller.state.approval_state == "pending"
    # The existing RPI approval prerequisites are separately covered by
    # test_loops; set its loop record to the plan phase with an approval and
    # exercise only the durable-controller bridge here.
    approval_id = controller.request_approval(bound.controller, "rpi-plan-approved")
    controller.grant_approval(bound.controller, approval_id, by="human", plan_level=True)
    fresh = controller.for_loop(project / ".awino", state.id)
    assert fresh.controller.state.approval_state == "approved"


def test_rpi_handoff_marks_the_controller_action_applied_without_closing(
    project: Path, tmp_path: Path
) -> None:
    driver = loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=_skill_path(),
        open_rpi_run=lambda: "run-123",
        state_root=project / ".awino",
    )
    state = driver.new("connect loop controller")
    bound = controller.for_loop(project / ".awino", state.id)
    approval_id = controller.request_approval(bound.controller, "rpi-plan-approved")
    controller.grant_approval(bound.controller, approval_id, by="human", plan_level=True)
    controller.queue_action(bound.controller, "rpi-implement")
    driver._complete(state)
    fresh = controller.for_loop(project / ".awino", state.id)
    assert fresh.controller.state.pending_action_ids == []
    assert fresh.controller.state.status != "closed"
