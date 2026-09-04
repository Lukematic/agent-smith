"""The node actions: what each tick actually does, driven directly with a real
ledger and a fixture project - no CLI, no subprocess worker.

Covers the human-facing payoff that the routing-spine proof missed: stance
announced, mission referenced, CLOSE really closes and fires the task-close
order (walkthrough, grill, mission refresh), and the machine goes back and
forth - QUESTION answered re-enters ROUTE, STOP "continue" re-enters WORK."""

from __future__ import annotations

from pathlib import Path

import pytest

from smith import heilmeier, machine, stepper
from smith.enforce import Ledger, TaskClass
from smith.machine import Machine, Node
from smith.paths import SmithPaths
from smith.skill_catalog import SkillCatalog

SMITH_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> stepper.StepContext:
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='p'\nversion='0.1'\n[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    state = project / ".smith"
    (state / "run").mkdir(parents=True)
    (state / "memory").mkdir()
    (project / ".venv").mkdir()
    home = tmp_path / "home"
    (home / "plugin.json").parent.mkdir(parents=True, exist_ok=True)
    (home / "plugin.json").write_text("{}", encoding="utf-8")
    (home / "knowledge").mkdir()
    (home / "memory").mkdir()
    (home / "memory" / "lessons.md").write_text(
        "- [2026-01-01] a lesson about loaders\n", encoding="utf-8"
    )
    # health is machine-specific; the stepper only needs "no blocking failures"
    monkeypatch.setattr(stepper.health, "run_all", lambda _p, fast=True: [])
    return stepper.StepContext(
        state_root=state,
        project=project,
        home=home,
        paths=SmithPaths(root=home),
        ledger=Ledger(state),
        catalog=SkillCatalog(Path("/n"), Path("/n"), SMITH_ROOT / "skills"),
        scope=["tests/test_a.py"],
        verify="pytest -q",
    )


class TestLocateSurfacesStanceAndMission:
    def test_a_stated_position_announces_steel_man(self, ctx: stepper.StepContext) -> None:
        m = Machine(node=Node.LOCATE, request="I think we should rewrite the loader in rust")
        stepper._locate(m, ctx)
        assert m.stance == "steel-man"
        assert any(ln.startswith("STANCE  -> steel-man") for ln in ctx.lines)

    def test_plain_words_keep_the_default_stance(self, ctx: stepper.StepContext) -> None:
        m = Machine(node=Node.LOCATE, request="pytest is failing in the loader")
        stepper._locate(m, ctx)
        assert m.stance == "advisor"
        assert not any("STANCE  ->" in ln for ln in ctx.lines)

    def test_mission_is_read_and_reported(self, ctx: stepper.StepContext) -> None:
        heilmeier.save(ctx.state_root, heilmeier.Catechism({"objective": "trustworthy agents"}, {}))
        m = Machine(node=Node.LOCATE, request="pytest is failing")
        stepper._locate(m, ctx)
        assert any("MISSION  trustworthy agents" in ln for ln in ctx.lines)

    def test_missing_mission_is_named_not_ignored(self, ctx: stepper.StepContext) -> None:
        m = Machine(node=Node.LOCATE, request="pytest is failing")
        stepper._locate(m, ctx)
        assert any("MISSION  unanswered" in ln for ln in ctx.lines)


class TestLadderReferencesMission:
    def test_work_no_exam_mentions_is_flagged(self, ctx: stepper.StepContext) -> None:
        heilmeier.save(
            ctx.state_root,
            heilmeier.Catechism({"exams": "provenance resolves -> make check-provenance"}, {}),
        )
        m = Machine(
            node=Node.LADDER, request="pytest is failing in the loader", skill="awino-debug"
        )
        stepper._ladder(m, ctx)
        assert any("no exam mentions this work" in ln for ln in ctx.lines)

    def test_work_an_exam_mentions_is_not_flagged(self, ctx: stepper.StepContext) -> None:
        heilmeier.save(
            ctx.state_root, heilmeier.Catechism({"exams": "loader tests green -> pytest -q"}, {})
        )
        m = Machine(
            node=Node.LADDER, request="pytest is failing in the loader", skill="awino-debug"
        )
        stepper._ladder(m, ctx)
        assert not any("no exam mentions" in ln for ln in ctx.lines)


class TestCloseActuallyCloses:
    def _satisfied_run(self, ctx: stepper.StepContext) -> str:
        run = ctx.ledger.open(TaskClass.QUESTION, "prove close fires", loop="floor")
        return run.run_id

    def test_close_marks_complete_and_fires_walkthrough_and_grill(
        self, ctx: stepper.StepContext
    ) -> None:
        run_id = self._satisfied_run(ctx)
        m = Machine(node=Node.CLOSE, run_id=run_id, loop="floor")
        obs = stepper._close(m, ctx)
        assert obs == "closed"
        assert ctx.ledger.load(run_id).terminal_state == "complete"
        joined = "\n".join(ctx.lines)
        assert "[walkthrough]" in joined
        assert "[grill-offer]" in joined
        assert "Grill me" in joined
        assert "[mission-refresh]" in joined
        assert (ctx.state_root / "WALKTHROUGH.md").is_file()
        assert (ctx.state_root / "MISSION.md").is_file()

    def test_close_refuses_when_a_gate_is_missing_and_waits(self, ctx: stepper.StepContext) -> None:
        run = ctx.ledger.open(TaskClass.BUGFIX, "needs gates", loop="floor")
        m = Machine(node=Node.CLOSE, run_id=run.run_id, loop="floor")
        assert stepper._close(m, ctx) == "waiting"
        assert ctx.ledger.load(run.run_id).terminal_state != "complete"
        assert any("CLOSE  refused" in ln for ln in ctx.lines)


class TestBackAndForth:
    def test_question_answered_reenters_route(self, ctx: stepper.StepContext) -> None:
        m, _ = stepper.step(ctx, "xyzzy plugh wibble")  # LOCATE
        m, _ = stepper.step(ctx)  # ROUTE -> none -> QUESTION
        assert m.node is Node.QUESTION
        ctx.answer = "pytest is failing with a ValueError in the loader"
        m, _ = stepper.step(ctx)  # QUESTION answered -> ROUTE
        assert m.node is Node.ROUTE
        m, _ = stepper.step(ctx)  # ROUTE -> high -> LADDER
        assert m.node is Node.LADDER and m.skill == "awino-debug"

    def test_stop_continue_reenters_work_and_counts_the_floor(
        self, ctx: stepper.StepContext
    ) -> None:
        run = ctx.ledger.open(TaskClass.BUGFIX, "stuck", loop="ralph")
        machine.save(
            ctx.state_root, Machine(node=Node.STOP, run_id=run.run_id, loop="ralph", floor=2)
        )
        ctx.answer = "continue"
        m, _ = stepper.step(ctx)
        assert m.node is Node.WORK and m.floor == 3

    def test_stop_drop_ends(self, ctx: stepper.StepContext) -> None:
        machine.save(ctx.state_root, Machine(node=Node.STOP, loop="ralph", floor=3))
        ctx.answer = "drop"
        m, _ = stepper.step(ctx)
        assert m.node is Node.DONE

    def test_stop_without_an_answer_waits(self, ctx: stepper.StepContext) -> None:
        machine.save(ctx.state_root, Machine(node=Node.STOP, loop="ralph", floor=3))
        m, lines = stepper.step(ctx)
        assert m.node is Node.STOP
        assert any("human decision" in ln for ln in lines)
