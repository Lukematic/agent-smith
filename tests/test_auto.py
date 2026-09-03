"""`awino auto`: the bounded Seed driver, proven with a real ledger and real
floors - only the tracker and the worker are injected, because the real
tracker shells out to `sd` and the real worker is whatever harness is present.

The stopping rules are the product: max-seeds honored exactly, first
non-complete Seed stops the whole loop with a pending human decision, planned
task classes refuse to run without a human-approved plan label, and nothing
pushes anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from smith.auto import AutoResult, run_auto
from smith.dispatch import FloorState
from smith.enforce import Ledger
from smith.seeds import Issue
from smith.skill_catalog import SkillCatalog

SMITH_ROOT = Path(__file__).resolve().parents[1]

_DEBUG_TITLE = "pytest is failing because the marker file is missing; fix it"


class FakeTracker:
    """Stands in for seeds.Seeds: ready() and close() only."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        self.closed: list[tuple[str, str]] = []

    def ready(self, limit: int = 20) -> list[Issue]:
        return [i for i in self.issues if i.status == "open"][:limit]

    def close(self, issue_id: str, reason: str) -> None:
        self.closed.append((issue_id, reason))
        self.issues = [
            Issue(i.id, i.title, "closed", i.type, i.priority, i.labels, i.assignee, i.description)
            if i.id == issue_id
            else i
            for i in self.issues
        ]


def _issue(n: int, *, type_: str = "bug", labels: tuple[str, ...] = ()) -> Issue:
    return Issue(f"seed-{n}", _DEBUG_TITLE, "open", type_, 1, labels, None, f"seed number {n}")


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "state")


@pytest.fixture
def catalog() -> SkillCatalog:
    return SkillCatalog(
        project_root=Path("/nonexistent-p"),
        global_root=Path("/nonexistent-g"),
        bundled_root=SMITH_ROOT / "skills",
    )


def _verify_cmd(tmp_path: Path) -> tuple[Path, str]:
    marker = tmp_path / "marker.txt"
    script = tmp_path / "check.py"
    script.write_text(
        f"import pathlib,sys\nsys.exit(0 if pathlib.Path(r'{marker}').exists() else 1)\n",
        encoding="utf-8",
    )
    return marker, f'"{sys.executable}" "{script}"'


class TestBudget:
    def test_max_seeds_is_honored_exactly(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        marker, verify = _verify_cmd(tmp_path)
        tracker = FakeTracker([_issue(1), _issue(2), _issue(3)])

        def worker(state: FloorState) -> None:
            marker.write_text("done", encoding="utf-8")

        result = run_auto(
            ledger,
            tracker,  # type: ignore[arg-type]
            catalog,
            SMITH_ROOT,
            tmp_path,
            worker,
            verify,
            max_seeds=2,
            confirmed_budget=True,
        )
        assert result.stopped_because == "max seeds reached"
        assert [s.outcome for s in result.seeds] == ["closed", "closed"]
        assert len(tracker.closed) == 2

    def test_unconfirmed_budget_refuses_before_touching_anything(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        tracker = FakeTracker([_issue(1)])
        result = run_auto(
            ledger,
            tracker,  # type: ignore[arg-type]
            catalog,
            SMITH_ROOT,
            tmp_path,
            lambda s: None,
            "true",
            max_seeds=1,
            confirmed_budget=False,
        )
        assert result.seeds == ()
        assert "confirmation" in result.stopped_because
        assert tracker.closed == []


class TestStopOnFirstFailure:
    def test_a_seed_that_never_verifies_stops_the_loop_with_a_pending_decision(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        _marker, verify = _verify_cmd(tmp_path)  # marker never written -> always fails
        tracker = FakeTracker([_issue(1), _issue(2)])

        result = run_auto(
            ledger,
            tracker,  # type: ignore[arg-type]
            catalog,
            SMITH_ROOT,
            tmp_path,
            lambda s: None,
            verify,
            max_seeds=2,
            confirmed_budget=True,
        )
        assert result.seeds[0].outcome == "blocked"
        assert len(result.seeds) == 1  # seed-2 was never started
        assert "human decision pending" in result.stopped_because
        assert tracker.closed == []

        # The pending decision is durable, not conversational.
        current = ledger.inspect_current()
        assert current.run is not None
        pending = [c for c in current.run.checkpoints if c.pending_decision]
        assert pending


class TestPlannedClassesNeedApproval:
    def test_a_feature_seed_without_plan_approved_label_is_refused(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        _marker, verify = _verify_cmd(tmp_path)
        tracker = FakeTracker([_issue(1, type_="feature")])
        result = run_auto(
            ledger,
            tracker,  # type: ignore[arg-type]
            catalog,
            SMITH_ROOT,
            tmp_path,
            lambda s: None,
            verify,
            max_seeds=1,
            confirmed_budget=True,
        )
        assert result.seeds[0].outcome == "refused"
        assert "approved plan" in result.seeds[0].detail
        assert tracker.closed == []

    def test_a_bug_seed_needs_no_plan(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        marker, verify = _verify_cmd(tmp_path)
        tracker = FakeTracker([_issue(1, type_="bug")])

        result = run_auto(
            ledger,
            tracker,  # type: ignore[arg-type]
            catalog,
            SMITH_ROOT,
            tmp_path,
            lambda s: marker.write_text("done", encoding="utf-8"),
            verify,
            max_seeds=1,
            confirmed_budget=True,
        )
        assert result.seeds[0].outcome == "closed"


class TestRevisePathInsideOneSeed:
    def test_a_seed_that_fails_once_then_verifies_closes_after_the_revise_floor(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        marker, verify = _verify_cmd(tmp_path)
        tracker = FakeTracker([_issue(1)])
        attempts: list[int] = []

        def worker(state: FloorState) -> None:
            attempts.append(state.floor)
            if state.floor >= 2:
                marker.write_text("done", encoding="utf-8")

        result = run_auto(
            ledger,
            tracker,  # type: ignore[arg-type]
            catalog,
            SMITH_ROOT,
            tmp_path,
            worker,
            verify,
            max_seeds=1,
            confirmed_budget=True,
        )
        assert result.seeds[0].outcome == "closed"
        assert attempts == [1, 2]


class TestNothingIsPushed:
    def test_run_auto_never_invokes_git_push(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as sp

        pushes: list[str] = []
        original = sp.run

        def spy(cmd, *args, **kwargs):
            text = cmd if isinstance(cmd, str) else " ".join(map(str, cmd))
            if "push" in text:
                pushes.append(text)
            return original(cmd, *args, **kwargs)

        monkeypatch.setattr(sp, "run", spy)
        marker, verify = _verify_cmd(tmp_path)
        tracker = FakeTracker([_issue(1)])
        run_auto(
            ledger,
            tracker,  # type: ignore[arg-type]
            catalog,
            SMITH_ROOT,
            tmp_path,
            lambda s: marker.write_text("done", encoding="utf-8"),
            verify,
            max_seeds=1,
            confirmed_budget=True,
        )
        assert pushes == []


class TestResultShape:
    def test_max_seeds_below_one_is_refused(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="max_seeds"):
            run_auto(
                ledger,
                FakeTracker([]),  # type: ignore[arg-type]
                catalog,
                SMITH_ROOT,
                tmp_path,
                lambda s: None,
                "true",
                max_seeds=0,
                confirmed_budget=True,
            )

    def test_no_ready_seeds_reports_that(
        self, ledger: Ledger, catalog: SkillCatalog, tmp_path: Path
    ) -> None:
        result = run_auto(
            ledger,
            FakeTracker([]),  # type: ignore[arg-type]
            catalog,
            SMITH_ROOT,
            tmp_path,
            lambda s: None,
            "true",
            max_seeds=1,
            confirmed_budget=True,
        )
        assert isinstance(result, AutoResult)
        assert result.stopped_because == "no ready seeds"
