"""The gate ledger must refuse work that has not earned completion.

These tests are the reason to trust the mechanism. A prose instruction cannot be
tested; a ledger can.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.enforce import (
    CONTRACTS,
    Gate,
    Ledger,
    LedgerError,
    TaskClass,
    adjudicate,
    detect_scope_violations,
    detect_test_weakening,
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
