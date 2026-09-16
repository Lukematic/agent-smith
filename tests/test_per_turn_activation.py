"""Phase 4: real per-turn host activation.

Each host adapter (Claude Code, Kilo, Roo) triggers the SAME Phase 2 plan
controller at session start, user turn, and tool-result boundaries. No
live host exists in this sandbox, so every journey here is driven by a
faithful double and labeled ``double_driven`` — never parity. These tests
pin:

- the full journey per host: session_start -> user_turn -> plan edit ->
  approval -> execution -> tool_result -> restart recovery, all recorded
  in the one controller journal;
- boundary redelivery is idempotent (same delivery id applies once);
- session start shows the controller's pending work (human-observable);
- restart resumes the same pending state and the host-activity trail;
- a detected host binary does NOT upgrade double-driven evidence to
  live (never claim parity from test doubles).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from awino import controller as C
from awino.hosts import Evidence, get_adapter, supported_hosts


@pytest.fixture(params=supported_hosts())
def host(request):
    return get_adapter(request.param)


@pytest.fixture()
def plan(tmp_path: Path, host):
    return C.for_plan(tmp_path, f"journey-{host.HOST}")


class TestPerTurnJourney:
    def test_full_coding_journey_through_one_controller(self, tmp_path, host, plan):
        session = "sess-1"

        # 1. session start shows pending work, nothing runs unattended.
        C.queue_action(plan.controller, "fix-login")
        started = host.session_start(
            plan, session, evidence=Evidence.DOUBLE_DRIVEN, detail="double journey"
        )
        assert any("PENDING" in line for line in started["status_lines"])
        assert any("fix-login" in line for line in started["status_lines"])

        # 2. user turn: the human's intent enters the controller.
        turn = host.user_turn(
            plan,
            session,
            "fix the login bug",
            delivery_id="turn-1",
            evidence=Evidence.DOUBLE_DRIVEN,
        )
        assert turn["plan_revision"] >= 1

        # 3. the human edits the plan: scope changes are journaled.
        plan.controller.submit_event(
            event_id="scope-edit-1",
            kind="scope_set",
            payload={"scope": ["fix the login bug, keep the session API stable"]},
        )

        # 4. consequential work asks first; the human grants at plan level.
        aid = C.request_approval(plan.controller, "run the fix", plan_level=True, by="human")
        granted = C.grant_approval(plan.controller, aid, by="human", plan_level=True)
        assert granted["approval_state"] == "approved"

        # 5. execution under the approved plan.
        C.apply_action(plan.controller, "fix-login", result="patched login.py")

        # 6. tool-result boundary: the tool call's completion is recorded.
        host.tool_result(
            plan,
            session,
            "Edit",
            "patched login.py",
            delivery_id="tool-1",
            evidence=Evidence.DOUBLE_DRIVEN,
        )

        # The journal is one trail: every boundary recorded under this host.
        kinds = [event.kind for event, _ in plan.controller._read_journal()]
        assert "host_session_started" in kinds
        assert "host_user_turn" in kinds
        assert "host_tool_result" in kinds
        assert kinds.count("host_user_turn") == 1

        # 7. restart: a fresh load resumes the same pending state.
        reloaded = C.PlanController.load(tmp_path, f"journey-{host.HOST}")
        assert reloaded.state.approval_state == "approved"
        assert reloaded.state.pending_action_ids == []
        activity = reloaded.state.host_activity
        assert [a["boundary"] for a in activity] == [
            "session_start",
            "user_turn",
            "tool_result",
        ]
        assert all(a["host"] == host.HOST for a in activity)
        assert all(a["evidence"] == Evidence.DOUBLE_DRIVEN for a in activity)
        assert all(a["session_id"] == session for a in activity)

        # 8. the host's own evidence file records the double-driven run.
        host.record_evidence(
            tmp_path,
            session_id=session,
            evidence=Evidence.DOUBLE_DRIVEN,
            boundaries=["session_start", "user_turn", "tool_result"],
            detail="faithful double journey",
        )
        records = host.read_evidence(tmp_path)
        assert len(records) == 1
        assert records[0]["evidence"] == Evidence.DOUBLE_DRIVEN

    def test_boundary_redelivery_applies_once(self, host, plan):
        before = plan.controller.event_count()
        first = host.user_turn(
            plan,
            "sess-2",
            "hello",
            delivery_id="dup-1",
            evidence=Evidence.DOUBLE_DRIVEN,
        )
        second = host.user_turn(
            plan,
            "sess-2",
            "hello but hostile and different",
            delivery_id="dup-1",
            evidence=Evidence.DOUBLE_DRIVEN,
        )
        assert second == first  # exact replay of the recorded outcome
        assert plan.controller.event_count() == before + 1

    def test_session_start_redelivery_applies_once(self, host, plan):
        before = plan.controller.event_count()
        host.session_start(plan, "sess-3", evidence=Evidence.DOUBLE_DRIVEN)
        host.session_start(plan, "sess-3", evidence=Evidence.DOUBLE_DRIVEN)
        assert plan.controller.event_count() == before + 1

    def test_invalid_evidence_label_is_rejected(self, host, plan):
        with pytest.raises(ValueError, match="evidence must be"):
            host.session_start(plan, "sess-4", evidence="definitely-live-trust-me")


class TestNoParityFromDoubles:
    def test_detected_binary_does_not_claim_live(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
        status = get_adapter("claude_code").status()
        assert status.detected_live is True
        # The binary is present but the API surface is unproven: still not
        # live, and a double-driven journey never upgrades it.
        assert status.evidence == Evidence.UNVERIFIED
        assert status.evidence != Evidence.LIVE

    def test_double_driven_journey_stays_double_driven(self, tmp_path, host, plan):
        host.session_start(plan, "sess-5", evidence=Evidence.DOUBLE_DRIVEN)
        host.record_evidence(
            tmp_path,
            session_id="sess-5",
            evidence=Evidence.DOUBLE_DRIVEN,
            boundaries=["session_start"],
        )
        assert host.status().evidence in (
            Evidence.UNVERIFIED,
            Evidence.DOUBLE_DRIVEN,
        )
        assert host.status().evidence != Evidence.LIVE
        assert host.read_evidence(tmp_path)[0]["evidence"] == Evidence.DOUBLE_DRIVEN


class TestUnsupportedHost:
    def test_deliberately_unsupported_host_yields_report(self):
        from awino.hosts import UnknownHost

        with pytest.raises(UnknownHost) as excinfo:
            get_adapter("clippy")
        message = str(excinfo.value)
        assert "clippy" in message
        assert "never a silent pass" in message
