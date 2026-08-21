"""FAIR documentation and mission discovery must be honest.

The failure both modules guard against is the same: a confident claim with nothing
behind it. A generated README that says "this directory contains files" satisfies a
gate and informs nobody. A fabricated mission propagates into every downstream
plan. So these tests check that both modules mark weak output as weak.
"""

from __future__ import annotations

from pathlib import Path

from smith import fair, mission
from smith.mission import Confidence, Kind


class TestFairExemptions:
    def test_tool_caches_are_exempt(self, tmp_path: Path) -> None:
        assert fair.is_exempt(tmp_path / "__pycache__", tmp_path)
        assert fair.is_exempt(tmp_path / ".venv" / "lib", tmp_path)

    def test_self_describing_directories_are_exempt(self, tmp_path: Path) -> None:
        # A directory with a SKILL.md already declares its purpose. Demanding a
        # second document creates two places to describe one thing.
        skill = tmp_path / "skills" / "thing"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: thing\n---\n", encoding="utf-8")
        assert fair.is_exempt(skill, tmp_path)

    def test_ordinary_directory_is_not_exempt(self, tmp_path: Path) -> None:
        target = tmp_path / "lib"
        target.mkdir()
        (target / "code.py").write_text("x = 1\n", encoding="utf-8")
        assert fair.is_exempt(target, tmp_path) is None


class TestFairAudit:
    def test_directory_without_readme_fails(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        target.mkdir()
        (target / "rows.csv").write_text("a,b\n", encoding="utf-8")
        status = fair.inspect(target, tmp_path)
        assert not status.ok
        assert status.problem == "no README.md"

    def test_readme_missing_sections_fails(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        target.mkdir()
        (target / "rows.csv").write_text("a,b\n", encoding="utf-8")
        (target / "README.md").write_text("# data\n\nSome prose.\n", encoding="utf-8")
        status = fair.inspect(target, tmp_path)
        assert not status.ok
        assert "Contents" in status.missing_sections

    def test_complete_readme_passes(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        target.mkdir()
        (target / "rows.csv").write_text("a,b\n", encoding="utf-8")
        (target / "README.md").write_text(
            "# data\n\n## Contents\nrows\n\n## Usage\nread it\n\n## Format\ncsv\n\n## Stability\nstable\n",
            encoding="utf-8",
        )
        assert fair.inspect(target, tmp_path).ok

    def test_empty_directory_is_not_documentable(self, tmp_path: Path) -> None:
        # A directory with no files is a namespace, not a unit of meaning.
        (tmp_path / "empty").mkdir()
        assert (tmp_path / "empty") not in fair.documentable(tmp_path)


class TestFairGeneration:
    def test_known_directory_gets_real_content(self, tmp_path: Path) -> None:
        target = tmp_path / "memory"
        target.mkdir()
        (target / "lessons.md").write_text("x\n", encoding="utf-8")
        rendered = fair.render(target, tmp_path)
        assert "Append-only" in rendered
        assert fair.STUB_MARKER not in rendered

    def test_unknown_directory_is_marked_as_a_stub(self, tmp_path: Path) -> None:
        # A guess stated confidently is worse than an obvious gap.
        target = tmp_path / "mystery"
        target.mkdir()
        (target / "thing.bin").write_text("x", encoding="utf-8")
        rendered = fair.render(target, tmp_path)
        assert fair.STUB_MARKER in rendered
        assert "TODO" in rendered

    def test_generated_files_carry_the_marker(self, tmp_path: Path) -> None:
        target = tmp_path / "memory"
        target.mkdir()
        (target / "lessons.md").write_text("x\n", encoding="utf-8")
        assert fair.GENERATED_MARKER in fair.render(target, tmp_path)

    def test_human_authored_readme_is_never_overwritten(self, tmp_path: Path) -> None:
        target = tmp_path / "docs"
        target.mkdir()
        (target / "topic.md").write_text("x\n", encoding="utf-8")
        readme = target / "README.md"
        readme.write_text("# Mine\n\nHand written, no marker.\n", encoding="utf-8")
        fair.write_missing(tmp_path)
        assert readme.read_text(encoding="utf-8").startswith("# Mine")

    def test_generated_readme_is_refreshed(self, tmp_path: Path) -> None:
        target = tmp_path / "memory"
        target.mkdir()
        (target / "lessons.md").write_text("x\n", encoding="utf-8")
        readme = target / "README.md"
        readme.write_text(f"{fair.GENERATED_MARKER}\n# stale\n", encoding="utf-8")
        written, _ = fair.write_missing(tmp_path)
        assert readme in written
        assert "Append-only" in readme.read_text(encoding="utf-8")

    def test_stubs_are_reportable(self, tmp_path: Path) -> None:
        target = tmp_path / "mystery"
        target.mkdir()
        (target / "thing.bin").write_text("x", encoding="utf-8")
        fair.write_missing(tmp_path)
        assert [p.parent.name for p in fair.stubs(tmp_path)] == ["mystery"]


class TestMissionHonesty:
    """Smith must never invent a mission."""

    def test_empty_project_reports_unknown(self, tmp_path: Path) -> None:
        found = mission.discover(tmp_path)
        assert not found.known
        assert found.confidence is Confidence.UNKNOWN
        assert "unknown" in found.summary

    def test_unknown_mission_advises_asking(self, tmp_path: Path) -> None:
        advice = " ".join(mission.discover(tmp_path).advice())
        assert "ask the human" in advice

    def test_agent_instructions_are_the_strongest_source(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text(
            "# Agents\n\n## Purpose\n\nThis repository trains isotope market analysts.\n",
            encoding="utf-8",
        )
        (tmp_path / "README.md").write_text(
            "# Thing\n\n## Purpose\n\nSomething else entirely.\n", encoding="utf-8"
        )
        found = mission.discover(tmp_path)
        assert found.confidence is Confidence.STATED
        assert "isotope" in found.statement
        assert "AGENTS.md" in found.agent_instructions

    def test_readme_purpose_section_is_used(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Tool\n\n## Purpose\n\nConverts CSV exports into a tidy schema.\n", encoding="utf-8"
        )
        found = mission.discover(tmp_path)
        assert found.confidence is Confidence.STATED
        assert "CSV" in found.statement

    def test_pyproject_description_counts_as_stated(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "1"\ndescription = "A retry-aware HTTP client"\n',
            encoding="utf-8",
        )
        found = mission.discover(tmp_path)
        assert found.confidence is Confidence.STATED
        assert "retry-aware" in found.statement

    def test_non_goals_are_captured(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# T\n\n## Purpose\n\nDo one thing.\n\n## Non-Goals\n\n- No web UI\n- No auth\n",
            encoding="utf-8",
        )
        found = mission.discover(tmp_path)
        assert found.non_goals == ["No web UI", "No auth"]
        assert "non-goal" in " ".join(found.advice())

    def test_every_evidence_item_names_its_source(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# T\n\n## Purpose\n\nA thing.\n", encoding="utf-8")
        for item in mission.discover(tmp_path).evidence:
            assert item.source


class TestProjectKind:
    """Kind changes what good advice looks like, so it must be right."""

    def test_console_scripts_mean_cli(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "1"\n[project.scripts]\nx = "x:main"\n',
            encoding="utf-8",
        )
        assert mission.discover(tmp_path).kind is Kind.CLI

    def test_dockerfile_means_service(self, tmp_path: Path) -> None:
        (tmp_path / "Dockerfile").write_text("FROM python\n", encoding="utf-8")
        assert mission.discover(tmp_path).kind is Kind.SERVICE

    def test_notebooks_mean_research(self, tmp_path: Path) -> None:
        (tmp_path / "analysis.ipynb").write_text("{}", encoding="utf-8")
        assert mission.discover(tmp_path).kind is Kind.RESEARCH

    def test_markdown_heavy_repo_is_notes(self, tmp_path: Path) -> None:
        for i in range(8):
            (tmp_path / f"note{i}.md").write_text("# note\n", encoding="utf-8")
        assert mission.discover(tmp_path).kind is Kind.NOTES

    def test_notes_projects_are_not_asked_for_tests(self, tmp_path: Path) -> None:
        # Holding a notes repo to a library's standards produces advice nobody
        # follows, and a rule nobody follows is worse than none.
        for i in range(8):
            (tmp_path / f"note{i}.md").write_text("# note\n", encoding="utf-8")
        found = mission.discover(tmp_path)
        assert not found.kind.expects_tests
        assert "do not demand tests" in " ".join(found.advice())

    def test_cli_projects_do_expect_tests(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "1"\n[project.scripts]\nx = "x:main"\n',
            encoding="utf-8",
        )
        assert mission.discover(tmp_path).kind.expects_tests

    def test_every_kind_states_its_expectations(self) -> None:
        for kind in Kind:
            assert kind.expectations


class TestRealProject:
    def test_smith_itself_is_discoverable(self) -> None:
        from smith.paths import SmithPaths

        found = mission.discover(SmithPaths.discover().root)
        assert found.statement
        assert found.confidence.trustworthy
