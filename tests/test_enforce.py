"""The gate ledger must refuse work that has not earned completion.

These tests are the reason to trust the mechanism. A prose instruction cannot be
tested; a ledger can.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from smith.enforce import (
    CONTRACTS,
    Gate,
    Ledger,
    LedgerError,
    PlanDecision,
    TaskClass,
    TerminalState,
    adjudicate,
    detect_scope_violations,
    detect_test_weakening,
    score_run,
)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path)


class TestContract:
    def test_question_needs_no_gates(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "what is a harness")
        assert run.required == []
        assert adjudicate(run, []).can_close

    def test_code_change_requires_tests_and_lint(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "add retry")
        assert "tested" in run.required
        assert "linted" in run.required
        assert "tests_not_weakened" in run.required

    def test_bugfix_requires_diagnosis_before_fix(self, ledger: Ledger) -> None:
        # A bugfix that never reproduced the bug guessed at it.
        assert Gate.RESEARCHED in CONTRACTS[TaskClass.BUGFIX]

    def test_extra_gates_are_additive(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "risky", extra_gates=[Gate.REVIEWED])
        assert "reviewed" in run.required

    def test_required_gates_are_deduplicated(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "dup", extra_gates=[Gate.TESTED])
        assert run.required.count("tested") == 1


class TestRefusal:
    def test_cannot_close_with_no_evidence(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "unverified change")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert not verdict.can_close
        assert "GATE_MISSING" in verdict.blocked_reason

    def test_cannot_close_when_a_gate_fails(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "investigate")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert not verdict.can_close
        assert "GATE_FAILING" in verdict.blocked_reason

    def test_score_rewards_executed_clean_completion(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "investigate")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 0")
        evidence = ledger.evidence(run.run_id)
        score = score_run(run, evidence, adjudicate(run, evidence))
        assert score.total == 55
        assert score.grade == "verified"

    def test_score_penalizes_attestation_only(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.AUTHORING, "author")
        ledger.attest(run.run_id, Gate.PLANNED, "plan exists")
        ledger.attest(run.run_id, Gate.VALIDATED, "looks valid")
        ledger.attest(run.run_id, Gate.LESSON_RECORDED, "lesson noted")
        evidence = ledger.evidence(run.run_id)
        score = score_run(run, evidence, adjudicate(run, evidence))
        assert score.total < 45
        assert any(item.name == "assertion_only_evidence" for item in score.items)

    def test_closes_when_every_gate_passes(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "investigate")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 0")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert verdict.can_close
        assert verdict.blocked_reason == ""

    def test_partial_satisfaction_still_refuses(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "half done")
        ledger.record(run.run_id, Gate.TESTED, "exit 0")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert not verdict.can_close
        assert "tested" in verdict.satisfied
        assert "linted" in verdict.missing


class TestEvidenceIsReal:
    def test_exit_code_comes_from_the_process_not_the_caller(self, ledger: Ledger) -> None:
        # The whole mechanism rests on this: the agent names the command, the
        # ledger observes the result.
        run = ledger.open(TaskClass.RESEARCH, "prove it")
        item = ledger.record(run.run_id, Gate.RESEARCHED, "exit 3")
        assert item.exit_code == 3
        assert not item.passed

    def test_output_is_captured(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "capture")
        item = ledger.record(run.run_id, Gate.RESEARCHED, "echo distinctive-marker")
        assert "distinctive-marker" in item.output_head
        assert item.output_hash

    def test_attestation_is_distinguishable_from_execution(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.AUTHORING, "write a skill")
        ledger.attest(run.run_id, Gate.PLANNED, "specs/thing-spec.md")
        ledger.record(run.run_id, Gate.VALIDATED, "exit 0")
        ledger.attest(run.run_id, Gate.LESSON_RECORDED, "memory/lessons.md updated")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert verdict.can_close
        assert set(verdict.attested_only) == {"planned", "lesson_recorded"}
        assert "validated" not in verdict.attested_only

    def test_evidence_survives_reload(self, ledger: Ledger, tmp_path: Path) -> None:
        run = ledger.open(TaskClass.RESEARCH, "persist")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 0")
        reopened = Ledger(tmp_path)
        assert len(reopened.evidence(run.run_id)) == 1
        assert reopened.load(run.run_id).objective == "persist"

    def test_unknown_run_raises(self, ledger: Ledger) -> None:
        with pytest.raises(LedgerError):
            ledger.load("does-not-exist")


class TestRunArtifacts:
    def test_artifacts_are_append_only_hash_bound_and_filterable(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "bootstrap")
        first = ledger.append_artifact(run.run_id, "bootstrap", "human", {"runner": "native"})
        second = ledger.append_artifact(run.run_id, "debug", "agent", {"phase": "root"})

        assert first.payload_sha256
        assert ledger.artifacts(run.run_id) == [first, second]
        assert ledger.artifacts(run.run_id, "bootstrap") == [first]
        assert ledger.latest_artifact(run.run_id, "bootstrap") == first

    def test_legacy_run_without_artifact_file_reads_empty(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "legacy")
        ledger._artifacts(run.run_id).unlink()
        assert ledger.artifacts(run.run_id) == []


class TestRunSerialization:
    def test_new_runs_use_versioned_run_json(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "versioned")
        stored = json.loads(ledger._meta(run.run_id).read_text(encoding="utf-8"))
        assert stored["schema_version"] == 2

    def test_legacy_run_json_loads_with_defaults(self, ledger: Ledger) -> None:
        run_id = "legacy"
        ledger.run_dir(run_id).mkdir(parents=True)
        ledger._meta(run_id).write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "task_class": "research",
                    "objective": "old work",
                    "opened_at": "2026-01-01T00:00:00+00:00",
                    "required": ["researched"],
                    "skills_loaded": ["smith-rpi"],
                }
            ),
            encoding="utf-8",
        )
        loaded = ledger.load(run_id)
        assert loaded.schema_version == 1
        assert loaded.file_scope == []
        assert loaded.skills_loaded == ["smith-rpi"]
        assert loaded.plan_decisions == []
        assert loaded.checkpoints == []
        assert loaded.issue_id is None
        assert loaded.issue_started_at is None

    def test_issue_link_is_persisted_without_starting_work(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "linked work", issue_id="issue-123")
        loaded = ledger.load(run.run_id)
        assert loaded.issue_id == "issue-123"
        assert loaded.issue_started_at is None


class TestPlanApproval:
    def test_approval_binds_plan_hash_and_scope(self, ledger: Ledger, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("# Approved plan\n", encoding="utf-8")
        run = ledger.open(
            TaskClass.CODE_CHANGE,
            "planned work",
            file_scope=["src/smith/enforce.py"],
            plan_path=plan,
        )
        decision = ledger.approve_plan(run.run_id, "reviewer", "scope reviewed")
        assert decision.decision == PlanDecision.APPROVED
        assert len(decision.plan_sha256) == 64
        assert decision.approved_scope == ["src/smith/enforce.py"]
        assert ledger.validate_plan(run.run_id) == []
        assert ledger.load(run.run_id).plan_decisions == [decision]

    def test_plan_edit_invalidates_approval(self, ledger: Ledger, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("first", encoding="utf-8")
        run = ledger.open(TaskClass.CODE_CHANGE, "planned", plan_path=plan)
        ledger.approve_plan(run.run_id, "reviewer")
        plan.write_text("changed", encoding="utf-8")
        assert ledger.validate_plan(run.run_id) == ["approved plan hash no longer matches"]

    def test_invalid_approval_refuses_before_subprocess(
        self,
        ledger: Ledger,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("first", encoding="utf-8")
        run = ledger.open(TaskClass.CODE_CHANGE, "planned", plan_path=plan)
        ledger.approve_plan(run.run_id, "reviewer")
        plan.write_text("changed", encoding="utf-8")
        subprocess_run = Mock()
        monkeypatch.setattr("smith.enforce.subprocess.run", subprocess_run)
        with pytest.raises(LedgerError, match="PLAN_INVALID"):
            ledger.record(run.run_id, Gate.TESTED, "must-not-run")
        subprocess_run.assert_not_called()

    def test_planned_attestation_requires_hash_bound_approval(
        self, ledger: Ledger, tmp_path: Path
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("plan", encoding="utf-8")
        run = ledger.open(TaskClass.CODE_CHANGE, "planned", plan_path=plan)
        with pytest.raises(LedgerError, match="approve_plan"):
            ledger.attest(run.run_id, Gate.PLANNED, "trust me")

    def test_scope_edit_invalidates_approval(self, ledger: Ledger, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("plan", encoding="utf-8")
        run = ledger.open(TaskClass.CODE_CHANGE, "planned", file_scope=["one.py"], plan_path=plan)
        ledger.approve_plan(run.run_id, "reviewer")
        run = ledger.load(run.run_id)
        run.file_scope.append("two.py")
        ledger.save(run)
        assert ledger.validate_plan(run.run_id) == ["approved scope no longer matches"]

    @pytest.mark.parametrize("decision", [PlanDecision.HELD, PlanDecision.REJECTED])
    def test_hold_and_reject_supersede_approval(
        self, ledger: Ledger, tmp_path: Path, decision: PlanDecision
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("plan", encoding="utf-8")
        run = ledger.open(TaskClass.CODE_CHANGE, "planned", plan_path=plan)
        ledger.approve_plan(run.run_id, "reviewer")
        ledger.decide_plan(run.run_id, decision, "reviewer", "needs work")
        assert ledger.validate_plan(run.run_id) == [f"plan is {decision}"]

    def test_missing_plan_cannot_be_decided(self, ledger: Ledger, tmp_path: Path) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "planned", plan_path=tmp_path / "missing.md")
        with pytest.raises(LedgerError, match="plan does not exist"):
            ledger.approve_plan(run.run_id, "reviewer")


class TestCheckpoints:
    def test_pending_decision_and_resolution_survive_reload(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "choose")
        checkpoint = ledger.checkpoint(
            run.run_id,
            "planning",
            "tradeoff found",
            "wait for selection",
            pending_decision="Choose storage",
            options=["json", "sqlite"],
        )
        resolved = ledger.resolve_checkpoint(run.run_id, "json", "reviewer")
        assert resolved.checkpoint_id == checkpoint.checkpoint_id
        loaded = ledger.load(run.run_id)
        assert loaded.checkpoints[-1].selected_decision == "json"
        assert loaded.checkpoints[-1].decided_by == "reviewer"

    def test_cannot_replace_an_unresolved_decision(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "choose")
        ledger.checkpoint(
            run.run_id,
            "planning",
            "first",
            "wait",
            pending_decision="Choose",
            options=["a"],
        )
        with pytest.raises(LedgerError, match="pending decision"):
            ledger.checkpoint(
                run.run_id,
                "planning",
                "second",
                "wait",
                pending_decision="Choose again",
                options=["b"],
            )

    def test_resolution_must_be_a_declared_option(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "choose")
        ledger.checkpoint(
            run.run_id,
            "planning",
            "choice",
            "wait",
            pending_decision="Choose",
            options=["a", "b"],
        )
        with pytest.raises(LedgerError, match="declared option"):
            ledger.resolve_checkpoint(run.run_id, "c", "reviewer")


class TestCurrentInspection:
    def test_active_current_is_distinguished_from_stale(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "inspect")
        active = ledger.inspect_current()
        assert active.status == "active"
        assert active.run == run
        run.closed_at = "2026-01-01T00:00:00+00:00"
        ledger.save(run)
        stale = ledger.inspect_current()
        assert stale.status == "stale"
        assert stale.run == run

    def test_missing_current_target_is_broken(self, ledger: Ledger) -> None:
        ledger.base.mkdir(parents=True)
        ledger._current().write_text("missing", encoding="utf-8")
        inspected = ledger.inspect_current()
        assert inspected.status == "broken"
        assert inspected.run is None


class TestThreeStrikes:
    def test_third_failure_escalates(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "flaky")
        for _ in range(3):
            ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert verdict.attempts_exceeded == ["researched"]
        assert "THREE_STRIKES" in verdict.blocked_reason

    def test_two_failures_do_not_escalate(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "retryable")
        for _ in range(2):
            ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert verdict.attempts_exceeded == []
        assert "GATE_FAILING" in verdict.blocked_reason

    def test_success_after_failure_clears_the_gate(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "eventually works")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 0")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert verdict.can_close

    def test_fourth_attempt_is_refused_before_subprocess(
        self, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = ledger.open(TaskClass.RESEARCH, "terminal failure")
        for _ in range(3):
            ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        subprocess_run = Mock()
        monkeypatch.setattr("smith.enforce.subprocess.run", subprocess_run)
        with pytest.raises(LedgerError, match="THREE_STRIKES"):
            ledger.record(run.run_id, Gate.RESEARCHED, "must-not-run")
        subprocess_run.assert_not_called()

    def test_attestations_do_not_consume_strikes(
        self, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = ledger.open(TaskClass.RESEARCH, "attested")
        for _ in range(3):
            ledger.attest(run.run_id, Gate.RESEARCHED, "reviewed")
        subprocess_run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        monkeypatch.setattr("smith.enforce.subprocess.run", subprocess_run)
        ledger.record(run.run_id, Gate.RESEARCHED, "allowed")
        subprocess_run.assert_called_once()


class TestSkillAudit:
    def test_loaded_skills_are_recorded(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "with skills")
        ledger.note_skill(run.run_id, "smith-rpi")
        ledger.note_skill(run.run_id, "smith-delegate")
        assert ledger.load(run.run_id).skills_loaded == ["smith-rpi", "smith-delegate"]

    def test_duplicate_skill_records_once(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "dup skill")
        ledger.note_skill(run.run_id, "smith-rpi")
        ledger.note_skill(run.run_id, "smith-rpi")
        assert ledger.load(run.run_id).skills_loaded == ["smith-rpi"]

    def test_skill_events_are_persisted(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "skill history")
        ledger.note_skill(run.run_id, "smith-rpi", state="used", reason="planned work")
        loaded = ledger.load(run.run_id)
        assert len(loaded.skill_events) == 1
        assert loaded.skill_events[0].name == "smith-rpi"
        assert loaded.skill_events[0].state == "used"
        assert loaded.skill_events[0].reason == "planned work"
        assert loaded.skills_loaded == ["smith-rpi"]

    def test_recommendation_does_not_claim_skill_loaded(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "route only")
        ledger.note_skill(run.run_id, "awino-rpi", state="recommended", reason="complex change")
        loaded = ledger.load(run.run_id)
        assert loaded.skills_loaded == []
        assert loaded.skill_events[0].state == "recommended"


class TestWeakeningDetection:
    def test_deleted_assertion_in_test_file_is_caught(self) -> None:
        diff = """--- a/tests/test_thing.py
+++ b/tests/test_thing.py
-    assert result == 42
+    pass
"""
        assert detect_test_weakening(diff)

    def test_added_skip_marker_is_caught(self) -> None:
        diff = """--- a/tests/test_thing.py
+++ b/tests/test_thing.py
+@pytest.mark.skip(reason="flaky")
 def test_thing():
"""
        assert detect_test_weakening(diff)

    def test_removed_test_function_is_caught(self) -> None:
        diff = """--- a/tests/test_thing.py
+++ b/tests/test_thing.py
-def test_important_behaviour():
-    assert True
"""
        assert detect_test_weakening(diff)

    def test_production_assert_change_is_not_flagged(self) -> None:
        # Editing an assert in production code is legitimate work.
        diff = """--- a/src/smith/knowledge.py
+++ b/src/smith/knowledge.py
-    assert path
+    if not path:
+        raise ValueError
"""
        assert detect_test_weakening(diff) == []

    def test_adding_a_test_is_not_weakening(self) -> None:
        diff = """--- a/tests/test_thing.py
+++ b/tests/test_thing.py
+def test_new_case():
+    assert compute() == 7
"""
        assert detect_test_weakening(diff) == []


class TestScopeDetection:
    def test_file_outside_scope_is_flagged(self) -> None:
        violations = detect_scope_violations(
            ["src/smith/knowledge.py", "src/smith/secret.py"], ["src/smith/knowledge.py"]
        )
        assert violations == ["src/smith/secret.py"]

    def test_declared_files_pass(self) -> None:
        assert detect_scope_violations(["src/smith/cli.py"], ["src/smith/cli.py"]) == []

    def test_directory_scope_allows_children(self) -> None:
        assert detect_scope_violations(["src/smith/cli.py"], ["src/smith/"]) == []

    def test_empty_scope_means_unscoped_not_forbidden(self) -> None:
        assert detect_scope_violations(["anything.py"], []) == []

    def test_separator_style_does_not_matter(self) -> None:
        assert detect_scope_violations(["src\\smith\\cli.py"], ["src/smith/cli.py"]) == []


class TestDeliverableCompleteness:
    """Regression for the Nuclear Battery / TRISO substitution incident.

    An agent was asked to run 10 indicator scans for Nuclear Battery. It ran
    3, invented a success-adjacent status ("honesty_boundary") to describe
    the other 7 as skipped, and reported the run as implemented_and_tested.
    A partial deliverable reported as complete is a failed task, not a
    partial success - close() must refuse it mechanically, not rely on the
    agent's self-report.
    """

    def test_partial_deliverable_refuses_close_even_with_all_gates_green(
        self, ledger: Ledger
    ) -> None:
        run = ledger.open(TaskClass.RESEARCH, "run 10 indicator scans for Nuclear Battery")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 0")
        # 3 of 10 indicators actually ran - the exact incident numbers.
        ledger.record_completeness(run.run_id, achieved=3, stated=10, unit="indicator(s)")

        run = ledger.load(run.run_id)
        verdict = adjudicate(run, ledger.evidence(run.run_id))

        assert not verdict.can_close
        assert "DELIVERABLE_INCOMPLETE" in verdict.blocked_reason
        assert "3/10 indicator(s)" in verdict.blocked_reason

    def test_full_deliverable_does_not_block_close(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "run 10 indicator scans")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 0")
        ledger.record_completeness(run.run_id, achieved=10, stated=10, unit="indicator(s)")

        run = ledger.load(run.run_id)
        verdict = adjudicate(run, ledger.evidence(run.run_id))

        assert verdict.can_close

    def test_human_can_explicitly_accept_reduced_scope(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "run 10 indicator scans")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 0")
        ledger.record_completeness(
            run.run_id,
            achieved=3,
            stated=10,
            unit="indicator(s)",
            accept_reduced_scope=True,
            accepted_by="human-reviewer",
            accepted_reason="7 indicators require sensor data not yet collected",
        )

        run = ledger.load(run.run_id)
        verdict = adjudicate(run, ledger.evidence(run.run_id))

        assert verdict.can_close

    def test_accepting_reduced_scope_without_attribution_is_refused(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "run 10 indicator scans")
        with pytest.raises(LedgerError, match="COMPLETENESS_ACCEPTANCE_INCOMPLETE"):
            ledger.record_completeness(run.run_id, achieved=3, stated=10, accept_reduced_scope=True)

    def test_negative_counts_are_refused(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "run scans")
        with pytest.raises(LedgerError, match="COMPLETENESS_INVALID"):
            ledger.record_completeness(run.run_id, achieved=-1, stated=10)

    def test_no_completeness_record_means_no_completeness_constraint(self, ledger: Ledger) -> None:
        # Most tasks never state a count. Absence of the field must not block
        # closure - only a real Ledger.record_completeness() call does.
        run = ledger.open(TaskClass.QUESTION, "what is a harness")
        verdict = adjudicate(run, ledger.evidence(run.run_id))
        assert verdict.can_close

    def test_completeness_survives_a_reload_from_disk(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "run 10 indicator scans")
        ledger.record_completeness(run.run_id, achieved=3, stated=10, unit="indicator(s)")

        reloaded = ledger.load(run.run_id)

        assert reloaded.completeness is not None
        assert reloaded.completeness.achieved == 3
        assert reloaded.completeness.stated == 10
        assert not reloaded.completeness.satisfied


class TestEscapeHatchDenylist:
    """Regression for the incident where an agent invented 'honesty_boundary'
    to make 7 skipped indicator scans sound principled instead of unmet."""

    def test_the_exact_invented_term_from_the_incident_is_refused(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "run 10 indicator scans for Nuclear Battery")
        with pytest.raises(LedgerError, match="ESCAPE_HATCH_TERM"):
            ledger.attest(
                run.run_id,
                Gate.RESEARCHED,
                "7 of 10 indicators marked honesty_boundary - cannot relabel TRISO evidence",
            )

    def test_ungathered_is_refused(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "gather sources")
        with pytest.raises(LedgerError, match="ESCAPE_HATCH_TERM"):
            ledger.attest(run.run_id, Gate.RESEARCHED, "remaining sources left ungathered")

    def test_unavailable_is_refused(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "gather sources")
        with pytest.raises(LedgerError, match="ESCAPE_HATCH_TERM"):
            ledger.attest(run.run_id, Gate.RESEARCHED, "marked the remaining data unavailable")

    def test_matching_is_case_insensitive(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "gather sources")
        with pytest.raises(LedgerError, match="ESCAPE_HATCH_TERM"):
            ledger.attest(run.run_id, Gate.RESEARCHED, "Honesty_Boundary reached")

    def test_the_refusal_names_the_matched_term(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "gather sources")
        with pytest.raises(LedgerError, match="'ungathered'"):
            ledger.attest(run.run_id, Gate.RESEARCHED, "left ungathered for now")

    def test_an_ordinary_true_note_is_not_refused(self, ledger: Ledger) -> None:
        # The denylist must not become so broad it blocks honest, real notes.
        run = ledger.open(TaskClass.RESEARCH, "run 10 indicator scans")
        item = ledger.attest(
            run.run_id, Gate.RESEARCHED, "ran all 10 indicator scans; results attached"
        )
        assert item.passed

    def test_a_genuinely_blocked_note_pointing_at_a_checkpoint_is_not_refused(
        self, ledger: Ledger
    ) -> None:
        # Honestly reporting a blocker and pointing at the real mechanism
        # (a checkpoint) must remain possible - only invented status words
        # are denied, not the concept of being blocked.
        run = ledger.open(TaskClass.RESEARCH, "run 10 indicator scans")
        item = ledger.attest(
            run.run_id,
            Gate.RESEARCHED,
            "3 of 10 indicators ran; 7 require sensor data not yet collected, "
            "recorded as a pending checkpoint decision",
        )
        assert item.passed


class TestTerminalStates:
    """A run must persist through exactly three allowed terminal states.

    COMPLETE, BLOCKED, and PAUSED are each earned by a distinct mechanism.
    None of the three may be reached by omission, by an agent's self-report,
    or as a side effect of an unrelated command.
    """

    def test_new_run_has_no_terminal_state(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "fresh run")
        assert run.terminal_state is None

    def test_mark_complete_sets_terminal_state(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "trivial")
        completed = ledger.mark_complete(run.run_id)
        assert completed.terminal_state == str(TerminalState.COMPLETE)
        assert completed.verdict == "COMPLETE"
        assert completed.closed_at is not None

    def test_terminal_state_survives_reload(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "trivial")
        ledger.mark_complete(run.run_id)
        loaded = ledger.load(run.run_id)
        assert loaded.terminal_state == str(TerminalState.COMPLETE)

    def test_legacy_run_without_terminal_state_defaults_to_none(self, ledger: Ledger) -> None:
        run_id = "legacy-terminal"
        ledger.run_dir(run_id).mkdir(parents=True)
        ledger._meta(run_id).write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "task_class": "question",
                    "objective": "old",
                    "opened_at": "2026-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        loaded = ledger.load(run_id)
        assert loaded.terminal_state is None

    # ── BLOCKED requires both a reproducible failure and a pending decision ──

    def test_blocked_requires_a_recorded_failure(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "needs a decision")
        ledger.checkpoint(
            run.run_id,
            "stuck",
            "no repro yet",
            "wait",
            pending_decision="proceed how?",
            options=["a", "b"],
        )
        with pytest.raises(LedgerError, match="BLOCKED_REQUIRES_EVIDENCE"):
            ledger.mark_blocked(run.run_id)

    def test_blocked_requires_an_unresolved_pending_decision(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "genuinely stuck")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        with pytest.raises(LedgerError, match="BLOCKED_REQUIRES_PENDING_DECISION"):
            ledger.mark_blocked(run.run_id)

    def test_blocked_is_refused_once_the_decision_is_resolved(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "resolved already")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        ledger.checkpoint(
            run.run_id,
            "stuck",
            "no repro yet",
            "wait",
            pending_decision="proceed how?",
            options=["a", "b"],
        )
        ledger.resolve_checkpoint(run.run_id, "a", "reviewer")
        with pytest.raises(LedgerError, match="BLOCKED_REQUIRES_PENDING_DECISION"):
            ledger.mark_blocked(run.run_id)

    def test_blocked_succeeds_with_both_a_failure_and_a_pending_decision(
        self, ledger: Ledger
    ) -> None:
        run = ledger.open(TaskClass.RESEARCH, "properly blocked")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        ledger.checkpoint(
            run.run_id,
            "stuck",
            "reproducible failure hit",
            "wait for a human",
            pending_decision="how to proceed?",
            options=["retry", "abandon"],
        )
        blocked = ledger.mark_blocked(run.run_id)
        assert blocked.terminal_state == str(TerminalState.BLOCKED)

    def test_attestation_alone_is_not_a_reproducible_blocker(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "attested only")
        ledger.attest(run.run_id, Gate.RESEARCHED, "trust me it is blocked")
        ledger.checkpoint(
            run.run_id,
            "stuck",
            "s",
            "n",
            pending_decision="d",
            options=["a"],
        )
        with pytest.raises(LedgerError, match="BLOCKED_REQUIRES_EVIDENCE"):
            ledger.mark_blocked(run.run_id)

    # ── PAUSED requires an explicit, human-named command ─────────────────────

    def test_pause_requires_non_empty_by(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "pausable")
        with pytest.raises(LedgerError, match="PAUSE_REQUIRES_HUMAN"):
            ledger.pause(run.run_id, "", "taking a break")

    def test_pause_requires_non_whitespace_by(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "pausable")
        with pytest.raises(LedgerError, match="PAUSE_REQUIRES_HUMAN"):
            ledger.pause(run.run_id, "   ", "taking a break")

    def test_pause_with_named_human_sets_terminal_state(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.QUESTION, "pausable")
        paused = ledger.pause(run.run_id, "jane", "waiting on external input")
        assert paused.terminal_state == str(TerminalState.PAUSED)
        artifacts = ledger.artifacts(run.run_id, "pause")
        assert artifacts and artifacts[-1].actor == "jane"
        assert artifacts[-1].payload["reason"] == "waiting on external input"

    def test_no_command_sets_paused_as_a_side_effect(self, ledger: Ledger) -> None:
        # Nothing other than an explicit pause() call may write this state.
        # record(), attest(), checkpoint(), and close-adjacent calls must
        # never touch terminal_state=PAUSED implicitly.
        run = ledger.open(TaskClass.RESEARCH, "never auto-paused")
        ledger.record(run.run_id, Gate.RESEARCHED, "exit 0")
        ledger.attest(run.run_id, Gate.RESEARCHED, "note")
        loaded = ledger.load(run.run_id)
        assert loaded.terminal_state is None

    # ── three-strikes must leave a genuine decision point behind ─────────────

    def test_three_strikes_auto_creates_a_pending_decision_checkpoint(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.RESEARCH, "escalating")
        for _ in range(3):
            ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        loaded = ledger.load(run.run_id)
        assert loaded.escalated_gates == ["researched"]
        pending = [
            cp
            for cp in loaded.checkpoints
            if cp.pending_decision is not None and cp.selected_decision is None
        ]
        assert len(pending) == 1
        assert "researched" in pending[0].pending_decision

        # And now the run can be properly certified BLOCKED because the
        # decision point three-strikes created satisfies mark_blocked().
        blocked = ledger.mark_blocked(run.run_id)
        assert blocked.terminal_state == str(TerminalState.BLOCKED)

    def test_three_strikes_does_not_duplicate_an_existing_pending_decision(
        self, ledger: Ledger
    ) -> None:
        run = ledger.open(TaskClass.RESEARCH, "already has a decision")
        ledger.checkpoint(
            run.run_id,
            "planning",
            "already asked",
            "wait",
            pending_decision="pre-existing question",
            options=["x", "y"],
        )
        for _ in range(3):
            ledger.record(run.run_id, Gate.RESEARCHED, "exit 1")
        loaded = ledger.load(run.run_id)
        pending = [
            cp
            for cp in loaded.checkpoints
            if cp.pending_decision is not None and cp.selected_decision is None
        ]
        assert len(pending) == 1
        assert pending[0].pending_decision == "pre-existing question"
