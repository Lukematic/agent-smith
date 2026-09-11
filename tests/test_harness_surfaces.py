"""Roo as a probe-verified harness target; Cline/Codex now real members.

An operator with no floor is worse than no elevator: shipping a skills
directory for a tool whose persona location was never proven would satisfy
the letter of "any tool" while breaking the spirit. Roo, Cline, and Codex
are modeled only through mechanisms with documented or probed locations;
anything unverified (Cline's global ``~/.cline``) is marked UNVERIFIED in
harness.py and never presented as proven. File-at-root targets
(``AGENTS.md``, ``.clinerules``) install through ownership.safe_write, which
refuses to overwrite human-authored files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from awino.harness import Harness


def _probe(path: Path) -> bool:
    return path.is_dir()


class TestRooSkillsInstallToTheProbedPath:
    def test_roo_global_root_matches_the_probed_home_directory(self) -> None:
        assert Harness.ROO.global_root == Path.home() / ".roo"

    def test_roo_supports_skills(self) -> None:
        assert Harness.ROO.supports_skills is True

    def test_roo_skills_land_at_the_probed_layout(self, tmp_path: Path) -> None:
        from awino.harness import Target

        target = Target(Harness.ROO, tmp_path / "roo-home", "global")
        assert target.skills_root == tmp_path / "roo-home" / "skills"


class TestRooModeSupportStillResolvesThroughModesPy:
    def test_roo_is_a_known_editor_in_modes_py(self) -> None:
        from awino.modes import EDITORS

        assert "roo" in EDITORS
        label, extension_id, project_file = EDITORS["roo"]
        del label
        assert extension_id == "rooveterinaryinc.roo-cline"
        assert project_file == ".roomodes"


class TestClineAndCodexAreHarnessMembers:
    def test_cline_is_a_harness_member(self) -> None:
        assert "cline" in {h.value for h in Harness}

    def test_codex_is_a_harness_member(self) -> None:
        assert "codex" in {h.value for h in Harness}


class TestUnverifiedLocationsAreMarkedNotAssumed:
    def test_cline_global_path_is_marked_unverified_in_the_module_docs(self) -> None:
        from awino import harness

        doc = harness.__doc__ or ""
        assert "UNVERIFIED" in doc
        # The mark must sit next to the claim it qualifies, not elsewhere.
        cline_pos = doc.find("~/.cline")
        assert cline_pos != -1
        assert "UNVERIFIED" in doc[max(0, cline_pos - 200) : cline_pos + 200]

    def test_install_never_clobbers_a_human_authored_agents_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from awino import harness

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        awino_home = tmp_path / "awino-home"
        (awino_home / "agents").mkdir(parents=True)
        (awino_home / "agents" / "awino.md").write_text(
            "---\nname: awino\n---\n\nbody", encoding="utf-8"
        )
        (awino_home / "skills").mkdir(parents=True)

        project = tmp_path / "proj"
        project.mkdir()
        human_text = "# My instructions\n\nDo not touch.\n"
        (project / "AGENTS.md").write_text(human_text, encoding="utf-8")

        target = harness.Target(harness.Harness.CODEX, project, "project")
        actions = harness.install(awino_home, target, skills=True)
        assert (project / "AGENTS.md").read_text(encoding="utf-8") == human_text
        assert any(a.failed for a in actions)
