"""Real machine actions must use the durable controller, not an unused adapter."""

from __future__ import annotations

from pathlib import Path

from awino import controller, machine, stepper
from awino.enforce import Ledger
from awino.machine import Node
from awino.paths import AwinoPaths
from awino.skill_catalog import SkillCatalog


def _context(tmp_path: Path) -> stepper.StepContext:
    project = tmp_path / "project"
    project.mkdir()
    state = project / ".awino"
    state.mkdir()
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "plugin.json").write_text("{}", encoding="utf-8")
    (home / "memory").mkdir()
    (home / "memory" / "lessons.md").write_text("", encoding="utf-8")
    return stepper.StepContext(
        state_root=state,
        project=project,
        home=home,
        paths=AwinoPaths(root=home),
        ledger=Ledger(state),
        catalog=SkillCatalog(
            tmp_path / "project-skills", tmp_path / "global-skills", Path("skills")
        ),
        scope=["src/example.py"],
        verify='python -c "raise SystemExit(0)"',
        confirmed_budget=True,
    )


def test_machine_open_persists_controller_approval_across_fresh_process(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    active = machine.Machine(
        node=Node.OPEN, request="change one file", loop="floor", skill="awino-debug"
    )
    assert stepper._open(active, ctx) == "opened"
    machine.save(ctx.state_root, active)

    restored = machine.load(ctx.state_root)
    adapter = controller.for_machine(ctx.state_root, restored.run_id or "")
    assert restored.controller_plan_id == adapter.controller.plan_id
    assert adapter.controller.state.approval_state == "approved"


def test_machine_execute_records_action_in_the_persisted_controller(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    active = machine.Machine(
        node=Node.OPEN, request="change one file", loop="floor", skill="awino-debug"
    )
    assert stepper._open(active, ctx) == "opened"
    active.floor = 1
    # Work dispatch itself is outside this integration contract; emulate its
    # already-opened action to test the new durable execute handoff.
    adapter = controller.for_machine(ctx.state_root, active.run_id or "")
    controller.queue_action(adapter.controller, "work-1")
    active.controller_action_id = "work-1"
    ctx.answer = "done"
    assert stepper._execute(active, ctx) == "executed"
    fresh = controller.for_machine(ctx.state_root, active.run_id or "")
    assert fresh.controller.state.pending_action_ids == []
