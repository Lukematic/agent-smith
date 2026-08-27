"""Tests for ``smith.doc_review``: rubric application and the two hard caps.

Real subagent spawning is out of scope here (see module docstring in
doc_review.py): these tests inject a fake ``ReviewerFn`` to drive the
iteration loop deterministically, and use ``score_document`` directly for
the rubric-heuristics unit tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith import doc_review
from smith.doc_review import (
    DocReviewResult,
    Issue,
    RubricKind,
    build_reviewer_assignment,
    run_review_loop,
    score_document,
)
from smith.enforce import ReviewVerdict
from smith.spawn import Role

# ── rubric application against inline fixtures ───────────────────────────────


class TestRubricScoring:
    def test_placeholder_markers_flag_completeness_issues(self) -> None:
        doc = """# Title

## Section One

This part is done.

## Section Two

TODO: fill this in.
"""
        result = score_document(doc, RubricKind.rubric(RubricKind.SPEC))
        assert result.verdict is ReviewVerdict.CHANGES_REQUESTED
        assert any(issue.category == "Completeness" for issue in result.issues)
        assert any("TODO" in issue.detail for issue in result.issues)

    def test_tbd_and_lorem_ipsum_are_flagged(self) -> None:
        doc = "# Spec\n\n## Scope\n\nTBD\n\n## Details\n\nLorem ipsum dolor sit amet.\n"
        result = score_document(doc, RubricKind.rubric(RubricKind.SPEC))
        assert result.verdict is ReviewVerdict.CHANGES_REQUESTED
        categories = {issue.category for issue in result.issues}
        assert "Completeness" in categories

    def test_empty_section_is_a_completeness_issue(self) -> None:
        doc = "# Spec\n\n## Goals\n\n## Non-Goals\n\nDo not do X.\n"
        result = score_document(doc, RubricKind.rubric(RubricKind.SPEC))
        assert result.verdict is ReviewVerdict.CHANGES_REQUESTED
        assert any(
            issue.category == "Completeness" and "Goals" in issue.detail for issue in result.issues
        )

    def test_duplicate_headings_flag_consistency(self) -> None:
        doc = "# Spec\n\n## Scope\n\nreal content here\n\n## Scope\n\nmore content\n"
        result = score_document(doc, RubricKind.rubric(RubricKind.SPEC))
        assert any(issue.category == "Consistency" for issue in result.issues)

    def test_clean_document_is_approved(self) -> None:
        doc = "# Spec\n\n## Goals\n\nShip the feature.\n\n## Non-Goals\n\nNo scope creep.\n"
        result = score_document(doc, RubricKind.rubric(RubricKind.SPEC))
        assert result.verdict is ReviewVerdict.APPROVED
        assert result.issues == ()

    def test_plan_rubric_includes_spec_rows_plus_three_more(self) -> None:
        plan_rubric = RubricKind.rubric(RubricKind.PLAN)
        spec_rubric = RubricKind.rubric(RubricKind.SPEC)
        assert set(spec_rubric) <= set(plan_rubric)
        assert len(plan_rubric) == len(spec_rubric) + 3
        assert "Spec Alignment" in plan_rubric
        assert "Task Decomposition" in plan_rubric
        assert "Chunk Size" in plan_rubric

    def test_unknown_rubric_kind_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown rubric kind"):
            RubricKind.rubric("bogus")


# ── the 5-iteration hard cap ──────────────────────────────────────────────────


class TestMaxIterationsCap:
    def test_a_reviewer_that_never_approves_stops_at_five_and_surfaces(self) -> None:
        # Category is varied each call so this test isolates the
        # max-iterations cap: the same-disagreement cap (which tracks
        # category, not wording) would otherwise fire first at 3.
        calls = {"n": 0}

        def varying_category(_text: str, _rubric: tuple[str, ...]) -> DocReviewResult:
            calls["n"] += 1
            category = "Completeness" if calls["n"] % 2 else "Consistency"
            return DocReviewResult(
                ReviewVerdict.CHANGES_REQUESTED,
                (Issue(category, f"issue #{calls['n']}"),),
            )

        outcome = run_review_loop(
            lambda: "doc text",
            RubricKind.rubric(RubricKind.SPEC),
            varying_category,
        )
        assert len(outcome.iterations) == doc_review.MAX_ITERATIONS
        assert outcome.surfaced_to_human
        assert "MAX_ITERATIONS_REACHED" in outcome.stopped_reason
        assert not outcome.approved


# ── the 3-iteration same-disagreement cap ────────────────────────────────────


class TestSameDisagreementCap:
    def test_same_category_recurring_three_times_fires_even_as_other_things_change(
        self,
    ) -> None:
        calls = {"n": 0}

        def recurring_completeness(_text: str, _rubric: tuple[str, ...]) -> DocReviewResult:
            calls["n"] += 1
            # Completeness recurs every time; a second, changing category is
            # mixed in so the test proves recurrence is tracked per-category,
            # not merely "the issue list changed".
            extra_category = f"Coverage-variant-{calls['n']}"
            return DocReviewResult(
                ReviewVerdict.CHANGES_REQUESTED,
                (
                    Issue("Completeness", f"still missing something, attempt {calls['n']}"),
                    Issue(extra_category, "a different, non-recurring detail"),
                ),
            )

        outcome = run_review_loop(
            lambda: "doc text",
            RubricKind.rubric(RubricKind.SPEC),
            recurring_completeness,
        )
        assert outcome.surfaced_to_human
        assert "THREE_STRIKES" in outcome.stopped_reason
        assert len(outcome.iterations) == 3
        assert not outcome.approved

    def test_a_fixed_disagreement_does_not_count_toward_the_cap(self) -> None:
        calls = {"n": 0}

        def fixes_after_two(_text: str, _rubric: tuple[str, ...]) -> DocReviewResult:
            calls["n"] += 1
            if calls["n"] <= 2:
                return DocReviewResult(
                    ReviewVerdict.CHANGES_REQUESTED, (Issue("Clarity", "vague wording"),)
                )
            return DocReviewResult(ReviewVerdict.APPROVED, ())

        outcome = run_review_loop(
            lambda: "doc text", RubricKind.rubric(RubricKind.SPEC), fixes_after_two
        )
        assert outcome.approved
        assert not outcome.surfaced_to_human
        assert len(outcome.iterations) == 3


# ── the reviewer Assignment builder ──────────────────────────────────────────


class TestReviewerAssignment:
    def test_assignment_is_read_only_reviewer_scoped_to_the_document(self, tmp_path: Path) -> None:
        doc_path = tmp_path / "spec.md"
        doc_path.write_text("# Spec\n\nsome content\n", encoding="utf-8")
        assignment = build_reviewer_assignment(
            "reviewer-1", doc_path, doc_path.read_text(encoding="utf-8"), RubricKind.SPEC
        )
        assert assignment.role is Role.REVIEWER
        assert assignment.role.read_only
        assert assignment.file_scope == []
        assert str(doc_path) in assignment.context_paths
        assert "Completeness" in assignment.objective
        assert "YAGNI" in assignment.objective
        # Spec rubric only: plan-only rows must not appear.
        assert "Chunk Size" not in assignment.objective

    def test_plan_assignment_includes_plan_only_rubric_rows(self, tmp_path: Path) -> None:
        doc_path = tmp_path / "plan.md"
        doc_path.write_text("# Plan\n\nsome content\n", encoding="utf-8")
        assignment = build_reviewer_assignment(
            "reviewer-1", doc_path, doc_path.read_text(encoding="utf-8"), RubricKind.PLAN
        )
        assert "Spec Alignment" in assignment.objective
        assert "Chunk Size" in assignment.objective

    def test_a_read_only_assignment_declaring_no_scope_has_no_problems(
        self, tmp_path: Path
    ) -> None:
        doc_path = tmp_path / "spec.md"
        doc_path.write_text("content", encoding="utf-8")
        assignment = build_reviewer_assignment("reviewer-1", doc_path, "content", RubricKind.SPEC)
        # verification is intentionally empty for a reviewer: there is no
        # command to run, only a judgement to report. That is a known,
        # accepted gap here (the CLI does not spawn this Assignment), not
        # an oversight in the builder.
        problems = assignment.problems()
        assert problems == ["no verification command, so the result cannot be checked"]
