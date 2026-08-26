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
    check_clone_freshness,
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


class TestCloneFreshness:
    """Multiple clones of the same installation can silently drift; the
    exact failure this session discovered manually before this check
    existed."""

    def _git(self, cwd: Path, *args: str) -> None:
        import subprocess

        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def _clone_with_remote(self, tmp_path: Path) -> SmithPaths:
        remote = tmp_path / "remote.git"
        remote.mkdir()
        self._git(remote, "init", "--bare", "-b", "main")

        origin = tmp_path / "origin"
        origin.mkdir()
        self._git(origin, "init", "-b", "main")
        self._git(origin, "config", "user.email", "test@example.com")
        self._git(origin, "config", "user.name", "test")
        (origin / "README.md").write_text("first\n", encoding="utf-8")
        self._git(origin, "add", "README.md")
        self._git(origin, "commit", "-m", "first")
        self._git(origin, "remote", "add", "origin", str(remote))
        self._git(origin, "push", "-u", "origin", "main")
        return SmithPaths(root=origin)

    def test_in_sync_clone_is_ok(self, tmp_path: Path) -> None:
        paths = self._clone_with_remote(tmp_path)
        result = check_clone_freshness(paths)
        assert result.health is Health.OK

    def test_unpushed_commit_warns(self, tmp_path: Path) -> None:
        paths = self._clone_with_remote(tmp_path)
        (paths.root / "new.md").write_text("second\n", encoding="utf-8")
        self._git(paths.root, "add", "new.md")
        self._git(paths.root, "commit", "-m", "second")

        result = check_clone_freshness(paths)
        assert result.health is Health.WARN
        assert "ahead" in result.detail

    def test_unpulled_remote_commit_warns(self, tmp_path: Path) -> None:
        paths = self._clone_with_remote(tmp_path)
        remote = tmp_path / "remote.git"
        other_clone = tmp_path / "other-clone"
        self._git(tmp_path, "clone", str(remote), str(other_clone))
        self._git(other_clone, "config", "user.email", "test@example.com")
        self._git(other_clone, "config", "user.name", "test")
        (other_clone / "elsewhere.md").write_text("elsewhere\n", encoding="utf-8")
        self._git(other_clone, "add", "elsewhere.md")
        self._git(other_clone, "commit", "-m", "elsewhere")
        self._git(other_clone, "push", "origin", "main")

        result = check_clone_freshness(paths)
        assert result.health is Health.WARN
        assert "behind" in result.detail

    def test_uncommitted_tracked_change_warns(self, tmp_path: Path) -> None:
        paths = self._clone_with_remote(tmp_path)
        (paths.root / "README.md").write_text("changed\n", encoding="utf-8")

        result = check_clone_freshness(paths)
        assert result.health is Health.WARN
        assert "uncommitted" in result.detail

    def test_non_git_directory_is_ok_not_a_failure(self, blank: SmithPaths) -> None:
        result = check_clone_freshness(blank)
        assert result.health is Health.OK
