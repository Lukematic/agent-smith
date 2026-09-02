"""Roo as a probe-verified harness target, and Cline/Codex explicitly deferred
rather than guessed at.

An operator with no floor is worse than no elevator: shipping a skills
directory for a tool whose persona location was never proven would satisfy
the letter of "any tool" while breaking the spirit - the human would get
capabilities with no way to select the agent that uses them. So Roo, whose
persona *and* skills paths are both proven (mode support via modes.py,
skills via a verified ~/.roo/skills/<name>/SKILL.md), gets full treatment.
Cline and Codex get a real skills path and an honest, reported gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.harness import Harness


def _probe(path: Path) -> bool:
    return path.is_dir()


class TestRooSkillsInstallToTheProbedPath:
    def test_roo_global_root_matches_the_probed_home_directory(self) -> None:
        assert Harness.ROO.global_root == Path.home() / ".roo"

    def test_roo_supports_skills(self) -> None:
        assert Harness.ROO.supports_skills is True

    def test_roo_skills_land_at_the_probed_layout(self, tmp_path: Path) -> None:
        from smith.harness import Target

        target = Target(Harness.ROO, tmp_path / "roo-home", "global")
        assert target.skills_root == tmp_path / "roo-home" / "skills"


class TestRooModeSupportStillResolvesThroughModesPy:
    def test_roo_is_a_known_editor_in_modes_py(self) -> None:
        from smith.modes import EDITORS

        assert "roo" in EDITORS
        label, extension_id, project_file = EDITORS["roo"]
        del label
        assert extension_id == "rooveterinaryinc.roo-cline"
        assert project_file == ".roomodes"


class TestClineAndCodexAreNotHarnessMembers:
    def test_cline_is_not_a_harness_member(self) -> None:
        assert "cline" not in {h.value for h in Harness}

    def test_codex_is_not_a_harness_member(self) -> None:
        assert "codex" not in {h.value for h in Harness}


class TestNothingIsWrittenToUnprovenTools:
    def test_installing_every_known_harness_never_touches_cline_or_codex_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from smith import harness

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        smith_home = tmp_path / "smith-home"
        (smith_home / "agents").mkdir(parents=True)
        (smith_home / "agents" / "awino.md").write_text(
            "---\nname: awino\n---\n\nbody", encoding="utf-8"
        )
        (smith_home / "skills").mkdir(parents=True)

        for member in harness.Harness:
            target = harness.Target(member, member.global_root, "global")
            harness.install(smith_home, target, skills=True, overwrite=True)

        assert not (fake_home / ".cline").exists()
        assert not (fake_home / ".codex").exists()
