"""Heilmeier catechism as a living mission document: prefilled from what the
project already knows, gaps asked one at a time, exams wired to real verify
commands, and derived insights that surface what the answers imply."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from awino.heilmeier import (
    QUESTIONS,
    Catechism,
    command_is_executable,
    insights,
    load,
    missing_mission_fields,
    render,
    save,
    validate_mission,
)


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj" / ".awino"
    p.mkdir(parents=True)
    return p


class TestShape:
    def test_eight_questions_each_with_a_stance(self) -> None:
        assert len(QUESTIONS) == 8
        assert {q.stance for q in QUESTIONS} <= {"first-principles", "steel-man", "advisor"}
        assert QUESTIONS[4].key == "risks" and QUESTIONS[4].stance == "steel-man"
        assert QUESTIONS[7].key == "exams"

    def test_missing_file_loads_empty(self, tmp_path: Path) -> None:
        cat = load(_project(tmp_path))
        assert cat.answers == {}


class TestPrefill:
    def test_mission_from_project_yaml_prefills_objective(self, tmp_path: Path) -> None:
        p = _project(tmp_path)
        (p / "project.yaml").write_text("mission: ship a trustworthy agent\n", encoding="utf-8")
        cat = load(p)
        assert cat.answers["objective"].startswith("ship a trustworthy agent")
        assert cat.source["objective"] == "project.yaml"

    def test_explicit_answers_win_over_prefill(self, tmp_path: Path) -> None:
        p = _project(tmp_path)
        (p / "project.yaml").write_text("mission: derived\n", encoding="utf-8")
        save(p, Catechism(answers={"objective": "human wrote this"}, source={"objective": "human"}))
        assert load(p).answers["objective"] == "human wrote this"


class TestGapWalk:
    def test_next_gap_is_the_first_unanswered_in_order(self, tmp_path: Path) -> None:
        cat = Catechism(answers={"objective": "x", "today": "y"}, source={})
        assert cat.next_gap().key == "new_approach"

    def test_fully_answered_has_no_gap(self) -> None:
        cat = Catechism(answers={q.key: "a" for q in QUESTIONS}, source={})
        assert cat.next_gap() is None


class TestExams:
    def test_exam_lines_with_arrow_become_verify_commands(self) -> None:
        cat = Catechism(
            answers={
                "exams": "tests green -> uv run pytest -q\nusers adopt it\nlint clean -> ruff check src"
            },
            source={},
        )
        assert cat.exam_commands() == ["uv run pytest -q", "ruff check src"]
        assert cat.exams_without_commands() == ["users adopt it"]


class TestInsights:
    def test_jargon_in_objective_is_flagged(self) -> None:
        cat = Catechism(answers={"objective": "an LLM agent orchestration harness"}, source={})
        assert any("jargon" in i.lower() for i in insights(cat, open_seeds=[]))

    def test_risk_without_exam_is_flagged_and_becomes_a_research_prompt(self) -> None:
        cat = Catechism(
            answers={
                "risks": "workers ignore the skill\nlogin dependency",
                "exams": "tests green -> pytest",
            },
            source={},
        )
        out = insights(cat, open_seeds=[])
        assert any("no exam" in i.lower() and "workers ignore" in i.lower() for i in out)
        assert any(i.startswith("RESEARCH") for i in out)

    def test_open_seeds_not_tied_to_an_exam_are_counted(self) -> None:
        cat = Catechism(answers={"exams": "tests green -> pytest"}, source={})
        out = insights(cat, open_seeds=["privacy denylist", "per-project isolation"])
        assert any("2 open seed" in i.lower() for i in out)

    def test_cost_without_midterm_exam_is_flagged(self) -> None:
        cat = Catechism(answers={"cost": "3 weeks", "exams": "final: ships -> pytest"}, source={})
        assert any("mid-term" in i.lower() for i in insights(cat, open_seeds=[]))


class TestLivingDocument:
    def test_render_writes_markdown_with_all_eight_and_insights(self, tmp_path: Path) -> None:
        p = _project(tmp_path)
        cat = Catechism(
            answers={"objective": "make agents trustworthy"}, source={"objective": "human"}
        )
        path = render(p, cat, open_seeds=["x"])
        text = path.read_text(encoding="utf-8")
        assert text.count("## ") >= 9  # 8 questions + insights
        assert "make agents trustworthy" in text
        assert "(unanswered)" in text

    def test_round_trip(self, tmp_path: Path) -> None:
        p = _project(tmp_path)
        save(p, Catechism(answers={"risks": "r1\nr2"}, source={"risks": "human"}))
        assert load(p).answers["risks"] == "r1\nr2"


class TestProseCommandsRejected:
    """The Heilmeier prose bug: English descriptions after '->' were once
    called gate-ready. A wired exam must be an executable command."""

    PROSE_EXAMS = (
        "deployment works -> the app deploys cleanly\n"
        "tests pass -> all tests are green\n"
        "users adopt it -> people actually use the thing"
    )

    def _cat(self, exams: str) -> Catechism:
        return Catechism(
            answers={"objective": "make agents trustworthy", "exams": exams},
            source={},
        )

    def test_prose_after_arrow_is_not_a_valid_command(self) -> None:
        cat = self._cat(self.PROSE_EXAMS)
        assert cat.exam_commands() != []  # raw parse still sees the wiring
        assert cat.exam_commands_valid() == []
        assert len(cat.exam_command_problems()) == 3

    def test_prose_commands_do_not_satisfy_success_criteria(self) -> None:
        cat = self._cat(self.PROSE_EXAMS)
        assert "success_criteria" in missing_mission_fields(cat)
        problems = validate_mission(cat)
        assert any("prose" in p and "success_criteria" in p for p in problems)

    def test_prose_commands_are_not_rendered_gate_ready(self, tmp_path: Path) -> None:
        p = _project(tmp_path)
        path = render(p, self._cat(self.PROSE_EXAMS), open_seeds=[])
        text = path.read_text(encoding="utf-8")
        # the user's answers are still recorded verbatim, but nothing is
        # labeled gate-ready and no prose is listed as a verify command
        assert "## Exam commands (gate-ready)" not in text
        assert "`the app deploys cleanly`" not in text

    def test_insights_flag_prose_commands(self) -> None:
        cat = self._cat(self.PROSE_EXAMS)
        out = insights(cat, open_seeds=[])
        assert any("prose instead of a command" in i for i in out)

    def test_nonexistent_binary_is_rejected(self) -> None:
        assert not command_is_executable("frobnicate --fast")

    def test_unbalanced_quote_is_rejected(self) -> None:
        assert not command_is_executable('echo "oops')

    def test_empty_command_is_rejected(self) -> None:
        assert not command_is_executable("   ")

    def test_real_executable_is_accepted_and_runs(self) -> None:
        cmd = f"{shlex.quote(sys.executable)} -c \"print('ok')\""
        assert command_is_executable(cmd)
        cat = self._cat(f"report builds -> {cmd}")
        assert cat.exam_commands_valid() == [cmd]
        assert "success_criteria" not in missing_mission_fields(cat)
        assert validate_mission(cat) == []
        # the wired command actually executes: gate-ready means runnable
        proc = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == "ok"

    def test_absolute_executable_path_is_accepted(self) -> None:
        assert command_is_executable(sys.executable)

    def test_mixed_prose_and_real_commands(self) -> None:
        cmd = f"{shlex.quote(sys.executable)} -c \"print('ok')\""
        cat = self._cat(f"tests pass -> all tests are green\nreport builds -> {cmd}")
        assert cat.exam_commands_valid() == [cmd]
        assert len(cat.exam_command_problems()) == 1
        assert "success_criteria" not in missing_mission_fields(cat)
