"""Lesson recall: storage becomes retrieval. Given an objective, surface the
few lessons that share vocabulary with it - the memory that is relevant now,
not a count of how much memory exists."""

from __future__ import annotations

from pathlib import Path

from smith.recall import recall_lessons


def _lessons(tmp_path: Path) -> Path:
    p = tmp_path / "lessons.md"
    p.write_text(
        "- [2026-01-01] `FLOOR_VERIFY_CWD` - verify commands must be relative to the project root, not .smith.\n"
        "- [2026-01-02] `SILENT_CHAIN_NOOP` - never infer gate closure from a silent && chain.\n"
        "- [2026-01-03] `LINTER_FALSE_POSITIVE` - a grep for a retired script name matches prose about it.\n"
        "- [2026-01-04] Unrelated note about pyproject packaging on OneDrive.\n",
        encoding="utf-8",
    )
    return p


def test_returns_lessons_sharing_vocabulary_with_the_objective(tmp_path: Path) -> None:
    hits = recall_lessons(_lessons(tmp_path), "fix the verify command path in floor close")
    assert hits and "FLOOR_VERIFY_CWD" in hits[0]


def test_caps_at_three_and_ranks_by_overlap(tmp_path: Path) -> None:
    hits = recall_lessons(_lessons(tmp_path), "gate close chain verify grep script prose", limit=3)
    assert len(hits) == 3


def test_no_overlap_returns_empty_not_everything(tmp_path: Path) -> None:
    assert recall_lessons(_lessons(tmp_path), "quantum chromodynamics") == []


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert recall_lessons(tmp_path / "nope.md", "anything") == []


def test_stopwords_do_not_match(tmp_path: Path) -> None:
    assert recall_lessons(_lessons(tmp_path), "the a is not from") == []
