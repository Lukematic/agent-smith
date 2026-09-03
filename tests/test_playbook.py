"""Playbook: written orders that fire on session events, so the human runs one
word instead of remembering forty commands.

The playbook is data (steps per event), the steps are pure functions returning
printed lines, and the runner is deterministic - so 'what happens at session
start' is a file you can read, not a persona's recollection."""

from __future__ import annotations

import json
from pathlib import Path

from smith.enforce import Gate, Ledger, TaskClass
from smith.playbook import (
    DEFAULT_PLAYBOOK,
    grill_questions,
    load_playbook,
    run_event,
    walkthrough,
)


def _state(tmp_path: Path) -> Path:
    s = tmp_path / "state"
    s.mkdir()
    return s


class TestPlaybookData:
    def test_default_has_the_three_events(self) -> None:
        assert set(DEFAULT_PLAYBOOK) == {"session-start", "task-close", "session-end"}

    def test_project_override_replaces_an_event_list(self, tmp_path: Path) -> None:
        s = _state(tmp_path)
        (s / "playbook.json").write_text(json.dumps({"session-start": ["start"]}), encoding="utf-8")
        book = load_playbook(s)
        assert book["session-start"] == ["start"]
        assert book["task-close"] == DEFAULT_PLAYBOOK["task-close"]

    def test_unknown_step_is_refused_at_load(self, tmp_path: Path) -> None:
        s = _state(tmp_path)
        (s / "playbook.json").write_text(json.dumps({"session-end": ["nope"]}), encoding="utf-8")
        import pytest

        with pytest.raises(ValueError, match="unknown step"):
            load_playbook(s)


class TestWalkthrough:
    def test_walkthrough_of_a_closed_run_names_objective_gates_and_files(
        self, tmp_path: Path
    ) -> None:
        s = _state(tmp_path)
        ledger = Ledger(s)
        run = ledger.open(TaskClass.QUESTION, "prove the closer verifies", file_scope=["a.py"])
        ledger.record(run.run_id, Gate.TESTED, 'python -c "raise SystemExit(0)"')
        text = walkthrough(ledger, run.run_id)
        assert "prove the closer verifies" in text
        assert "tested" in text
        assert "a.py" in text
        assert "## Grill me" in text

    def test_grill_questions_are_three_and_reference_the_run(self, tmp_path: Path) -> None:
        s = _state(tmp_path)
        ledger = Ledger(s)
        run = ledger.open(TaskClass.BUGFIX, "fix redaction", file_scope=["session_log.py"])
        qs = grill_questions(ledger, run.run_id)
        assert len(qs) == 3
        assert any("session_log.py" in q for q in qs)


class TestRunEvent:
    def test_session_start_lines_include_each_step_header(self, tmp_path: Path) -> None:
        s = _state(tmp_path)
        lines = run_event("session-start", s, tmp_path, ledger=Ledger(s), open_seeds=[])
        joined = "\n".join(lines)
        for step in DEFAULT_PLAYBOOK["session-start"]:
            assert f"[{step}]" in joined

    def test_task_close_with_no_closed_run_says_so_instead_of_crashing(
        self, tmp_path: Path
    ) -> None:
        s = _state(tmp_path)
        lines = run_event("task-close", s, tmp_path, ledger=Ledger(s), open_seeds=[])
        assert any("no closed run" in ln for ln in lines)

    def test_mission_refresh_regenerates_the_document(self, tmp_path: Path) -> None:
        s = _state(tmp_path)
        run_event("session-end", s, tmp_path, ledger=Ledger(s), open_seeds=["x"])
        assert (s / "MISSION.md").is_file()

    def test_unknown_event_is_refused(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(ValueError, match="unknown event"):
            run_event("lunch", _state(tmp_path), tmp_path, ledger=Ledger(tmp_path), open_seeds=[])


class TestStepsDeclareSkills:
    def test_every_default_step_maps_to_a_real_skill_or_direct(self) -> None:
        from smith.playbook import STEP_SKILLS
        from smith.skill_catalog import SkillCatalog

        names = {
            s.name
            for s in SkillCatalog(
                Path("/n"), Path("/n"), Path(__file__).resolve().parents[1] / "skills"
            ).skills
        }
        for steps in DEFAULT_PLAYBOOK.values():
            for step in steps:
                assert step in STEP_SKILLS, f"{step} has no skill declaration"
                assert STEP_SKILLS[step] == "direct" or STEP_SKILLS[step] in names

    def test_run_event_prints_the_skill_next_to_each_step(self, tmp_path: Path) -> None:
        s = _state(tmp_path)
        lines = run_event("session-start", s, tmp_path, ledger=Ledger(s), open_seeds=[])
        assert any("[mission-gap] skill=awino-discover" in ln for ln in lines)


class TestElevator:
    def test_elevator_routes_recalls_and_names_the_next_command(self, tmp_path: Path) -> None:
        from smith.playbook import elevator
        from smith.skill_catalog import SkillCatalog

        s = _state(tmp_path)
        (s / "memory").mkdir()
        (s / "memory" / "lessons.md").write_text(
            "- [2026-01-01] `FLOOR_VERIFY_CWD` - verify commands relative to project root.\n",
            encoding="utf-8",
        )
        catalog = SkillCatalog(
            Path("/n"), Path("/n"), Path(__file__).resolve().parents[1] / "skills"
        )
        lines = elevator(
            "pytest is failing with a ValueError when the verify command runs",
            s,
            tmp_path,
            ledger=Ledger(s),
            catalog=catalog,
        )
        joined = "\n".join(lines)
        assert "FLOOR  awino-debug" in joined
        assert "RECALL" in joined and "FLOOR_VERIFY_CWD" in joined
        assert "NEXT  awino gate open bugfix" in joined
        assert "awino floor open" in joined

    def test_elevator_on_a_vague_ask_asks_one_question_and_gives_no_command(
        self, tmp_path: Path
    ) -> None:
        from smith.playbook import elevator
        from smith.skill_catalog import SkillCatalog

        s = _state(tmp_path)
        catalog = SkillCatalog(
            Path("/n"), Path("/n"), Path(__file__).resolve().parents[1] / "skills"
        )
        lines = elevator("xyzzy plugh wibble", s, tmp_path, ledger=Ledger(s), catalog=catalog)
        joined = "\n".join(lines)
        assert joined.count("QUESTION") == 1
        assert "NEXT  awino gate open" not in joined

    def test_elevator_announces_a_stance_switch(self, tmp_path: Path) -> None:
        from smith.playbook import elevator
        from smith.skill_catalog import SkillCatalog

        s = _state(tmp_path)
        catalog = SkillCatalog(
            Path("/n"), Path("/n"), Path(__file__).resolve().parents[1] / "skills"
        )
        lines = elevator(
            "I think we should rewrite the ledger in rust",
            s,
            tmp_path,
            ledger=Ledger(s),
            catalog=catalog,
        )
        assert any("STANCE  -> steel-man" in ln for ln in lines)
