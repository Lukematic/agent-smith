"""Regression for a real user request: a generic pyproject.toml/justfile
template A.W.I.N.O. instantiates fresh into a target project - never storing
any real project's name, dependencies, or description in A.W.I.N.O.'s own
repository or any shared location.
"""

from __future__ import annotations

from pathlib import Path

from smith.project_template import (
    independent_subprojects,
    is_multi_project_container,
    render_justfile,
    render_pyproject,
    scaffold,
    slugify,
)


class TestSlugify:
    def test_lowercases_and_hyphenates(self) -> None:
        assert slugify("My Cool Project") == "my-cool-project"

    def test_strips_non_alphanumeric(self) -> None:
        assert slugify("weird!!name??") == "weird-name"

    def test_empty_input_falls_back_to_project(self) -> None:
        assert slugify("") == "project"

    def test_already_valid_slug_is_unchanged(self) -> None:
        assert slugify("already-valid-slug") == "already-valid-slug"


class TestRenderPyproject:
    def test_produces_a_valid_toml_document(self) -> None:
        import tomllib

        body = render_pyproject("my project", "A test description")
        parsed = tomllib.loads(body)
        assert parsed["project"]["name"] == "my-project"
        assert parsed["project"]["description"] == "A test description"

    def test_carries_no_content_from_any_real_project(self) -> None:
        # The template must never leak A.W.I.N.O.'s own name/dependencies or
        # any other project's identity into a freshly-generated file.
        body = render_pyproject("some-new-project")
        assert "awino" not in body.lower()
        assert "smith" not in body.lower()

    def test_double_quotes_in_description_do_not_break_the_toml(self) -> None:
        import tomllib

        body = render_pyproject("proj", 'a "quoted" description')
        tomllib.loads(body)  # must not raise


class TestRenderJustfile:
    def test_contains_the_expected_recipes(self) -> None:
        body = render_justfile()
        assert "install:" in body
        assert "test:" in body
        assert "lint:" in body

    def test_carries_no_project_specific_content(self) -> None:
        body = render_justfile()
        assert "awino" not in body.lower()
        assert "smith" not in body.lower()


class TestScaffold:
    def test_writes_both_files_into_a_fresh_project(self, tmp_path: Path) -> None:
        results = scaffold(tmp_path, "fresh-project")
        assert {r.outcome for r in results} == {"written"}
        assert (tmp_path / "pyproject.toml").is_file()
        assert (tmp_path / "justfile").is_file()

    def test_never_overwrites_an_existing_pyproject_by_default(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("# hand-authored\n", encoding="utf-8")
        results = scaffold(tmp_path, "fresh-project")
        skipped = next(r for r in results if r.path.name == "pyproject.toml")
        assert skipped.outcome == "skipped"
        assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == "# hand-authored\n"

    def test_overwrite_flag_genuinely_replaces_when_explicitly_requested(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text("# stale\n", encoding="utf-8")
        results = scaffold(tmp_path, "fresh-project", overwrite=True)
        written = next(r for r in results if r.path.name == "pyproject.toml")
        assert written.outcome == "written"
        assert "stale" not in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")

    def test_the_generated_file_is_lf_not_crlf_on_disk(self, tmp_path: Path) -> None:
        # Same Windows newline-translation bug class caught elsewhere this
        # session (ownership.py) - the written bytes must match what was
        # rendered, not be silently translated by text-mode write.
        scaffold(tmp_path, "fresh-project")
        raw = (tmp_path / "pyproject.toml").read_bytes()
        assert b"\r\n" not in raw


class TestMultiProjectContainerDetection:
    """Regression for a real, live-caught bug, twice: running project-scaffold
    against the actual ai_explained workspace (21 loose topic subfolders, no
    .git of its own, exactly one real subproject: .smith) silently wrote a
    real pyproject.toml at the wrong level. The first fix attempt required
    two marker-bearing siblings before refusing and STILL passed against the
    real folder, because it has only one - the real signal is not "how many
    siblings", it is "does this folder itself have no identity of its own
    while something inside it already does.\""""

    def test_a_folder_with_no_marker_of_its_own_and_one_subproject_is_a_container(
        self, tmp_path: Path
    ) -> None:
        # This is the exact real shape that was missed by requiring 2+
        # siblings: only one genuine subproject, but the parent itself
        # declares nothing - re-verified live against the actual
        # ai_explained folder after this fix, not just this synthetic case.
        (tmp_path / "sub-a" / ".git").mkdir(parents=True)
        (tmp_path / "loose-topic-folder").mkdir()
        assert is_multi_project_container(tmp_path) is True

    def test_an_empty_folder_is_not_a_container(self, tmp_path: Path) -> None:
        assert is_multi_project_container(tmp_path) is False

    def test_a_genuine_single_project_with_a_vendored_dependency_is_not_flagged(
        self, tmp_path: Path
    ) -> None:
        # When root itself already declares a real project, one nested
        # subproject (a vendored dependency, a git submodule) is normal and
        # must not alone trip the container check - only a genuine sibling
        # collision does, which is rarer and left to a human to judge.
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (tmp_path / "vendor" / "some-dep" / ".git").mkdir(parents=True)
        assert is_multi_project_container(tmp_path) is False

    def test_a_project_with_two_independent_siblings_is_still_a_container(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (tmp_path / "vendor-a" / ".git").mkdir(parents=True)
        (tmp_path / "vendor-b" / "pyproject.toml").parent.mkdir(parents=True)
        (tmp_path / "vendor-b" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        assert is_multi_project_container(tmp_path) is True

    def test_hidden_directories_are_not_counted_as_subprojects(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".venv" / "pyproject.toml").parent.mkdir(parents=True)
        assert independent_subprojects(tmp_path) == []

    def test_detector_flags_the_exact_live_incident_shape(self, tmp_path: Path) -> None:
        # Reproduces the exact live incident shape: several independent
        # subdirectories, one of them already a full install-shaped project.
        # scaffold() itself has no opinion on this - it is the CLI's job to
        # ask is_multi_project_container() before calling scaffold() at all,
        # verified end-to-end in tests/test_cli_project_bootstrap.py.
        (tmp_path / "sandbox" / ".git").mkdir(parents=True)
        (tmp_path / "research_idea").mkdir()
        (tmp_path / "smith-install").mkdir()
        (tmp_path / "smith-install" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        assert is_multi_project_container(tmp_path) is True
