"""d4cf: symbol drift. A name removed from code that still appears anywhere -
help text, a rubric, a skill doc, a string literal - is the bug class behind
BLOCKED, dispatch_loop, registry_build.ps1, and the Goose script. Grep both
sides of the diff, not one."""

from __future__ import annotations

from pathlib import Path

from smith.drift import removed_symbols, symbol_drift

DIFF_ENUM_VALUE = """\
diff --git a/src/smith/enforce.py b/src/smith/enforce.py
--- a/src/smith/enforce.py
+++ b/src/smith/enforce.py
@@ -83,3 +83,2 @@ class ReviewVerdict(StrEnum):
     CHANGES_REQUESTED = "changes-requested"
-    BLOCKED = "blocked"
"""

DIFF_FUNCTION = """\
--- a/src/smith/x.py
+++ b/src/smith/x.py
@@
-def _load_runner_recipes(project):
-    return []
+def keep(): ...
"""

DIFF_RENAME = """\
--- a/src/smith/x.py
+++ b/src/smith/x.py
@@
-MAX_CONCURRENT = 6
+MAX_PARALLEL = 6
"""


def test_removed_symbols_finds_enum_values_functions_and_constants() -> None:
    assert removed_symbols(DIFF_ENUM_VALUE) == {"BLOCKED", "blocked"}
    assert removed_symbols(DIFF_FUNCTION) == {"_load_runner_recipes"}
    assert removed_symbols(DIFF_RENAME) == {"MAX_CONCURRENT"}


def test_a_symbol_still_referenced_in_prose_is_reported(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cli.py").write_text(
        'help="approved, changes-requested, or blocked"\n', encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("emit 'blocked' when unsafe\n", encoding="utf-8")
    findings = symbol_drift(DIFF_ENUM_VALUE, tmp_path)
    paths = {f.path.name for f in findings}
    assert "cli.py" in paths and "guide.md" in paths
    assert all(f.symbol in {"blocked", "BLOCKED"} for f in findings)


def test_a_symbol_referenced_only_in_its_own_removal_is_clean(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("def keep(): ...\n", encoding="utf-8")
    assert symbol_drift(DIFF_FUNCTION, tmp_path) == []


def test_short_or_common_names_are_not_scanned(tmp_path: Path) -> None:
    diff = "--- a/x.py\n+++ b/x.py\n@@\n-x = 1\n-ok = 2\n-data = 3\n"
    (tmp_path / "x.py").write_text("x ok data everywhere", encoding="utf-8")
    assert symbol_drift(diff, tmp_path) == []


def test_findings_carry_file_and_line(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "s.md").write_text(
        "line one\nuse MAX_CONCURRENT here\n", encoding="utf-8"
    )
    f = symbol_drift(DIFF_RENAME, tmp_path)
    assert len(f) == 1 and f[0].line == 2 and f[0].symbol == "MAX_CONCURRENT"


def test_lowercase_values_only_match_when_quoted(tmp_path: Path) -> None:
    """`blocked` is also an English word; a sentence using it is not a reference."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("the run was blocked by health\n", encoding="utf-8")
    (tmp_path / "docs" / "b.md").write_text(
        "--verdict blocked is accepted: 'blocked'\n", encoding="utf-8"
    )
    hits = symbol_drift(DIFF_ENUM_VALUE, tmp_path)
    assert {f.path.name for f in hits} == {"b.md"}
