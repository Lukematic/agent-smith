"""Mode installation must respect the schema and never destroy a user's own modes.

Two failure modes drive these tests. An invalid mode is dropped silently by the
extension, so the mode simply never appears with no error to debug. And a naive
write would overwrite a settings file that contains modes the user wrote
themselves, which is unacceptable for a tool that installs itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from smith.modes import EDITORS, Mode, ModeTarget, build_modes, discover, install, status


@pytest.fixture
def target(tmp_path: Path) -> ModeTarget:
    return ModeTarget("kilo", "Kilo Code", tmp_path / "custom_modes.yaml", "global")


def read_modes(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["customModes"]


class TestSchema:
    def test_valid_mode_passes(self) -> None:
        mode = Mode(slug="thing", name="Thing", role_definition="You are a thing.")
        assert mode.validate() == []

    @pytest.mark.parametrize("slug", ["has space", "has_underscore", "has.dot", "has/slash"])
    def test_invalid_slug_is_rejected(self, slug: str) -> None:
        mode = Mode(slug=slug, name="X", role_definition="Y")
        assert any("slug" in p for p in mode.validate())

    def test_missing_role_definition_is_rejected(self) -> None:
        assert any(
            "roleDefinition" in p for p in Mode(slug="x", name="X", role_definition="").validate()
        )

    def test_unknown_tool_group_is_rejected(self) -> None:
        mode = Mode(slug="x", name="X", role_definition="Y", groups=["read", "telepathy"])
        assert any("telepathy" in p for p in mode.validate())

    def test_duplicate_tool_group_is_rejected(self) -> None:
        mode = Mode(slug="x", name="X", role_definition="Y", groups=["read", "read"])
        assert any("duplicate" in p for p in mode.validate())

    def test_tuple_group_with_file_regex_is_valid(self) -> None:
        mode = Mode(
            slug="x",
            name="X",
            role_definition="Y",
            groups=["read", ["edit", {"fileRegex": r"\.md$"}]],
        )
        assert mode.validate() == []

    def test_empty_optional_fields_are_omitted(self) -> None:
        # Present-but-empty is not the same as absent: the schema applies min(1)
        # checks to values that exist.
        payload = Mode(slug="x", name="X", role_definition="Y").to_dict()
        assert "whenToUse" not in payload
        assert "customInstructions" not in payload

    def test_serialization_uses_camel_case(self) -> None:
        payload = Mode(
            slug="x", name="X", role_definition="Y", when_to_use="Z", custom_instructions="W"
        ).to_dict()
        assert payload["roleDefinition"] == "Y"
        assert payload["whenToUse"] == "Z"
        assert payload["customInstructions"] == "W"


class TestInstall:
    def test_installs_into_an_absent_file(self, target: ModeTarget) -> None:
        mode = Mode(slug="thing", name="Thing", role_definition="You are a thing.")
        outcome, _ = install(mode, target)
        assert outcome == "INSTALLED"
        assert [m["slug"] for m in read_modes(target.path)] == ["thing"]

    def test_preserves_existing_user_modes(self, target: ModeTarget) -> None:
        # The critical case: a tool that installs itself must not delete the modes
        # its user wrote.
        target.path.write_text(
            yaml.safe_dump(
                {
                    "customModes": [
                        {"slug": "mine", "name": "Mine", "roleDefinition": "R", "groups": ["read"]}
                    ]
                }
            ),
            encoding="utf-8",
        )
        install(Mode(slug="thing", name="Thing", role_definition="Y"), target)
        slugs = [m["slug"] for m in read_modes(target.path)]
        assert slugs == ["mine", "thing"]

    def test_reinstall_is_skipped_without_force(self, target: ModeTarget) -> None:
        mode = Mode(slug="thing", name="Thing", role_definition="Y")
        install(mode, target)
        outcome, detail = install(mode, target)
        assert outcome == "SKIPPED"
        assert "force" in detail

    def test_force_updates_in_place_without_duplicating(self, target: ModeTarget) -> None:
        install(Mode(slug="thing", name="Old", role_definition="Y"), target)
        outcome, _ = install(
            Mode(slug="thing", name="New", role_definition="Y"), target, force=True
        )
        assert outcome == "UPDATED"
        entries = read_modes(target.path)
        assert len(entries) == 1
        assert entries[0]["name"] == "New"

    def test_invalid_mode_is_not_written(self, target: ModeTarget) -> None:
        outcome, _ = install(Mode(slug="bad slug", name="X", role_definition="Y"), target)
        assert outcome == "FAILED"
        assert not target.path.exists()

    def test_corrupt_file_is_not_overwritten(self, target: ModeTarget) -> None:
        # Silently replacing an unparseable file would destroy whatever the user
        # was in the middle of writing.
        target.path.write_text("customModes: [ this is not: valid: yaml", encoding="utf-8")
        original = target.path.read_text(encoding="utf-8")
        outcome, detail = install(Mode(slug="thing", name="T", role_definition="Y"), target)
        assert outcome == "FAILED"
        assert "YAML" in detail
        assert target.path.read_text(encoding="utf-8") == original

    def test_empty_file_is_treated_as_no_modes(self, target: ModeTarget) -> None:
        target.path.write_text("", encoding="utf-8")
        outcome, _ = install(Mode(slug="thing", name="T", role_definition="Y"), target)
        assert outcome == "INSTALLED"


class TestDiscovery:
    def test_every_editor_gets_a_project_target(self, tmp_path: Path) -> None:
        project_targets = [t for t in discover(tmp_path) if t.scope == "project"]
        assert {t.editor for t in project_targets} == set(EDITORS)

    def test_project_filenames_match_the_editor(self, tmp_path: Path) -> None:
        names = {t.editor: t.path.name for t in discover(tmp_path) if t.scope == "project"}
        assert names["kilo"] == ".kilocodemodes"
        assert names["roo"] == ".roomodes"

    def test_status_reports_absence_for_project_scope(self, tmp_path: Path) -> None:
        # Only project targets are asserted: global targets legitimately reflect a
        # real install on the machine running the tests.
        project = [
            (t, present) for t, present in status(tmp_path, "agent-smith") if t.scope == "project"
        ]
        assert project
        assert all(not present for _, present in project)


class TestSmithModes:
    def test_five_modes_are_built(self) -> None:
        assert len(build_modes(Path("/tmp/smith"))) == 5

    def test_all_are_schema_valid(self) -> None:
        for mode in build_modes(Path("/tmp/smith")):
            assert mode.validate() == [], f"{mode.slug}: {mode.validate()}"

    def test_consult_mode_cannot_edit(self) -> None:
        # The restriction is the whole point: a consult that edits is not a consult.
        ask = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "awino-consult")
        assert "edit" not in ask.groups
        assert "command" not in ask.groups

    def test_plan_mode_edits_markdown_only(self) -> None:
        plan = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "awino-plan")
        edit = next(g for g in plan.groups if isinstance(g, list) and g[0] == "edit")
        assert "md" in edit[1]["fileRegex"]
        assert "command" not in plan.groups

    def test_discover_mode_is_read_only(self) -> None:
        mode = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "awino-discover")
        assert "edit" not in mode.groups
        assert "command" not in mode.groups
        assert "one unresolved question" in mode.custom_instructions

    def test_research_mode_loads_evidence_and_reproducibility(self) -> None:
        mode = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "awino-research")
        assert "awino-evidence" in mode.custom_instructions
        assert "awino-reproducibility" in mode.custom_instructions
        assert "edit" not in mode.groups

    def test_full_mode_can_edit(self) -> None:
        full = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "awino")
        assert "edit" in full.groups

    def test_modes_without_command_do_not_advertise_executable_cli_commands(self) -> None:
        command_pattern = re.compile(r"`(?:awino|smith)(?:\s[^`]*)?`")
        for mode in build_modes(Path("/tmp/smith")):
            if "command" in mode.groups:
                continue
            text = f"{mode.role_definition}\n{mode.custom_instructions}"
            assert not command_pattern.search(text), mode.slug

    def test_all_mode_skill_routes_use_canonical_awino_names(self) -> None:
        legacy_skill = re.compile(r"\bsmith-(?:consult|discover|evidence|reproducibility|rpi)\b")
        for mode in build_modes(Path("/tmp/smith")):
            assert not legacy_skill.search(mode.custom_instructions), mode.slug

    def test_primary_mode_exposes_the_startup_display_contract(self) -> None:
        primary = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "awino")
        required = {
            "Project",
            "Mission confidence",
            "Toolchain",
            "Tracker",
            "Active run",
            "Pending human decision",
            "Next recommended action",
            "Route skill",
        }
        assert all(label in primary.custom_instructions for label in required)

    def test_primary_mode_cannot_silently_switch_the_selected_kilo_mode(self) -> None:
        primary = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "awino")
        assert "cannot silently switch" in primary.custom_instructions
        assert "selected Kilo mode" in primary.custom_instructions

    def test_role_definition_stays_lean(self) -> None:
        # roleDefinition is resident on every turn. Embedding the whole persona
        # would duplicate the constitution and pay for it repeatedly, which is the
        # CONTEXT_BLOAT this tool exists to prevent.
        for mode in build_modes(Path("/tmp/smith")):
            assert len(mode.role_definition) < 2500, f"{mode.slug} roleDefinition is bloated"

    def test_role_definition_points_at_the_constitution(self) -> None:
        for mode in build_modes(Path("/tmp/smith")):
            assert "AWINO.md" in mode.role_definition

    def test_no_active_mode_loads_the_legacy_constitution_pointer(self) -> None:
        for mode in build_modes(Path("/tmp/smith")):
            assert "AGENT_SMITH.md" not in mode.role_definition

    def test_every_mode_states_when_to_use_it(self) -> None:
        for mode in build_modes(Path("/tmp/smith")):
            assert mode.when_to_use
            assert mode.description

    def test_home_path_is_embedded_so_files_are_findable(self) -> None:
        modes = build_modes(Path("/opt/custom-smith"))
        assert all("/opt/custom-smith" in m.role_definition for m in modes)


class TestReadmeMatchesGeneratedModes:
    """The README documents mode names by hand; build_modes() generates them.

    Those two drifted once already: the code was renamed to A.W.I.N.O. but the
    README table still said Agent Smith / Smith Consult / etc, which is exactly
    the kind of stale-doc bug a human catches by reading, not by running the
    existing suite. This makes that drift a CI failure instead of a support
    question raised after release.
    """

    def _readme_text(self) -> str:
        root = Path(__file__).resolve().parents[1]
        return (root / "README.md").read_text(encoding="utf-8")

    def test_every_generated_mode_name_appears_in_readme(self) -> None:
        readme = self._readme_text()
        for mode in build_modes(Path("/tmp/smith")):
            assert mode.name in readme, f"README is missing or stale for mode name {mode.name!r}"

    def test_readme_does_not_still_reference_the_old_mode_names(self) -> None:
        readme = self._readme_text()
        stale = [
            "🕶️ Agent Smith",
            "🕶️ Smith Consult",
            "🕶️ Smith Plan",
            "🕶️ Smith Discover",
            "🕶️ Smith Research",
        ]
        found = [name for name in stale if name in readme]
        assert not found, f"README still contains renamed-away mode names: {found}"

    @pytest.mark.parametrize("relative_path", ["agents/awino.md", "AWINO.md", "README.md"])
    def test_primary_flow_contract_is_documented(self, relative_path: str) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / relative_path).read_text(encoding="utf-8")
        for label in (
            "Project",
            "Mission confidence",
            "Toolchain",
            "Tracker",
            "Active run",
            "Pending human decision",
            "Next recommended action",
            "Route skill",
        ):
            assert label in text, f"{relative_path} is missing {label!r}"


class TestIdempotentLinking:
    """A reinstall must not fail on links it created itself.

    Windows junctions do not report as symlinks, so an is_symlink() check alone
    missed them and a second install produced 21 FAILED lines. Reinstalling after a
    git pull is the normal path, so it must be quiet.
    """

    def test_relinking_the_same_source_is_skipped(self, tmp_path: Path) -> None:
        from smith.harness import _link_or_copy

        source = tmp_path / "src"
        source.mkdir()
        (source / "SKILL.md").write_text("x", encoding="utf-8")
        destination = tmp_path / "dst"

        first, _ = _link_or_copy(source, destination)
        assert first in {"LINKED", "COPIED"}

        second, detail = _link_or_copy(source, destination)
        if first == "LINKED":
            assert second == "SKIPPED"
            assert "git pull" in detail
        else:
            # A copy cannot be detected as current, so it is refreshed instead.
            assert second == "COPIED"

    def test_foreign_link_pointing_elsewhere_is_refused(self, tmp_path: Path) -> None:
        from smith.harness import _link_or_copy

        old = tmp_path / "old"
        old.mkdir()
        (old / "marker-old").write_text("x", encoding="utf-8")
        new = tmp_path / "new"
        new.mkdir()
        (new / "marker-new").write_text("x", encoding="utf-8")
        destination = tmp_path / "dst"

        _link_or_copy(old, destination)
        outcome, _ = _link_or_copy(new, destination)
        assert outcome == "FAILED"
        assert (destination / "marker-old").exists()
        assert not (destination / "marker-new").exists()

    def test_an_unrecorded_preexisting_link_is_refused_without_overwrite(
        self, tmp_path: Path
    ) -> None:
        # Regression for a real bug found live on a machine with skills
        # installed before ownership.record() was called on link success: a
        # manifest-unaware link must not be silently relinked, and must not
        # be confused with a genuinely foreign (never-A.W.I.N.O.-owned) path.
        from smith.harness import _link_or_copy

        source = tmp_path / "source"
        source.mkdir()
        (source / "marker").write_text("x", encoding="utf-8")
        destination = tmp_path / "dst"

        # Create the link the same way the installer's own fallback does,
        # without going through _link_or_copy, so no ownership entry exists -
        # exactly the state a pre-fix install left behind.
        first, _ = _link_or_copy(source, destination)
        assert first == "LINKED"
        from smith import ownership

        manifest = ownership.manifest_path(destination.parent)
        manifest.unlink()

        outcome, detail = _link_or_copy(source, destination)
        assert outcome == "SKIPPED"
        assert "already linked" in detail

    def test_overwrite_repoints_an_owned_link_after_backing_it_up(self, tmp_path: Path) -> None:
        # The real fix: --overwrite is the escape hatch for a link that is
        # genuinely A.W.I.N.O.'s own but now needs to point somewhere else
        # (a moved or consolidated repository), instead of the flag doing
        # nothing at all.
        from smith.harness import _link_or_copy

        old = tmp_path / "old"
        old.mkdir()
        (old / "marker-old").write_text("x", encoding="utf-8")
        new = tmp_path / "new"
        new.mkdir()
        (new / "marker-new").write_text("x", encoding="utf-8")
        destination = tmp_path / "dst"

        first, _ = _link_or_copy(old, destination)
        assert first == "LINKED"

        without_overwrite, _ = _link_or_copy(new, destination)
        assert without_overwrite == "FAILED"

        with_overwrite, _ = _link_or_copy(new, destination, overwrite=True)
        assert with_overwrite == "LINKED"
        assert (destination / "marker-new").exists()
        assert not (destination / "marker-old").exists()

        backups = list((destination.parent / ".awino-backups").glob("*/dst"))
        assert backups, "expected the old destination to be backed up, not discarded"


class TestHarnessFrontmatter:
    """Each harness needs a different frontmatter shape, and getting it wrong
    fails silently: the file lands on disk, nothing errors, and the agent never
    appears. Every assertion here was verified against a real installation.
    """

    def _persona(self, harness, tmp_path: Path) -> str:
        from smith.harness import _persona_for

        source = tmp_path / "agent-smith.md"
        source.write_text(
            "---\nname: agent-smith\ndescription: Does agentic engineering\n"
            "model: claude-sonnet-4-5\n---\n\nBody text here.\n",
            encoding="utf-8",
        )
        return _persona_for(harness, source)

    def test_kilo_requires_mode_primary_to_be_selectable(self, tmp_path: Path) -> None:
        # Without `mode: primary` Kilo installs a subagent: invocable by another
        # agent but invisible in the mode selector. That was the real bug behind
        # "it is not a mode".
        from smith.harness import Harness

        rendered = self._persona(Harness.KILO, tmp_path)
        assert "mode: primary" in rendered
        assert "displayName: A.W.I.N.O." in rendered

    def test_claude_declares_tools(self, tmp_path: Path) -> None:
        from smith.harness import Harness

        rendered = self._persona(Harness.CLAUDE, tmp_path)
        assert "tools:" in rendered
        assert "Read" in rendered

    def test_copilot_quotes_description_and_declares_tools(self, tmp_path: Path) -> None:
        from smith.harness import Harness

        rendered = self._persona(Harness.COPILOT, tmp_path)
        assert "description: '" in rendered
        assert "tools: []" in rendered

    def test_cursor_uses_always_apply(self, tmp_path: Path) -> None:
        from smith.harness import Harness

        rendered = self._persona(Harness.CURSOR, tmp_path)
        assert "alwaysApply:" in rendered
        assert "mode:" not in rendered

    def test_body_survives_every_adaptation(self, tmp_path: Path) -> None:
        from smith.harness import Harness

        for harness in Harness:
            assert "Body text here." in self._persona(harness, tmp_path)

    def test_kilo_global_root_is_config_kilo(self, tmp_path: Path, monkeypatch) -> None:
        # ~/.kilo is not where Kilo reads global agents. It is ~/.config/kilo, and
        # the earlier wrong path produced an install that looked successful and
        # did nothing.
        from smith.harness import Harness

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert Harness.KILO.global_root.parts[-2:] == (".config", "kilo")

    def test_copilot_filename_encodes_the_artifact_type(self) -> None:
        from smith.harness import Harness

        assert Harness.COPILOT.persona_filename.endswith(".chatmode.md")

    def test_only_real_skill_harnesses_claim_skill_support(self) -> None:
        from smith.harness import Harness

        assert Harness.CLAUDE.supports_skills
        assert Harness.KILO.supports_skills
        assert not Harness.CURSOR.supports_skills
        assert not Harness.COPILOT.supports_skills
