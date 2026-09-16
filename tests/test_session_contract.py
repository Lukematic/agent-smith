"""Phase 2: the controller session contract.

A session binds to one plan; the binding is stable across restarts; plans
are isolated from each other; every consequential action asks first with
recorded provenance; and interrupting a session then restarting shows the
same pending work, approval state, and budget -- all derived from stored
facts, never from memory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from awino import controller as C
from awino.controller import ApprovalRequired, PlanController


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "state"


class TestSessionBinding:
    def test_binding_is_stable_across_rebinds(self, root: Path) -> None:
        first = C.for_plan(root, "session-plan")
        C.queue_action(first.controller, "a1")
        second = C.for_plan(root, "session-plan")
        assert second.controller.state.plan_id == "session-plan"
        assert second.controller.state.pending_action_ids == ["a1"]
        # No duplicate plan was created: one plan file, one journal.
        assert len(list((root / "plans").iterdir())) == 1

    def test_sessions_are_isolated(self, root: Path) -> None:
        one = C.for_plan(root, "one")
        two = C.for_plan(root, "two")
        aid = C.request_approval(one.controller, "consequential", action_id="a-one")
        C.grant_approval(one.controller, aid, by="luke", plan_level=True)
        C.queue_action(one.controller, "work-one")
        assert two.controller.state.pending_action_ids == []
        assert two.controller.state.approval_state == "pending"

    def test_every_mutation_is_journaled(self, root: Path) -> None:
        adapter = C.for_plan(root, "p")
        before = adapter.controller.event_count()
        C.queue_action(adapter.controller, "a1")
        C.record_review(adapter.controller, verdict="ship", by="luke")
        assert adapter.controller.event_count() == before + 2


class TestAskFirst:
    def test_consequential_action_asks_before_it_acts(self, root: Path) -> None:
        adapter = C.for_loop(root, "loop-7")
        aid = adapter.request_approval("open an expensive floor", "costs subprocesses")
        with pytest.raises(ApprovalRequired, match="needs human approval"):
            adapter.require_approval(aid)
        assert any("AWAITING_APPROVAL" in line for line in adapter.status_lines())

    def test_provenance_is_recorded(self, root: Path) -> None:
        adapter = C.for_machine(root, "run-3")
        aid = adapter.request_approval("consequential", action_id="do-it")
        C.grant_approval(adapter.controller, aid, by="luke")
        grant = adapter.require_approval(aid)
        assert grant["by"] == "luke"
        assert grant["at"]
        snapshot = adapter.controller.status_snapshot()
        assert snapshot["approvals_granted"] == 1
        assert snapshot["pending_approvals"] == []


class TestInterruptAndRestart:
    def test_restart_shows_the_same_pending_work(self, root: Path) -> None:
        adapter = C.for_plan(root, "p")
        aid = adapter.request_approval("run exams", "consequential")
        C.grant_approval(adapter.controller, aid, by="luke")
        C.queue_action(adapter.controller, "a1")
        C.charge_budget(adapter.controller, "floors", 1)
        lines_before = adapter.status_lines()

        restarted = C.for_plan(root, "p")
        assert restarted.status_lines() == lines_before
        text = "\n".join(restarted.status_lines())
        assert "PENDING  1 action(s): a1" in text
        assert "APPROVAL  pending (1 granted, 0 pending)" in text
        assert "floors 1/3" in text

    def test_nothing_about_status_comes_from_memory(self, root: Path) -> None:
        # Two brand-new controller objects on the same dir agree exactly:
        # the snapshot is a pure function of stored facts.
        adapter = C.for_plan(root, "p", scope=["x.py"])
        C.queue_action(adapter.controller, "a1")
        a = PlanController.load(root, "p").status_snapshot()
        b = PlanController.load(root, "p").status_snapshot()
        assert a == b
        assert a["scope"] == ["x.py"]
        assert a["pending_actions"] == ["a1"]
