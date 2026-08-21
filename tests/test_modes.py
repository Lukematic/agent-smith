"""Mode installation must respect the schema and never destroy a user's own modes.

Two failure modes drive these tests. An invalid mode is dropped silently by the
extension, so the mode simply never appears with no error to debug. And a naive
write would overwrite a settings file that contains modes the user wrote
themselves, which is unacceptable for a tool that installs itself.
"""

from __future__ import annotations

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
        assert any("roleDefinition" in p for p in Mode(slug="x", name="X", role_definition="").validate())

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
            yaml.safe_dump({"customModes": [{"slug": "mine", "name": "Mine", "roleDefinition": "R", "groups": ["read"]}]}),
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
        outcome, _ = install(Mode(slug="thing", name="New", role_definition="Y"), target, force=True)
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
        project = [(t, present) for t, present in status(tmp_path, "agent-smith") if t.scope == "project"]
        assert project
        assert all(not present for _, present in project)


class TestSmithModes:
    def test_three_modes_are_built(self) -> None:
        assert len(build_modes(Path("/tmp/smith"))) == 3

    def test_all_are_schema_valid(self) -> None:
        for mode in build_modes(Path("/tmp/smith")):
            assert mode.validate() == [], f"{mode.slug}: {mode.validate()}"

    def test_consult_mode_cannot_edit(self) -> None:
        # The restriction is the whole point: a consult that edits is not a consult.
        ask = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "agent-smith-ask")
        assert "edit" not in ask.groups
        assert "command" not in ask.groups

    def test_plan_mode_edits_markdown_only(self) -> None:
        plan = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "agent-smith-plan")
        edit = next(g for g in plan.groups if isinstance(g, list) and g[0] == "edit")
        assert "md" in edit[1]["fileRegex"]

    def test_full_mode_can_edit(self) -> None:
        full = next(m for m in build_modes(Path("/tmp/smith")) if m.slug == "agent-smith")
        assert "edit" in full.groups

    def test_role_definition_stays_lean(self) -> None:
        # roleDefinition is resident on every turn. Embedding the whole persona
        # would duplicate the constitution and pay for it repeatedly, which is the
        # CONTEXT_BLOAT this tool exists to prevent.
        for mode in build_modes(Path("/tmp/smith")):
            assert len(mode.role_definition) < 2500, f"{mode.slug} roleDefinition is bloated"

    def test_role_definition_points_at_the_constitution(self) -> None:
        for mode in build_modes(Path("/tmp/smith")):
            assert "AGENT_SMITH.md" in mode.role_definition

    def test_every_mode_states_when_to_use_it(self) -> None:
        for mode in build_modes(Path("/tmp/smith")):
            assert mode.when_to_use
            assert mode.description

    def test_home_path_is_embedded_so_files_are_findable(self) -> None:
        modes = build_modes(Path("/opt/custom-smith"))
        assert all("/opt/custom-smith" in m.role_definition for m in modes)
