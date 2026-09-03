"""Heilmeier catechism as a living mission document: prefilled from what the
project already knows, gaps asked one at a time, exams wired to real verify
commands, and derived insights that surface what the answers imply."""

from __future__ import annotations

from pathlib import Path

from smith.heilmeier import (
    QUESTIONS,
    Catechism,
    insights,
    load,
    render,
    save,
)


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj" / ".smith"
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
