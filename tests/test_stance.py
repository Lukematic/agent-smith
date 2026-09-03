"""Stances: how the controller talks to the human, switched by *their* words.

The spec's Phase 5 table row by row. detect() is deterministic - keyword and
phase rules, no model call - so a stance switch is testable, and never silent:
the caller must print the STANCE line whenever the result differs from the
current stance.
"""

from __future__ import annotations

from pathlib import Path

from smith.stance import STANCES, Stance, detect, load_default, save_default


class TestCatalogShape:
    def test_the_seven_stances_exist(self) -> None:
        names = {s.name for s in STANCES}
        assert names == {
            "advisor",
            "first-principles",
            "steel-man",
            "assumption-audit",
            "teach-back",
            "research-intake",
            "expert",
        }

    def test_every_stance_has_rules_and_a_trigger_description(self) -> None:
        for stance in STANCES:
            assert stance.rules.strip()
            assert stance.trigger_description.strip()
            assert len(stance.rules.splitlines()) <= 15


class TestDetection:
    def test_decomposition_language_triggers_first_principles(self) -> None:
        assert detect("let's break this down into fundamentals").name == "first-principles"

    def test_a_stated_position_triggers_steel_man(self) -> None:
        assert detect("I think we should rewrite the whole module in rust").name == "steel-man"

    def test_a_stated_conclusion_triggers_assumption_audit(self) -> None:
        assert detect("so that means the cache is the bottleneck").name == "assumption-audit"

    def test_teach_me_language_triggers_teach_back(self) -> None:
        assert detect("teach me how the ledger works").name == "teach-back"
        assert detect("I don't understand the gate model").name == "teach-back"

    def test_research_language_triggers_research_intake(self) -> None:
        assert detect("research what agent memory approaches exist").name == "research-intake"

    def test_human_experience_language_triggers_expert(self) -> None:
        assert detect("honestly, how would you handle this burnout").name == "expert"

    def test_plain_task_language_stays_default(self) -> None:
        assert detect("fix the failing test in ci") is None
        assert detect("run the suite") is None

    def test_detection_is_deterministic(self) -> None:
        text = "I think we should ship it"
        assert detect(text) == detect(text)


class TestPersistence:
    def test_default_round_trips_through_project_yaml(self, tmp_path: Path) -> None:
        save_default(tmp_path, "steel-man")
        assert load_default(tmp_path) == "steel-man"

    def test_missing_config_defaults_to_advisor(self, tmp_path: Path) -> None:
        assert load_default(tmp_path) == "advisor"

    def test_unknown_stance_name_is_refused(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(ValueError, match="unknown stance"):
            save_default(tmp_path, "sycophant")


class TestStanceShape:
    def test_stances_are_frozen(self) -> None:
        import pytest

        with pytest.raises(AttributeError):
            STANCES[0].name = "other"  # type: ignore[misc]

    def test_lookup_by_name(self) -> None:
        assert Stance.by_name("advisor").name == "advisor"
        import pytest

        with pytest.raises(ValueError, match="unknown stance"):
            Stance.by_name("nope")
