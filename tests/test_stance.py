"""Stances: how the controller talks to the human, switched by *their* words.

The spec's Phase 5 table row by row. detect() is deterministic - keyword and
phase rules, no model call - so a stance switch is testable, and never silent:
the caller must print the STANCE line whenever the result differs from the
current stance.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from awino.stance import (
    STANCES,
    Stance,
    baseline_stance,
    detect,
    load_default,
    resolve_stance,
    save_default,
)


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


class TestParaphraseDetection:
    """Every stance fires on the original phrasing and a new paraphrase."""

    CASES: ClassVar[list[tuple[str, str, str]]] = [
        # (stance, original-style prompt, new paraphrase prompt)
        (
            "advisor",
            "fix the failing test in ci",
            "run the suite",
        ),
        (
            "first-principles",
            "let's break this down into fundamentals",
            "what are the first principles behind this design",
        ),
        (
            "steel-man",
            "I think we should rewrite the whole module in rust",
            "play devil's advocate on my plan to rewrite the module",
        ),
        (
            "assumption-audit",
            "so that means the cache is the bottleneck",
            "what am I missing in concluding the cache is the bottleneck",
        ),
        (
            "teach-back",
            "teach me how the ledger works",
            "help me understand how the ledger works",
        ),
        (
            "research-intake",
            "research what agent memory approaches exist",
            "look into what agent memory approaches exist",
        ),
        (
            "expert",
            "honestly, how would you handle this burnout",
            "as a human, how would you handle this burnout",
        ),
    ]

    @pytest.mark.parametrize("stance,original,paraphrase", CASES)
    def test_original_phrasing_fires(self, stance: str, original: str, paraphrase: str) -> None:
        hit = detect(original)
        if stance == "advisor":
            assert hit is None  # advisor is the no-match default
        else:
            assert hit is not None and hit.name == stance

    @pytest.mark.parametrize("stance,original,paraphrase", CASES)
    def test_paraphrase_fires(self, stance: str, original: str, paraphrase: str) -> None:
        hit = detect(paraphrase)
        if stance == "advisor":
            assert hit is None
        else:
            assert hit is not None and hit.name == stance

    def test_more_paraphrases_fire_steel_man(self) -> None:
        for prompt in (
            "I'm leaning toward rewriting it in rust",
            "challenge this plan before I present it",
            "push back on the proposal",
            "give me the other side of this argument",
            "poke holes in my design",
        ):
            assert detect(prompt).name == "steel-man", prompt

    def test_specific_intents_still_checked_before_broader_ones(self) -> None:
        # Expert-ish framing around a stated position must steel-man.
        assert detect("honestly, I think we should rewrite it").name == "steel-man"
        # Position-challenging language must not leak into assumption-audit.
        assert detect("poke holes in my plan to ship Friday").name == "steel-man"


class TestChallengeMeBaseline:
    def _write_profile(self, tmp_path: Path, text: str) -> Path:
        profile = tmp_path / "profile.yaml"
        profile.write_text(text, encoding="utf-8")
        return profile

    def test_challenge_me_true_makes_neutral_prompt_advisor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = self._write_profile(tmp_path, "challenge_me: true\n")
        monkeypatch.setenv("AWINO_PROFILE", str(profile))
        resolved = resolve_stance("what time is it")
        assert resolved is not None
        assert resolved.name == "advisor"

    def test_specific_match_still_wins_over_challenge_me(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = self._write_profile(tmp_path, "challenge_me: true\n")
        monkeypatch.setenv("AWINO_PROFILE", str(profile))
        assert resolve_stance("teach me how the ledger works").name == "teach-back"

    def test_no_profile_keeps_existing_behavior(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AWINO_PROFILE", str(tmp_path / "no-such-file.yaml"))
        assert resolve_stance("what time is it") is None
        assert resolve_stance("what time is it", current="steel-man").name == "steel-man"

    def test_challenge_me_false_or_missing_key_keeps_existing_behavior(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for text in ("challenge_me: false\n", "other_key: 1\n"):
            profile = self._write_profile(tmp_path, text)
            monkeypatch.setenv("AWINO_PROFILE", str(profile))
            assert resolve_stance("what time is it") is None

    def test_baseline_stance_reads_explicit_path(self, tmp_path: Path) -> None:
        assert baseline_stance(tmp_path / "nope.yaml") == "default"
        assert baseline_stance(self._write_profile(tmp_path, "challenge_me: true\n")) == "advisor"
        assert baseline_stance(self._write_profile(tmp_path, "challenge_me: false\n")) == "default"

    def test_advisor_carries_the_challenge_rules(self) -> None:
        rules = Stance.by_name("advisor").rules
        assert "Disagree in three lines" in rules
        assert "No validation phrases" in rules


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
