"""Phase 2: one plan-bound durable controller.

The loop controllers and the machine controller share this durable plan
controller through explicit adapters. These tests pin the contract:
write-ahead events, exactly-once submission, revision conflicts, budget
ceilings, ask-first approvals, review, closure over stored facts, and
restart resuming from stored state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from awino import controller as C
from awino.controller import (
    ApprovalRequired,
    PlanAdapter,
    PlanBudgetExhausted,
    PlanConflict,
    PlanController,
    PlanError,
    PlanExists,
    PlanNotClosable,
    PlanNotFound,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def plan(root: Path) -> PlanController:
    return PlanController.create(root, "p1", scope=["a.py"], budgets={"floors": 2})


class TestPlanLifecycle:
    def test_create_and_load_round_trip(self, root: Path, plan: PlanController) -> None:
        assert (root / "plans" / "p1" / "plan.json").is_file()
        assert (root / "plans" / "p1" / "events.jsonl").is_file()
        loaded = PlanController.load(root, "p1")
        assert loaded.state.plan_id == "p1"
        assert loaded.state.scope == ["a.py"]
        assert loaded.state.plan_revision == plan.state.plan_revision

    def test_duplicate_create_refuses(self, plan: PlanController, root: Path) -> None:
        with pytest.raises(PlanExists):
            PlanController.create(root, "p1")

    def test_load_missing_refuses(self, root: Path) -> None:
        with pytest.raises(PlanNotFound):
            PlanController.load(root, "nope")

    def test_every_mutation_is_journaled(self, root: Path, plan: PlanController) -> None:
        before = plan.event_count()
        plan.submit_event(event_id="s1", kind="scope_set", payload={"scope": ["b.py"]})
        assert plan.event_count() == before + 1
        lines = (root / "plans" / "p1" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == plan.event_count()
        # The journal records the event BEFORE the outcome: write-ahead.
        first = json.loads(lines[0])
        assert first["kind"] == "plan_created"
        assert "outcome" in first

    def test_unknown_event_kind_refuses(self, plan: PlanController) -> None:
        with pytest.raises(PlanError, match="unknown controller event kind"):
            plan.submit_event(event_id="x", kind="frobnicate", payload={})


class TestIdempotentEvents:
    def test_second_identical_submission_applies_once(self, plan: PlanController) -> None:
        first = plan.submit_event(event_id="q1", kind="action_queued", payload={"action_id": "a1"})
        second = plan.submit_event(event_id="q1", kind="action_queued", payload={"action_id": "a1"})
        assert first == second
        assert plan.state.pending_action_ids == ["a1"]
        assert plan.state.plan_revision == first["plan_revision"]

    def test_replay_ignores_a_different_payload(self, plan: PlanController) -> None:
        first = plan.submit_event(event_id="q1", kind="action_queued", payload={"action_id": "a1"})
        # A retry that somehow carries a different payload must not fork the
        # plan: the recorded outcome is canonical.
        second = plan.submit_event(
            event_id="q1", kind="action_queued", payload={"action_id": "EVIL"}
        )
        assert second == first
        assert plan.state.pending_action_ids == ["a1"]

    def test_apply_action_is_idempotent(self, plan: PlanController) -> None:
        plan.submit_event(event_id="q1", kind="action_queued", payload={"action_id": "a1"})
        one = C.apply_action(plan, "a1", "done")
        two = C.apply_action(plan, "a1", "done")
        assert one == two
        assert plan.state.pending_action_ids == []


class TestRevisionConflicts:
    def test_matching_expected_revision_applies(self, plan: PlanController) -> None:
        rev = plan.state.plan_revision
        outcome = plan.submit_event(
            event_id="r1",
            kind="scope_set",
            payload={"scope": ["c.py"]},
            expected_revision=rev,
        )
        assert outcome["plan_revision"] == rev + 1

    def test_stale_expected_revision_refuses(self, plan: PlanController) -> None:
        plan.submit_event(event_id="r1", kind="scope_set", payload={"scope": ["c.py"]})
        before = plan.event_count()
        with pytest.raises(PlanConflict, match="stale revision"):
            plan.submit_event(
                event_id="r2",
                kind="scope_set",
                payload={"scope": ["d.py"]},
                expected_revision=0,
            )
        # The refusal leaves no trace: the journal did not grow.
        assert plan.event_count() == before
        assert plan.state.scope == ["c.py"]


class TestBudgets:
    def test_charge_within_ceiling(self, plan: PlanController) -> None:
        outcome = C.charge_budget(plan, "floors", 1)
        assert outcome == {
            "event_id": "budget-charged-floors-r2-1",
            "kind": "budget_charged",
            "budget": "floors",
            "used": 1,
            "ceiling": 2,
            "plan_revision": 2,
        }

    def test_charge_past_ceiling_refuses_without_a_trace(self, plan: PlanController) -> None:
        C.charge_budget(plan, "floors", 2)
        before = plan.event_count()
        with pytest.raises(PlanBudgetExhausted, match="would reach 3/2"):
            C.charge_budget(plan, "floors", 1)
        assert plan.event_count() == before
        assert plan.state.budget_used["floors"] == 2

    def test_preflight_reports_an_exhausted_budget(self, plan: PlanController) -> None:
        assert C.preflight(plan) == []
        C.charge_budget(plan, "floors", 2)
        problems = C.preflight(plan)
        assert any("budget exhausted: floors 2/2" in p for p in problems)

    def test_non_positive_charge_refuses(self, plan: PlanController) -> None:
        with pytest.raises(PlanError):
            C.charge_budget(plan, "floors", 0)


class TestAskFirstApprovals:
    def test_consequential_action_asks_first(self, plan: PlanController) -> None:
        aid = C.request_approval(plan, "run the exams", "touches the repo")
        with pytest.raises(ApprovalRequired, match="needs human approval"):
            C.require_approval(plan, aid)
        # The ask is recorded as pending: the human can see what waits.
        assert any(a["action_id"] == aid for a in plan.state.pending_approvals)

    def test_grant_records_provenance(self, plan: PlanController) -> None:
        aid = C.request_approval(plan, "run the exams")
        C.grant_approval(plan, aid, by="luke")
        grant = C.require_approval(plan, aid)
        assert grant["by"] == "luke"
        assert grant["at"]
        assert not any(a["action_id"] == aid for a in plan.state.pending_approvals)

    def test_grant_needs_a_name(self, plan: PlanController) -> None:
        aid = C.request_approval(plan, "run the exams")
        before = plan.event_count()
        with pytest.raises(PlanError, match="needs a name"):
            C.grant_approval(plan, aid, by="  ")
        # The refused grant left no journal trace.
        assert plan.event_count() == before

    def test_plan_level_grant_approves_the_plan(self, plan: PlanController) -> None:
        aid = C.request_approval(plan, "approve plan")
        C.grant_approval(plan, aid, by="luke", plan_level=True)
        assert plan.state.approval_state == "approved"

    def test_invalidation_stops_work_until_reapproval(self, plan: PlanController) -> None:
        aid = C.request_approval(plan, "approve plan")
        C.grant_approval(plan, aid, by="luke", plan_level=True)
        C.invalidate_approval(plan, "scope changed materially")
        assert plan.state.approval_state == "invalidated"
        assert any("invalidated" in p for p in C.preflight(plan))


class TestReview:
    def test_verdict_must_be_mechanical(self, plan: PlanController) -> None:
        with pytest.raises(PlanError, match="ship\\|revise\\|blocked"):
            C.record_review(plan, verdict="looks good to me", by="luke")

    def test_review_is_journaled_and_visible(self, plan: PlanController) -> None:
        C.record_review(plan, verdict="revise", detail="exam failed", by="luke")
        review = plan.state.last_review
        assert review is not None
        assert review["verdict"] == "revise"
        assert review["by"] == "luke"
        assert any("REVIEW  revise" in line for line in plan.status_lines())


class TestClosure:
    def _approvable(self, plan: PlanController) -> None:
        aid = C.request_approval(plan, "approve plan")
        C.grant_approval(plan, aid, by="luke", plan_level=True)

    def test_closure_refuses_pending_actions(self, plan: PlanController) -> None:
        self._approvable(plan)
        C.queue_action(plan, "a1")
        with pytest.raises(PlanNotClosable, match="1 pending action"):
            C.close_plan(plan, by="luke")

    def test_closure_refuses_pending_approvals(self, plan: PlanController) -> None:
        self._approvable(plan)
        C.request_approval(plan, "one more thing")
        with pytest.raises(PlanNotClosable, match="1 pending approval"):
            C.close_plan(plan, by="luke")

    def test_closure_refuses_an_unapproved_plan(self, plan: PlanController) -> None:
        with pytest.raises(PlanNotClosable, match="must approve the plan before closure"):
            C.close_plan(plan, by="luke")

    @pytest.mark.parametrize("verdict", ["revise", "blocked"])
    def test_closure_refuses_a_non_shipping_latest_review(
        self, plan: PlanController, verdict: str
    ) -> None:
        self._approvable(plan)
        C.record_review(plan, verdict=verdict, detail="verification did not clear", by="reviewer")
        with pytest.raises(PlanNotClosable, match=f"latest review is {verdict!r}"):
            C.close_plan(plan, by="luke")
        assert plan.state.status != "closed"

    def test_a_shipping_review_after_blocked_review_allows_closure(
        self, plan: PlanController
    ) -> None:
        self._approvable(plan)
        C.record_review(plan, verdict="blocked", detail="first verification failed", by="reviewer")
        C.record_review(plan, verdict="ship", detail="remediation verified", by="reviewer")
        assert C.close_plan(plan, by="luke")["status"] == "closed"

    def test_closure_succeeds_when_clean(self, plan: PlanController) -> None:
        self._approvable(plan)
        C.queue_action(plan, "a1")
        C.apply_action(plan, "a1", "done")
        outcome = C.close_plan(plan, by="luke")
        assert outcome["status"] == "closed"
        assert plan.state.status == "closed"

    def test_double_close_refuses(self, plan: PlanController) -> None:
        self._approvable(plan)
        C.close_plan(plan, by="luke")
        with pytest.raises(PlanNotClosable, match="already closed"):
            C.close_plan(plan, by="luke")


class TestRestartResumesFromStoredState:
    def test_fresh_instance_reports_identical_status(
        self, root: Path, plan: PlanController
    ) -> None:
        aid = C.request_approval(plan, "run exams", "consequential")
        C.grant_approval(plan, aid, by="luke")
        C.queue_action(plan, "a1")
        C.charge_budget(plan, "floors", 1)
        before = plan.status_snapshot()
        lines_before = plan.status_lines()

        fresh = PlanController.load(root, "p1")
        assert fresh.status_snapshot() == before
        assert fresh.status_lines() == lines_before

    def test_status_shows_pending_work_approval_and_budget(
        self, root: Path, plan: PlanController
    ) -> None:
        C.request_approval(plan, "run exams", "consequential")
        C.queue_action(plan, "a1")
        lines = PlanController.load(root, "p1").status_lines()
        text = "\n".join(lines)
        assert "PENDING  1 action(s): a1" in text
        assert "AWAITING_APPROVAL" in text
        assert "floors 0/2" in text


class TestAdaptersKeepEntryPointsDistinct:
    def test_loop_and_machine_plans_are_isolated(self, root: Path) -> None:
        loop = C.for_loop(root, "loop-1")
        machine = C.for_machine(root, "run-1")
        assert isinstance(loop, PlanAdapter)
        assert loop.controller.plan_id != machine.controller.plan_id
        C.queue_action(loop.controller, "loop-action")
        assert machine.controller.state.pending_action_ids == []

    def test_rebinding_loads_the_same_plan(self, root: Path) -> None:
        first = C.for_loop(root, "loop-1")
        C.queue_action(first.controller, "a1")
        second = C.for_loop(root, "loop-1")
        assert second.controller.state.pending_action_ids == ["a1"]
        assert second.controller.state.plan_revision == first.controller.state.plan_revision

    def test_adapter_shares_preflight_review_closure(self, root: Path) -> None:
        adapter = C.for_plan(root, "p9", verifier="pytest -q")
        assert adapter.preflight() == []
        aid = adapter.request_approval("consequential work")
        with pytest.raises(ApprovalRequired):
            adapter.require_approval(aid)
        assert adapter.status_lines()[0] == "VIA  plan adapter"
        assert any("VERIFIER  pytest -q" in line for line in adapter.status_lines())

    def test_adapter_close_delegates(self, root: Path) -> None:
        adapter = C.for_machine(root, "run-2")
        aid = adapter.request_approval("approve plan")
        C.grant_approval(adapter.controller, aid, by="luke", plan_level=True)
        assert adapter.close(by="luke")["status"] == "closed"


# ── knowledge through the controller ────────────────────────────────────
# The unified service for the best / battery / claude / exam flows: every
# consultation is budget-charged against the plan, accounting persists
# across fresh stores, answers cite recorded receipts, and answers without
# a receipt are refused.


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    def __init__(self, text: str = "chapter body") -> None:
        self._text = text

    def get(self, url: str, **kwargs) -> _FakeResponse:
        return _FakeResponse(self._text)


def _paths(tmp_path: Path):
    from awino.paths import AwinoPaths

    return AwinoPaths(root=tmp_path)


class TestConsultKnowledge:
    QUESTION = "what is a harness?"
    CHAPTER = "chapters/6-harnesses/1-what-is-a-harness.md"

    def test_consult_records_receipt_and_charges_budget(self, root: Path, tmp_path: Path) -> None:
        import hashlib as _hashlib

        from awino.knowledge import KnowledgeReceiptRequired as _KRR

        adapter = C.for_plan(root, "kp", budgets={"knowledge_files": 2})
        out = C.consult_knowledge(
            adapter.controller,
            _paths(tmp_path),
            self.QUESTION,
            "book",
            self.CHAPTER,
            by="luke",
            client=_FakeClient(),
        )
        assert out["replay"] is False
        assert out["sha"] == _hashlib.sha256(b"chapter body").hexdigest()[:12]
        cited = C.answer_from_knowledge(adapter.controller, self.QUESTION)
        assert cited["sha"] == out["sha"]
        assert cited["path"] == self.CHAPTER
        assert adapter.controller.state.budget_used["knowledge_files"] == 1
        # The receipt outlives the controller instance: stored facts.
        fresh = PlanController.load(root, "kp")
        assert C.answer_from_knowledge(fresh, self.QUESTION)["sha"] == out["sha"]
        assert _KRR is not None  # the refusal type is importable

    def test_second_consult_replays_without_recharging(self, root: Path, tmp_path: Path) -> None:
        adapter = C.for_plan(root, "kp", budgets={"knowledge_files": 2})
        first = C.consult_knowledge(
            adapter.controller,
            _paths(tmp_path),
            self.QUESTION,
            "book",
            self.CHAPTER,
            client=_FakeClient(),
        )
        second = C.consult_knowledge(
            adapter.controller,
            _paths(tmp_path),
            self.QUESTION,
            "book",
            self.CHAPTER,
            client=_FakeClient("different body"),
        )
        assert second["replay"] is True
        assert second["sha"] == first["sha"]
        assert adapter.controller.state.budget_used["knowledge_files"] == 1

    def test_answer_without_receipt_refuses(self, root: Path) -> None:
        from awino.knowledge import KnowledgeReceiptRequired

        adapter = C.for_plan(root, "kp2")
        with pytest.raises(KnowledgeReceiptRequired):
            C.answer_from_knowledge(adapter.controller, "a question never asked")

    def test_exhausted_knowledge_budget_refuses(self, root: Path, tmp_path: Path) -> None:
        adapter = C.for_plan(root, "kp3", budgets={"knowledge_files": 1})
        C.consult_knowledge(
            adapter.controller,
            _paths(tmp_path),
            "question one",
            "book",
            self.CHAPTER,
            client=_FakeClient(),
        )
        with pytest.raises(PlanBudgetExhausted):
            C.consult_knowledge(
                adapter.controller,
                _paths(tmp_path),
                "question two",
                "book",
                self.CHAPTER,
                client=_FakeClient(),
            )

    def test_accounting_survives_a_fresh_store(self, root: Path, tmp_path: Path) -> None:
        # Two consults share the plan's accounting key: the second store
        # resumes the first store's consumption instead of resetting it.
        from awino.knowledge import KnowledgeStore

        paths = _paths(tmp_path)
        first = KnowledgeStore(paths, budget=5, accounting_key="plan-kp4")
        first._charge("book:a.md")
        second = KnowledgeStore(paths, budget=5, accounting_key="plan-kp4")
        assert second.opened == 1
