"""Project health gates must catch real decay and not cry wolf.

The doctor is only useful if it fails on genuine problems and stays quiet on
correct work. Both halves are tested here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith import health
from smith.health import (
    Health,
    check_docs,
    check_justfile_shim,
    check_memory,
    check_pyproject,
    check_seeds,
)
from smith.paths import SmithPaths


@pytest.fixture
def real() -> SmithPaths:
    return SmithPaths.discover()


@pytest.fixture
def blank(tmp_path: Path) -> SmithPaths:
    paths = SmithPaths(root=tmp_path)
    paths.ensure_scaffold()
    return paths


class TestRealProjectIsHealthy:
    """The repository must pass its own gates. If it does not, fix the repo."""

    def test_no_failing_gates(self, real: SmithPaths) -> None:
        results = health.run_all(real, fast=True)
        failing = [r.name for r in results if r.blocking]
        assert not failing, f"doctor failing on: {failing}"

    def test_docs_are_all_linked(self, real: SmithPaths) -> None:
        assert check_docs(real).health is Health.OK

    def test_memory_ledger_is_well_formed(self, real: SmithPaths) -> None:
        assert check_memory(real).health is Health.OK

    def test_pyproject_configures_quality_tools(self, real: SmithPaths) -> None:
        assert check_pyproject(real).health is Health.OK


class TestDocsGate:
    def test_missing_readme_fails(self, blank: SmithPaths) -> None:
        assert check_docs(blank).health is Health.FAIL

    def test_unlinked_doc_fails(self, blank: SmithPaths) -> None:
        (blank.root / "README.md").write_text("# Thing\n", encoding="utf-8")
        (blank.docs / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
        result = check_docs(blank)
        assert result.health is Health.FAIL
        assert "orphan.md" in result.detail

    def test_linked_doc_passes(self, blank: SmithPaths) -> None:
        (blank.root / "README.md").write_text("See [x](docs/kept.md)\n", encoding="utf-8")
        (blank.docs / "kept.md").write_text("# Kept\n", encoding="utf-8")
        assert check_docs(blank).health is Health.OK

    def test_broken_link_fails(self, blank: SmithPaths) -> None:
        (blank.root / "README.md").write_text("See [gone](docs/gone.md)\n", encoding="utf-8")
        result = check_docs(blank)
        assert result.health is Health.FAIL
        assert "gone.md" in result.detail


class TestMemoryGate:
    def test_missing_ledger_fails(self, blank: SmithPaths) -> None:
        assert check_memory(blank).health is Health.FAIL

    def test_undated_lesson_fails(self, blank: SmithPaths) -> None:
        blank.lessons.write_text("# Lessons\n\n- a rule with no date\n", encoding="utf-8")
        assert check_memory(blank).health is Health.FAIL

    def test_dated_lesson_passes(self, blank: SmithPaths) -> None:
        blank.lessons.write_text(
            "# Lessons\n\n- [2026-08-21] `MODE` do the thing. (surface: tools)\n", encoding="utf-8"
        )
        assert check_memory(blank).health is Health.OK

    def test_format_template_in_a_fence_is_not_a_lesson(self, blank: SmithPaths) -> None:
        # This exact false positive failed the real repo on a correct file.
        blank.lessons.write_text(
            "# Lessons\n\n```\n- [yyyy-mm-dd] `MODE` the rule.\n```\n\n"
            "- [2026-08-21] `REAL` an actual rule. (surface: tools)\n",
            encoding="utf-8",
        )
        assert check_memory(blank).health is Health.OK

    def test_empty_ledger_warns_without_blocking(self, blank: SmithPaths) -> None:
        blank.lessons.write_text("# Lessons\n\nNo rules yet.\n", encoding="utf-8")
        result = check_memory(blank)
        assert result.health is Health.WARN
        assert not result.blocking


class TestPyprojectGate:
    def test_missing_pyproject_fails(self, blank: SmithPaths) -> None:
        assert check_pyproject(blank).health is Health.FAIL

    def test_unparseable_pyproject_fails(self, blank: SmithPaths) -> None:
        (blank.root / "pyproject.toml").write_text("[project\nbroken", encoding="utf-8")
        result = check_pyproject(blank)
        assert result.health is Health.FAIL
        assert "parse" in result.detail

    def test_pyproject_without_ruff_fails(self, blank: SmithPaths) -> None:
        (blank.root / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "1"\nrequires-python = ">=3.12"\n', encoding="utf-8"
        )
        result = check_pyproject(blank)
        assert result.health is Health.FAIL
        assert "ruff" in result.detail

    def test_ruff_missing_core_rules_fails(self, blank: SmithPaths) -> None:
        (blank.root / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "1"\nrequires-python = ">=3.12"\n'
            '[tool.ruff.lint]\nselect = ["W"]\n'
            "[tool.pytest.ini_options]\ntestpaths = []\n",
            encoding="utf-8",
        )
        assert check_pyproject(blank).health is Health.FAIL


class TestJustfileGate:
    def test_missing_justfile_fails(self, blank: SmithPaths) -> None:
        assert check_justfile_shim(blank).health is Health.FAIL

    def test_justfile_missing_required_recipes_fails(self, blank: SmithPaths) -> None:
        (blank.root / "justfile").write_text("install:\n    echo hi\n", encoding="utf-8")
        result = check_justfile_shim(blank)
        assert result.health is Health.FAIL
        assert "missing recipes" in result.detail


class TestReporting:
    def test_summary_counts_every_result(self, real: SmithPaths) -> None:
        results = health.run_all(real, fast=True)
        counts = health.summarise(results)
        assert sum(counts.values()) == len(results)

    def test_json_output_is_valid(self, real: SmithPaths) -> None:
        import json

        payload = json.loads(health.as_json(health.run_all(real, fast=True)))
        assert "summary" in payload
        assert payload["checks"]

    def test_every_non_ok_result_carries_a_remedy(self, real: SmithPaths) -> None:
        # A finding without a fix is a complaint, not a gate.
        for result in health.run_all(real, fast=True):
            if result.health is not Health.OK:
                assert result.remedy, f"{result.name} has no remedy"


class TestProjectAwareSeeds:
    def test_project_root_does_not_inherit_smith_homes_tracker(
        self, blank: SmithPaths, tmp_path: Path
    ) -> None:
        project = tmp_path / "separate-project"
        project.mkdir()
        result = check_seeds(blank, project)
        assert result.health is Health.WARN
        assert any(
            phrase in result.detail for phrase in ("no .seeds", "not installed", "not on PATH")
        )

    def test_legacy_markdown_tracker_warns_instead_of_demanding_deletion(
        self, blank: SmithPaths, tmp_path: Path, monkeypatch
    ) -> None:
        from smith import seeds as seeds_module

        project = tmp_path / "project"
        (project / "tasks").mkdir(parents=True)
        (project / "tasks" / "todo.md").write_text("- [ ] old work\n", encoding="utf-8")

        monkeypatch.setattr(
            seeds_module.Seeds, "state", lambda self: (seeds_module.SeedsState.READY, "ready")
        )
        result = check_seeds(blank, project)
        assert result.health is Health.WARN
        assert "legacy" in result.detail
        assert "archive" in result.remedy
