"""Mission-first onboarding must persist only confirmed intent.

These tests cover the sparse-repository failure that motivated ``smith onboard``:
mission discovery found a sentence, but goals, tenets, primary user, expectations,
and success criteria were never collected, so planning rushed toward architecture.
"""

from __future__ import annotations

from pathlib import Path

from smith import onboarding
from smith.mission import Confidence, Kind, Mission


def stated_mission() -> Mission:
    return Mission(
        project="science-lite",
        statement="Build a source-grounded literature assistant.",
        confidence=Confidence.STATED,
        kind=Kind.APPLICATION,
        non_goals=["No autonomous publication"],
    )


class TestDraft:
    def test_repository_mission_seeds_a_draft_not_confirmation(self) -> None:
        intent = onboarding.seed_from_mission(stated_mission())
        assert intent.mission
        assert intent.source.startswith("drafted from")
        assert not intent.confirmed_at
        assert not intent.complete

    def test_non_goals_are_preserved(self) -> None:
        intent = onboarding.seed_from_mission(stated_mission())
        assert intent.non_goals == ["No autonomous publication"]

    def test_first_frontier_question_after_stated_mission_is_primary_user(self) -> None:
        intent = onboarding.seed_from_mission(stated_mission())
        questions = onboarding.frontier(intent)
        assert questions[0].key == "primary_user"

    def test_unknown_mission_asks_mission_first(self) -> None:
        intent = onboarding.ProjectIntent(mission="")
        assert onboarding.frontier(intent)[0].key == "mission"


class TestAnswers:
    def test_scalar_answer_is_stripped(self) -> None:
        intent = onboarding.ProjectIntent(mission="x")
        onboarding.apply(intent, "primary_user", "  working scientists  ")
        assert intent.primary_user == "working scientists"

    def test_list_answer_uses_semicolon_delimiter(self) -> None:
        intent = onboarding.ProjectIntent(mission="x")
        onboarding.apply(intent, "goals", "search open indexes; inspect evidence; cited synthesis")
        assert intent.goals == ["search open indexes", "inspect evidence", "cited synthesis"]

    def test_unknown_field_is_rejected(self) -> None:
        intent = onboarding.ProjectIntent(mission="x")
        try:
            onboarding.apply(intent, "framework", "LangGraph")
        except ValueError as exc:
            assert "unknown onboarding field" in str(exc)
        else:
            raise AssertionError("unknown field accepted")

    def test_remember_tenet_is_deduplicated(self) -> None:
        intent = onboarding.ProjectIntent(mission="x")
        onboarding.remember(intent, "tenet", "No issue, no branch")
        onboarding.remember(intent, "tenet", "No issue, no branch")
        assert intent.tenets == ["No issue, no branch"]


class TestConfirmation:
    def complete_intent(self) -> onboarding.ProjectIntent:
        return onboarding.ProjectIntent(
            mission="Build a source-grounded literature assistant.",
            primary_user="working scientists",
            goals=["reviewable evidence table"],
            tenets=["no claim without source IDs"],
            expectations=["free hosting"],
            non_goals=["no autonomous publication"],
            success_metric="a scientist can trace every claim",
            source="confirmed",
        )

    def test_complete_requires_mission_user_goal_tenet_and_metric(self) -> None:
        intent = self.complete_intent()
        assert intent.complete
        assert intent.missing == []

    def test_missing_success_metric_prevents_completion(self) -> None:
        intent = self.complete_intent()
        intent.success_metric = ""
        assert not intent.complete
        assert "success_metric" in intent.missing

    def test_round_trip_preserves_confirmed_intent(self, tmp_path: Path) -> None:
        intent = self.complete_intent()
        path = onboarding.save(tmp_path, intent)
        loaded = onboarding.load(tmp_path)
        assert path == tmp_path / ".smith" / "project.yaml"
        assert loaded is not None
        assert loaded.mission == intent.mission
        assert loaded.goals == intent.goals
        assert loaded.workflow.one_task_per_session
        assert loaded.workflow.planning_interview == "adaptive-grill"
        assert loaded.confirmed_at

    def test_partial_answers_persist_without_false_confirmation(self, tmp_path: Path) -> None:
        intent = onboarding.seed_from_mission(stated_mission())
        onboarding.apply(intent, "primary_user", "working scientists")
        onboarding.save(tmp_path, intent)
        loaded = onboarding.load(tmp_path)
        assert loaded is not None
        assert loaded.primary_user == "working scientists"
        assert not loaded.confirmed_at
        assert loaded.source.startswith("drafted from")

    def test_confirmed_intent_needs_no_confirmation(self, tmp_path: Path) -> None:
        intent = self.complete_intent()
        onboarding.save(tmp_path, intent)
        loaded = onboarding.load(tmp_path)
        assert loaded is not None
        assert not onboarding.confirmation_required(stated_mission(), loaded)

    def test_derived_mission_requires_confirmation(self) -> None:
        found = stated_mission()
        found.confidence = Confidence.DERIVED
        assert onboarding.confirmation_required(found, None)

    def test_json_report_names_remaining_frontier(self) -> None:
        intent = onboarding.ProjectIntent(mission="x")
        payload = onboarding.as_json(intent, onboarding.frontier(intent))
        assert '"primary_user"' in payload
        assert '"complete": false' in payload
