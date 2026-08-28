"""Regression for a real user request: a generic pyproject.toml/justfile
template A.W.I.N.O. instantiates fresh into a target project - never storing
any real project's name, dependencies, or description in A.W.I.N.O.'s own
repository or any shared location.
"""

from __future__ import annotations

from pathlib import Path

from smith.project_template import render_justfile, render_pyproject, scaffold, slugify


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
