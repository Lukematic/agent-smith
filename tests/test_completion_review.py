"""Unit tests for the ``gate review`` machinery in ``completion_review``.

Covers the ProvenanceRecord model (creation, serialization, backward
compatibility via ``Run.from_dict``), the acceptance-criteria extraction
heuristic, and the file_scope-vs-tidy-finding blocking classification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.completion_review import (
    TOOLCHAIN_GATES,
    AcceptanceCriterion,
    classify_tidy_findings,
    extract_acceptance_criteria,
)
from smith.enforce import Ledger, ProvenanceGateResult, ProvenanceRecord, Run, TaskClass
from smith.tidy import Clutter, Finding


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path)


class TestProvenanceRecordModel:
    def test_creation_carries_every_declared_field(self) -> None:
        record = ProvenanceRecord(
            issue_id="ISSUE-1",
            plan_sha256="abc123",
            run_id="run-1",
            gate_results=[ProvenanceGateResult(gate="tested", command="pytest -q", exit_code=0)],
            changed_files=["src/a.py", "src/b.py"],
            verdict="approved",
            risks="none known",
            recorded_at="2026-01-01T00:00:00+00:00",
        )
        assert record.issue_id == "ISSUE-1"
        assert record.plan_sha256 == "abc123"
        assert record.gate_results[0].exit_code == 0
        assert record.changed_files == ["src/a.py", "src/b.py"]
        assert record.verdict == "approved"

    def test_summary_reports_verdict_changed_files_and_risks(self) -> None:
        record = ProvenanceRecord(
            issue_id=None,
            plan_sha256=None,
            run_id="run-1",
            gate_results=[],
            changed_files=["a.py"],
            verdict="approved",
            risks="flaky test on CI",
            recorded_at="2026-01-01T00:00:00+00:00",
        )
        assert "review=approved" in record.summary
        assert "changed_files=1" in record.summary
        assert "flaky test on CI" in record.summary

    def test_summary_reports_no_risks_when_none_recorded(self) -> None:
        record = ProvenanceRecord(
            issue_id=None,
            plan_sha256=None,
            run_id="run-1",
            gate_results=[],
            changed_files=[],
            verdict="approved",
            risks=None,
            recorded_at="2026-01-01T00:00:00+00:00",
        )
        assert "risks=none recorded" in record.summary

    def test_run_round_trips_provenance_through_to_dict_and_from_dict(self, ledger: Ledger) -> None:
        run = ledger.open(TaskClass.CODE_CHANGE, "add retry logic")
        ledger.record_provenance(
            run.run_id,
            verdict="approved",  # type: ignore[arg-type]
            gate_results=[ProvenanceGateResult(gate="tested", command="pytest -q", exit_code=0)],
            changed_files=["src/retry.py"],
            risks="none",
        )
        reloaded = ledger.load(run.run_id)
        assert reloaded.provenance is not None
        assert reloaded.provenance.verdict == "approved"
        assert reloaded.provenance.changed_files == ["src/retry.py"]
        assert reloaded.provenance.gate_results[0].gate == "tested"
        # Round-trip through to_dict/from_dict directly, mirroring how the
        # ledger persists and reloads run.json.
        payload = reloaded.to_dict()
        again = Run.from_dict(payload)
        assert again.provenance is not None
        assert again.provenance.verdict == "approved"

    def test_run_from_dict_is_backward_compatible_with_no_provenance_field(self) -> None:
        """A run.json written before this feature existed has no 'provenance' key."""
        legacy = {
            "run_id": "run-old",
            "task_class": "code-change",
            "objective": "legacy run",
            "opened_at": "2025-01-01T00:00:00+00:00",
        }
        run = Run.from_dict(legacy)
        assert run.provenance is None


class TestAcceptanceCriteriaExtraction:
    def test_extracts_dash_bullets(self) -> None:
        text = "Ship the feature.\n- must log errors\n- must retry twice\n"
        criteria = extract_acceptance_criteria(text)
        assert [c.text for c in criteria] == ["must log errors", "must retry twice"]

    def test_extracts_numbered_list(self) -> None:
        text = "1. handle timeouts\n2. handle disconnects\n"
        criteria = extract_acceptance_criteria(text)
        assert [c.text for c in criteria] == ["handle timeouts", "handle disconnects"]

    def test_extracts_star_bullets(self) -> None:
        text = "* validate input\n* reject empty strings\n"
        criteria = extract_acceptance_criteria(text)
        assert len(criteria) == 2

    def test_returns_empty_when_no_bullets_present(self) -> None:
        text = "Add a retry to the HTTP client."
        criteria = extract_acceptance_criteria(text)
        assert criteria == []

    def test_returns_empty_for_empty_or_none_text(self) -> None:
        assert extract_acceptance_criteria("") == []
        assert extract_acceptance_criteria("", "") == []

    def test_merges_criteria_from_multiple_sources_deduplicated(self) -> None:
        objective = "- shared item\n- objective only\n"
        seed_description = "- shared item\n- seed only\n"
        criteria = extract_acceptance_criteria(objective, seed_description)
        texts = [c.text for c in criteria]
        assert texts.count("shared item") == 1
        assert "objective only" in texts
        assert "seed only" in texts

    def test_criterion_default_met_state_is_none(self) -> None:
        criterion = AcceptanceCriterion(text="must pass")
        assert criterion.met is None


class TestTidyFindingScopeClassification:
    def test_in_scope_clutter_is_blocking(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "generated.pyc"
        target.parent.mkdir(parents=True)
        target.write_text("junk", encoding="utf-8")
        clutter = Clutter(Finding.STRAY_ROOT_FILE, target, "looks disposable")

        classified = classify_tidy_findings([clutter], ["src/generated.pyc"], tmp_path)

        assert len(classified) == 1
        assert classified[0].in_scope is True
        assert classified[0].blocking is True

    def test_out_of_scope_clutter_is_warn_only(self, tmp_path: Path) -> None:
        target = tmp_path / "unrelated" / "stray.cache"
        target.parent.mkdir(parents=True)
        target.write_text("junk", encoding="utf-8")
        clutter = Clutter(Finding.STRAY_ROOT_FILE, target, "looks disposable")

        classified = classify_tidy_findings([clutter], ["src/generated.pyc"], tmp_path)

        assert len(classified) == 1
        assert classified[0].in_scope is False
        assert classified[0].blocking is False

    def test_scope_directory_prefix_matches(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "nested" / "cache.tmp"
        target.parent.mkdir(parents=True)
        target.write_text("junk", encoding="utf-8")
        clutter = Clutter(Finding.STRAY_ROOT_FILE, target, "looks disposable")

        classified = classify_tidy_findings([clutter], ["src/*"], tmp_path)

        assert classified[0].in_scope is True

    def test_empty_file_scope_means_nothing_is_in_scope(self, tmp_path: Path) -> None:
        target = tmp_path / "anything.tmp"
        target.write_text("junk", encoding="utf-8")
        clutter = Clutter(Finding.STRAY_ROOT_FILE, target, "looks disposable")

        classified = classify_tidy_findings([clutter], [], tmp_path)

        assert classified[0].in_scope is False
        assert classified[0].blocking is False


def test_toolchain_gates_map_to_real_gate_names() -> None:
    names = {name for name, _gate in TOOLCHAIN_GATES}
    assert names == {"test", "lint"}
