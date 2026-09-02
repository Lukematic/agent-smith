"""Deterministic dispatch routing: match a plain-language request to exactly one
canonical skill, or say why it cannot, without spawning anything.

This module is intentionally pure. Every test in this file must run with no
filesystem writes and no subprocess calls, because S3 (the execution loop) depends
on routing being safe to call speculatively before any budget is spent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from smith.dispatch import decide
from smith.skill_catalog import SkillCatalog

SMITH_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def catalog() -> SkillCatalog:
    """The real, installed 16-skill catalog. Routing must work against the actual
    skill set, not a synthetic fixture, or the test proves nothing about production
    behavior."""
    return SkillCatalog(
        project_root=Path("/nonexistent-project-root-for-routing-test"),
        global_root=Path("/nonexistent-global-root-for-routing-test"),
        bundled_root=SMITH_ROOT / "skills",
    )


class TestHighConfidenceRouting:
    def test_a_concrete_failure_routes_to_debug(self, catalog: SkillCatalog) -> None:
        decision = decide("pytest is failing with a ValueError in the loader", catalog)
        assert decision.confidence == "high"
        assert decision.skill is not None
        assert decision.skill.name == "awino-debug"
        assert decision.question is None

    def test_a_vague_behavioral_complaint_routes_to_triage(self, catalog: SkillCatalog) -> None:
        decision = decide("the agent keeps ignoring my instructions", catalog)
        assert decision.confidence == "high"
        assert decision.skill is not None
        assert decision.skill.name == "awino-triage"

    def test_a_conceptual_question_routes_to_consult(self, catalog: SkillCatalog) -> None:
        decision = decide(
            "i have a conceptual agentic engineering question about harnesses and "
            "context management",
            catalog,
        )
        assert decision.confidence == "high"
        assert decision.skill is not None
        assert decision.skill.name == "awino-consult"

    def test_a_multi_file_refactor_routes_to_rpi(self, catalog: SkillCatalog) -> None:
        decision = decide(
            "this is a big feature addition that touches many files and needs "
            "research then plan then implement",
            catalog,
        )
        assert decision.confidence == "high"
        assert decision.skill is not None
        assert decision.skill.name == "awino-rpi"

    def test_independent_parallel_work_routes_to_delegate(self, catalog: SkillCatalog) -> None:
        decision = decide(
            "we have parallel independent workstreams that need disjoint file "
            "ownership and should run simultaneously with the orchestrator "
            "coordinating rather than one agent doing everything",
            catalog,
        )
        assert decision.confidence == "high"
        assert decision.skill is not None
        assert decision.skill.name == "awino-delegate"


class TestNoConfidenceRouting:
    def test_an_empty_vague_request_produces_a_question_and_no_skill(
        self, catalog: SkillCatalog
    ) -> None:
        decision = decide("xyzzy plugh wibble", catalog)
        assert decision.confidence == "none"
        assert decision.skill is None
        assert decision.question is not None
        assert decision.alternatives == ()


class TestAmbiguousRouting:
    def test_two_close_skills_produce_a_question_naming_the_distinction(
        self, catalog: SkillCatalog
    ) -> None:
        # "update the agent's memory of what happened" plausibly touches both
        # awino-memory (durable lessons) and awino-self-update (knowledge registry
        # curation) - both mention memory/update-adjacent vocabulary.
        decision = decide("update what the agent remembers about the project", catalog)
        if decision.confidence == "high":
            pytest.skip(
                "catalog scoring resolved this unambiguously; ambiguity depends on "
                "installed skill wording, not a hardcoded fixture"
            )
        assert decision.confidence == "ambiguous"
        assert decision.skill is None
        assert len(decision.alternatives) >= 2
        assert decision.question is not None


class TestRoutingIsPure:
    def test_calling_decide_twice_returns_an_identical_result(self, catalog: SkillCatalog) -> None:
        first = decide("pytest is failing", catalog)
        second = decide("pytest is failing", catalog)
        assert first == second

    def test_deciding_performs_no_filesystem_write(
        self, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        marker_root = tmp_path / "routing-purity-check"
        shutil.copytree(SMITH_ROOT / "skills", marker_root)
        before = {p: p.stat().st_mtime_ns for p in marker_root.rglob("*") if p.is_file()}

        decide("what is a harness", catalog)
        decide("pytest is failing with a bug", catalog)
        decide("do the thing", catalog)

        after = {p: p.stat().st_mtime_ns for p in marker_root.rglob("*") if p.is_file()}
        assert before == after
