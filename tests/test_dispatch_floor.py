"""Portable dispatch floors: the trip split into open/close so *any* agent
environment - Kilo, Claude Code, Cline, a human in a terminal - can be the
worker, with the ledger holding state between the two calls.

This removes the external-CLI dependency Phase 0 exposed: `claude -p` demands
its own login on the worker machine, while a floor prompt file can be executed
by whatever authenticated agent is already running. The closer re-runs
verification itself with a real subprocess, so a completion claim alone still
never produces COMPLETE regardless of who did the work.

Also the D1 regression suite: the floor prompt must contain the routed skill's
actual SKILL.md text, not just its name.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from smith.dispatch import (
    FloorState,
    close_floor,
    open_floor,
)
from smith.enforce import Ledger, TaskClass
from smith.skill_catalog import SkillCatalog

SMITH_ROOT = Path(__file__).resolve().parents[1]

_DEBUG_REQUEST = "pytest is failing because the marker file does not exist; fix it"


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "state")


@pytest.fixture
def run_id(ledger: Ledger) -> str:
    return ledger.open(TaskClass.QUESTION, "portable dispatch trip").run_id


@pytest.fixture
def catalog() -> SkillCatalog:
    return SkillCatalog(
        project_root=Path("/nonexistent-project-for-floor-test"),
        global_root=Path("/nonexistent-global-for-floor-test"),
        bundled_root=SMITH_ROOT / "skills",
    )


def _passing_verify(tmp_path: Path) -> tuple[Path, str]:
    marker = tmp_path / "marker.txt"
    script = tmp_path / "check.py"
    script.write_text(
        f"import pathlib,sys\nsys.exit(0 if pathlib.Path(r'{marker}').exists() else 1)\n",
        encoding="utf-8",
    )
    return marker, f'"{sys.executable}" "{script}"'


class TestOpenFloorAttachesTheSkill:
    def test_the_prompt_file_contains_the_skill_md_text_not_just_its_name(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        _marker, verify = _passing_verify(tmp_path)
        state = open_floor(
            ledger,
            run_id,
            _DEBUG_REQUEST,
            catalog,
            SMITH_ROOT,
            verify,
            file_scope=["marker.txt"],
        )

        assert state.skill == "awino-debug"
        prompt = Path(state.prompt_path).read_text(encoding="utf-8")
        skill_text = (SMITH_ROOT / "skills" / "awino-debug" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        # The prompt must carry the skill's actual opening heading, proving the
        # worker receives the procedure and not merely a name-drop.
        first_heading = next(line for line in skill_text.splitlines() if line.startswith("#"))
        assert first_heading in prompt

    def test_open_floor_persists_pending_state_in_the_ledger(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        _marker, verify = _passing_verify(tmp_path)
        state = open_floor(
            ledger,
            run_id,
            _DEBUG_REQUEST,
            catalog,
            SMITH_ROOT,
            verify,
            file_scope=["marker.txt"],
        )

        pending = ledger.latest_artifact(run_id, "dispatch-pending")
        assert pending is not None
        assert pending.payload["floor"] == 1
        assert pending.payload["skill"] == state.skill
        assert pending.payload["verification"] == verify

    def test_an_ambiguous_request_refuses_with_a_question_and_no_prompt_file(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        _marker, verify = _passing_verify(tmp_path)
        with pytest.raises(ValueError, match="confidence"):
            open_floor(
                ledger,
                run_id,
                "xyzzy plugh wibble",
                catalog,
                SMITH_ROOT,
                verify,
                file_scope=["marker.txt"],
            )


class TestCloseFloorVerifiesForItself:
    def test_a_passing_verification_completes_the_trip(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        marker, verify = _passing_verify(tmp_path)
        open_floor(
            ledger,
            run_id,
            _DEBUG_REQUEST,
            catalog,
            SMITH_ROOT,
            verify,
            file_scope=["marker.txt"],
        )
        marker.write_text("done", encoding="utf-8")  # the "worker" did the work

        result = close_floor(ledger, run_id, tmp_path)

        assert result.outcome.value == "complete"
        floors = ledger.artifacts(run_id, "dispatch-floor")
        assert len(floors) == 1
        assert floors[0].payload["verified"] is True

    def test_a_failing_verification_opens_the_next_floor_with_the_failure_text(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        _marker, verify = _passing_verify(tmp_path)
        open_floor(
            ledger,
            run_id,
            _DEBUG_REQUEST,
            catalog,
            SMITH_ROOT,
            verify,
            file_scope=["marker.txt"],
        )
        # worker did nothing: marker absent, verification will exit 1

        result = close_floor(ledger, run_id, tmp_path)

        assert result.outcome.value == "revise"
        assert result.next_state is not None
        assert result.next_state.floor == 2
        next_prompt = Path(result.next_state.prompt_path).read_text(encoding="utf-8")
        assert "verification failed" in next_prompt.lower()

    def test_the_budget_is_exhausted_at_max_floors(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        _marker, verify = _passing_verify(tmp_path)
        open_floor(
            ledger,
            run_id,
            _DEBUG_REQUEST,
            catalog,
            SMITH_ROOT,
            verify,
            file_scope=["marker.txt"],
            max_floors=2,
        )
        first = close_floor(ledger, run_id, tmp_path)
        assert first.outcome.value == "revise"

        second = close_floor(ledger, run_id, tmp_path)
        assert second.outcome.value == "max-iterations"
        assert second.next_state is None

    def test_close_without_an_open_floor_refuses(
        self, ledger: Ledger, run_id: str, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="no pending floor"):
            close_floor(ledger, run_id, tmp_path)

    def test_distinct_floor_identities_are_persisted_across_a_revise(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        marker, verify = _passing_verify(tmp_path)
        open_floor(
            ledger,
            run_id,
            _DEBUG_REQUEST,
            catalog,
            SMITH_ROOT,
            verify,
            file_scope=["marker.txt"],
        )
        close_floor(ledger, run_id, tmp_path)  # fails -> floor 2 opens
        marker.write_text("done", encoding="utf-8")
        close_floor(ledger, run_id, tmp_path)  # passes

        floors = ledger.artifacts(run_id, "dispatch-floor")
        identities = [f.payload["invocation_id"] for f in floors]
        assert len(identities) == 2
        assert len(set(identities)) == 2


class TestFloorStateRoundTrip:
    def test_state_survives_a_fresh_ledger_instance(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        marker, verify = _passing_verify(tmp_path)
        open_floor(
            ledger,
            run_id,
            _DEBUG_REQUEST,
            catalog,
            SMITH_ROOT,
            verify,
            file_scope=["marker.txt"],
        )
        marker.write_text("done", encoding="utf-8")

        # A different process picks up the trip: new Ledger object, same root.
        fresh = Ledger(ledger.state_root)
        result = close_floor(fresh, run_id, tmp_path)
        assert result.outcome.value == "complete"

    def test_floor_state_dataclass_exposes_what_a_harness_needs(
        self, ledger: Ledger, run_id: str, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        _marker, verify = _passing_verify(tmp_path)
        state = open_floor(
            ledger,
            run_id,
            _DEBUG_REQUEST,
            catalog,
            SMITH_ROOT,
            verify,
            file_scope=["marker.txt"],
        )
        assert isinstance(state, FloorState)
        assert state.floor == 1
        assert state.prompt_path
        assert state.invocation_id
        assert state.skill == "awino-debug"


class TestReviewerFloor:
    """S3: the graph's real value (independent reviewer) with no login - a
    reviewer floor is just a floor whose role is mechanically read-only."""

    def _catalog(self):
        return SkillCatalog(Path("/n"), Path("/n"), SMITH_ROOT / "skills")

    def test_open_floor_reviewer_role_is_read_only_and_writes_no_scope(
        self, ledger: Ledger, run_id: str, tmp_path: Path
    ) -> None:
        from smith.dispatch import open_floor

        _marker, verify = _passing_verify(tmp_path)
        state = open_floor(
            ledger,
            run_id,
            "review the change",
            self._catalog(),
            SMITH_ROOT,
            verify,
            file_scope=[],
            role="reviewer",
            max_floors=1,
            project=tmp_path,
        )
        prompt = Path(state.prompt_path).read_text(encoding="utf-8")
        assert "Files you may WRITE" not in prompt or "none" in prompt.lower()
        pending = ledger.latest_artifact(run_id, "dispatch-pending")
        assert pending.payload["role"] == "reviewer"

    def test_reviewer_verdict_file_lives_under_state_root_not_project(
        self, ledger: Ledger, run_id: str, tmp_path: Path
    ) -> None:
        from smith.dispatch import open_floor

        _marker, verify = _passing_verify(tmp_path)
        state = open_floor(
            ledger,
            run_id,
            "review",
            self._catalog(),
            SMITH_ROOT,
            verify,
            file_scope=[],
            role="reviewer",
            max_floors=1,
            project=tmp_path,
        )
        assert str(ledger.state_root) in state.verdict_path
        assert str(tmp_path) not in state.verdict_path or "reviews" in state.verdict_path

    def test_ship_verdict_completes(self, ledger: Ledger, run_id: str, tmp_path: Path) -> None:
        from smith.dispatch import close_floor, open_floor

        _marker, verify = _passing_verify(tmp_path)
        state = open_floor(
            ledger,
            run_id,
            "review",
            self._catalog(),
            SMITH_ROOT,
            verify,
            file_scope=[],
            role="reviewer",
            max_floors=1,
            project=tmp_path,
        )
        Path(state.verdict_path).parent.mkdir(parents=True, exist_ok=True)
        Path(state.verdict_path).write_text(
            json.dumps({"verdict": "SHIP", "feedback": "looks right"}), encoding="utf-8"
        )
        result = close_floor(ledger, run_id, tmp_path)
        assert result.outcome.value == "complete"
        review = ledger.latest_artifact(run_id, "dispatch-review")
        assert review.payload["verdict"] == "SHIP"

    def test_revise_verdict_opens_a_worker_floor_with_feedback(
        self, ledger: Ledger, run_id: str, tmp_path: Path
    ) -> None:
        from smith.dispatch import close_floor, open_floor

        _marker, verify = _passing_verify(tmp_path)
        state = open_floor(
            ledger,
            run_id,
            "review",
            self._catalog(),
            SMITH_ROOT,
            verify,
            file_scope=[],
            role="reviewer",
            max_floors=2,
            project=tmp_path,
        )
        Path(state.verdict_path).parent.mkdir(parents=True, exist_ok=True)
        Path(state.verdict_path).write_text(
            json.dumps({"verdict": "REVISE", "feedback": "off-by-one in the loop bound"}),
            encoding="utf-8",
        )
        result = close_floor(ledger, run_id, tmp_path)
        assert result.outcome.value == "revise"
        assert result.next_state is not None
        next_prompt = Path(result.next_state.prompt_path).read_text(encoding="utf-8")
        assert "off-by-one in the loop bound" in next_prompt
        assert result.next_state.role != "reviewer"

    def test_missing_verdict_file_blocks(self, ledger: Ledger, run_id: str, tmp_path: Path) -> None:
        from smith.dispatch import close_floor, open_floor

        _marker, verify = _passing_verify(tmp_path)
        open_floor(
            ledger,
            run_id,
            "review",
            self._catalog(),
            SMITH_ROOT,
            verify,
            file_scope=[],
            role="reviewer",
            max_floors=1,
            project=tmp_path,
        )
        # deliberately do not write the verdict; force close via a verify that fails
        result = close_floor(ledger, run_id, tmp_path)
        assert result.outcome.value in ("max-iterations", "blocked")

    def test_malformed_verdict_blocks(self, ledger: Ledger, run_id: str, tmp_path: Path) -> None:
        from smith.dispatch import close_floor, open_floor

        _marker, verify = _passing_verify(tmp_path)
        state = open_floor(
            ledger,
            run_id,
            "review",
            self._catalog(),
            SMITH_ROOT,
            verify,
            file_scope=[],
            role="reviewer",
            max_floors=1,
            project=tmp_path,
        )
        Path(state.verdict_path).parent.mkdir(parents=True, exist_ok=True)
        Path(state.verdict_path).write_text("not json", encoding="utf-8")
        result = close_floor(ledger, run_id, tmp_path)
        assert result.outcome.value in ("max-iterations", "blocked")
