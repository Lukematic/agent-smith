"""Phase 2: concurrent and interrupted submissions.

Duplicate or interrupted runs must never double-apply; conflicting
concurrent edits refuse instead of silently merging; identical
resubmissions converge on the one recorded outcome.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from awino import controller as C
from awino.controller import PlanBudgetExhausted, PlanConflict, PlanController


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "state"


def _roll_back_plan_file(root: Path, plan_id: str, to_revision: int) -> None:
    """Simulate a crash between journal append and plan-file save: the
    journal is ahead of the plan file."""
    plan_file = root / "plans" / plan_id / "plan.json"
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    data["plan_revision"] = to_revision
    data["pending_action_ids"] = []
    data["budget_used"] = dict.fromkeys(data["budgets"], 0)
    plan_file.write_text(json.dumps(data), encoding="utf-8")


class TestDuplicateSubmissions:
    def test_same_event_id_twice_applies_once(self, root: Path) -> None:
        plan = PlanController.create(root, "p", budgets={"floors": 5})
        one = plan.submit_event(event_id="evt-1", kind="action_queued", payload={"action_id": "a1"})
        two = plan.submit_event(event_id="evt-1", kind="action_queued", payload={"action_id": "a1"})
        assert one == two
        assert plan.state.pending_action_ids == ["a1"]
        assert plan.event_count() == 2  # plan_created + the one application

    def test_two_instances_converge_on_one_outcome(self, root: Path) -> None:
        first = PlanController.create(root, "p", budgets={"floors": 5})
        second = PlanController.load(root, "p")  # a second "process"
        outcome_a = first.submit_event(
            event_id="evt-1", kind="action_queued", payload={"action_id": "a1"}
        )
        outcome_b = second.submit_event(
            event_id="evt-1", kind="action_queued", payload={"action_id": "a1"}
        )
        assert outcome_a == outcome_b
        assert PlanController.load(root, "p").state.pending_action_ids == ["a1"]

    def test_duplicate_budget_charge_does_not_double_count(self, root: Path) -> None:
        plan = PlanController.create(root, "p", budgets={"floors": 5})
        one = plan.submit_event(
            event_id="charge-1",
            kind="budget_charged",
            payload={"budget": "floors", "amount": 2},
        )
        two = plan.submit_event(
            event_id="charge-1",
            kind="budget_charged",
            payload={"budget": "floors", "amount": 2},
        )
        assert one == two
        assert plan.state.budget_used["floors"] == 2

    def test_concurrent_instances_use_distinct_atomic_temp_files(self, root: Path) -> None:
        """Windows must not share a fixed plan.json.tmp between writers."""
        first = PlanController.create(root, "p", budgets={"floors": 5})
        second = PlanController.load(root, "p")
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def submit(plan: PlanController, event_id: str, action_id: str) -> None:
            try:
                barrier.wait()
                plan.submit_event(
                    event_id=event_id, kind="action_queued", payload={"action_id": action_id}
                )
            except (
                Exception
            ) as exc:  # concurrent stale conflicts are allowed; OS write errors are not
                errors.append(exc)

        workers = [
            threading.Thread(target=submit, args=(first, "evt-a", "a")),
            threading.Thread(target=submit, args=(second, "evt-b", "b")),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert not [error for error in errors if isinstance(error, PermissionError)]
        assert not list((root / "plans" / "p").glob("*.tmp"))


class TestConflictingConcurrentEdits:
    def test_stale_writer_refuses_then_succeeds_after_reload(self, root: Path) -> None:
        writer_a = PlanController.create(root, "p")
        writer_b = PlanController.load(root, "p")  # both see revision 1
        writer_a.submit_event(event_id="a-1", kind="scope_set", payload={"scope": ["a.py"]})
        with pytest.raises(PlanConflict):
            writer_b.submit_event(
                event_id="b-1",
                kind="scope_set",
                payload={"scope": ["b.py"]},
                expected_revision=1,  # stale: the plan moved to 2
            )
        # After reloading, the same edit applies cleanly on the new revision.
        writer_b = PlanController.load(root, "p")
        outcome = writer_b.submit_event(
            event_id="b-1",
            kind="scope_set",
            payload={"scope": ["b.py"]},
            expected_revision=writer_b.state.plan_revision,
        )
        assert outcome["scope"] == ["b.py"]
        # One canonical result: the last writer's scope won, nothing merged.
        assert PlanController.load(root, "p").state.scope == ["b.py"]

    def test_refused_edit_leaves_no_journal_trace(self, root: Path) -> None:
        plan = PlanController.create(root, "p")
        before = plan.event_count()
        with pytest.raises(PlanConflict):
            plan.submit_event(
                event_id="x",
                kind="scope_set",
                payload={"scope": ["z.py"]},
                expected_revision=999,
            )
        assert plan.event_count() == before


class TestInterruptedRuns:
    def test_crash_between_journal_and_state_recovers_once(self, root: Path) -> None:
        plan = PlanController.create(root, "p", budgets={"floors": 5})
        plan.submit_event(event_id="q1", kind="action_queued", payload={"action_id": "a1"})
        assert plan.state.plan_revision == 2
        _roll_back_plan_file(root, "p", to_revision=1)

        recovered = PlanController.load(root, "p")
        assert recovered.state.pending_action_ids == ["a1"]
        assert recovered.state.plan_revision == 2
        # Recovery is not a new event: the journal did not grow.
        assert recovered.event_count() == 2

    def test_interrupted_budget_charge_never_double_counts(self, root: Path) -> None:
        plan = PlanController.create(root, "p", budgets={"floors": 5})
        C.charge_budget(plan, "floors", 2)
        _roll_back_plan_file(root, "p", to_revision=1)

        recovered = PlanController.load(root, "p")
        assert recovered.state.budget_used["floors"] == 2
        assert recovered.state.plan_revision == 2

    def test_recovery_is_idempotent(self, root: Path) -> None:
        plan = PlanController.create(root, "p", budgets={"floors": 5})
        plan.submit_event(event_id="q1", kind="action_queued", payload={"action_id": "a1"})
        _roll_back_plan_file(root, "p", to_revision=1)
        once = PlanController.load(root, "p")
        twice = PlanController.load(root, "p")
        assert once.status_snapshot() == twice.status_snapshot()
        assert twice.state.pending_action_ids == ["a1"]

    def test_budget_ceiling_survives_the_crash_window(self, root: Path) -> None:
        plan = PlanController.create(root, "p", budgets={"floors": 2})
        C.charge_budget(plan, "floors", 2)
        _roll_back_plan_file(root, "p", to_revision=1)
        recovered = PlanController.load(root, "p")
        with pytest.raises(PlanBudgetExhausted):
            C.charge_budget(recovered, "floors", 1)
