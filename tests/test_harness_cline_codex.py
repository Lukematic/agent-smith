"""Cline and Codex harness adapters, verified against documented behavior.

Codex (OpenAI Codex CLI): user-level config lives at `~/.codex` with global
AGENTS.md instructions at `~/.codex/AGENTS.md`, and project-level instructions
in `AGENTS.md` at the project root. These are documented Codex behaviors.

Cline (VS Code extension): the documented project-level mechanism is the
`.clinerules` FILE at the project root. No global dot-directory was verified
against a real installation, so `~/.cline` is UNVERIFIED and exists only as a
detection candidate - the tests pin that status rather than blessing the path.

Both are file-at-root mechanisms, so the project target root IS the project
directory itself. The persona write goes through ownership.safe_write, which
refuses to overwrite a non-installer-owned destination: a pre-existing
human-authored AGENTS.md or .clinerules is left untouched and reported as
FAILED, never clobbered. That guarantee is the core of the tests below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith import harness
from smith.harness import Harness, Target

BODY = "---\nname: awino\ndescription: Test persona\n---\n\nBody text here."


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


@pytest.fixture()
def smith_home(tmp_path: Path) -> Path:
    root = tmp_path / "smith-home"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "awino.md").write_text(BODY, encoding="utf-8")
    (root / "skills").mkdir(parents=True)
    return root


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def _persona_action(actions: list[harness.Action], filename: str) -> harness.Action:
    return next(a for a in actions if a.path.name == filename)


class TestCodexAdapter:
    def test_enum_value_and_label(self) -> None:
        assert Harness.CODEX.value == "codex"
        assert Harness.CODEX.label == "OpenAI Codex"

    def test_global_root_is_the_documented_codex_dir(self, fake_home: Path) -> None:
        assert Harness.CODEX.global_root == fake_home / ".codex"

    def test_project_root_is_the_project_dir_itself(self, project: Path) -> None:
        # Codex does not read `.codex/` in a project; its mechanism is the
        # AGENTS.md FILE at the project root.
        assert Harness.CODEX.project_root(project) == project

    def test_persona_is_agents_md_at_the_root(self) -> None:
        assert Harness.CODEX.persona_dir == ""
        assert Harness.CODEX.persona_filename == "AGENTS.md"

    def test_installs_persona_but_has_no_skills_or_plugins(self) -> None:
        assert Harness.CODEX.installs_persona_file is True
        assert Harness.CODEX.supports_skills is False
        assert Harness.CODEX.uses_plugins is False

    def test_persona_is_plain_markdown_without_frontmatter(self, tmp_path: Path) -> None:
        source = tmp_path / "awino.md"
        source.write_text(BODY, encoding="utf-8")
        rendered = harness._persona_for(Harness.CODEX, source)
        assert "Body text here." in rendered
        assert "---\ndescription" not in rendered
        assert rendered.startswith("<!-- installed by A.W.I.N.O.")

    def test_global_and_project_targets_are_detected(
        self, fake_home: Path, project: Path
    ) -> None:
        (fake_home / ".codex").mkdir()
        found = {(t.harness, t.scope): t for t in harness.detected(project)}
        assert (Harness.CODEX, "global") in found
        assert found[(Harness.CODEX, "global")].persona_path == fake_home / ".codex" / "AGENTS.md"
        assert (Harness.CODEX, "project") in found
        assert found[(Harness.CODEX, "project")].persona_path == project / "AGENTS.md"

    def test_install_writes_fresh_global_agents_md(
        self, fake_home: Path, smith_home: Path
    ) -> None:
        (fake_home / ".codex").mkdir()
        target = Target(Harness.CODEX, fake_home / ".codex", "global")
        actions = harness.install(smith_home, target)
        persona = _persona_action(actions, "AGENTS.md")
        assert persona.outcome == "INSTALLED"
        text = (fake_home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
        assert text.startswith("<!-- installed by A.W.I.N.O.")
        assert "Body text here." in text
        # skills are reported skipped, not silently omitted
        skills = [a for a in actions if "skills mechanism" in a.detail]
        assert skills and all(a.outcome == "SKIPPED" for a in skills)
        # reinstall is a no-op
        repeat = harness.install(smith_home, target)
        assert _persona_action(repeat, "AGENTS.md").outcome == "SKIPPED"

    def test_install_refuses_to_clobber_human_agents_md(
        self, project: Path, smith_home: Path
    ) -> None:
        human = project / "AGENTS.md"
        human.write_text("# My project conventions\n\nDo not touch.\n", encoding="utf-8")
        target = Target(Harness.CODEX, project, "project")
        actions = harness.install(smith_home, target)
        persona = _persona_action(actions, "AGENTS.md")
        assert persona.outcome == "FAILED"
        assert human.read_text(encoding="utf-8") == "# My project conventions\n\nDo not touch.\n"


class TestClineAdapter:
    def test_enum_value_and_label(self) -> None:
        assert Harness.CLINE.value == "cline"
        assert Harness.CLINE.label == "Cline"

    def test_global_root_is_unverified(self, fake_home: Path) -> None:
        # UNVERIFIED: no Cline-owned global dot-directory was confirmed
        # against a real installation. This path is only a detection
        # candidate; the test pins the value so a future verification
        # updates it deliberately.
        assert Harness.CLINE.global_root == fake_home / ".cline"

    def test_project_root_is_the_project_dir_itself(self, project: Path) -> None:
        # Cline's mechanism is the `.clinerules` FILE at the project root.
        assert Harness.CLINE.project_root(project) == project

    def test_persona_is_clinerules_at_the_root(self) -> None:
        assert Harness.CLINE.persona_dir == ""
        assert Harness.CLINE.persona_filename == ".clinerules"

    def test_installs_persona_but_has_no_skills_or_plugins(self) -> None:
        assert Harness.CLINE.installs_persona_file is True
        assert Harness.CLINE.supports_skills is False
        assert Harness.CLINE.uses_plugins is False

    def test_persona_is_plain_markdown_without_frontmatter(self, tmp_path: Path) -> None:
        source = tmp_path / "awino.md"
        source.write_text(BODY, encoding="utf-8")
        rendered = harness._persona_for(Harness.CLINE, source)
        assert "Body text here." in rendered
        assert "---\ndescription" not in rendered
        assert rendered.startswith("<!-- installed by A.W.I.N.O.")

    def test_project_target_is_detected(self, project: Path) -> None:
        found = {(t.harness, t.scope): t for t in harness.detected(project)}
        assert (Harness.CLINE, "project") in found
        assert found[(Harness.CLINE, "project")].persona_path == project / ".clinerules"

    def test_install_refuses_to_clobber_human_clinerules(
        self, project: Path, smith_home: Path
    ) -> None:
        human = project / ".clinerules"
        human.write_text("My Cline rules.\n", encoding="utf-8")
        target = Target(Harness.CLINE, project, "project")
        actions = harness.install(smith_home, target)
        persona = _persona_action(actions, ".clinerules")
        assert persona.outcome == "FAILED"
        assert human.read_text(encoding="utf-8") == "My Cline rules.\n"

    def test_install_writes_fresh_clinerules(self, project: Path, smith_home: Path) -> None:
        target = Target(Harness.CLINE, project, "project")
        actions = harness.install(smith_home, target)
        persona = _persona_action(actions, ".clinerules")
        assert persona.outcome == "INSTALLED"
        text = (project / ".clinerules").read_text(encoding="utf-8")
        assert "Body text here." in text


class TestHarnessFlagFilter:
    def test_str_values_match_the_install_harness_flag(self) -> None:
        # install.py filters with `str(t.harness) == which`
        assert str(Harness.CLINE) == "cline"
        assert str(Harness.CODEX) == "codex"

    def test_discover_yields_filterable_cline_and_codex_targets(
        self, fake_home: Path, project: Path
    ) -> None:
        targets = [
            t
            for t in harness.discover(project)
            if t.scope == "project" and str(t.harness) == "codex"
        ]
        assert len(targets) == 1
        assert targets[0].root == project


class TestExistingHarnessesUnaffected:
    def test_original_members_keep_their_properties(self) -> None:
        assert {h.value for h in Harness} == {
            "claude",
            "agents",
            "kilo",
            "cursor",
            "copilot",
            "roo",
            "cline",
            "codex",
        }
        assert Harness.CLAUDE.label == "Claude Code"
        assert Harness.AGENTS.label == "Goose / open agents"
        assert Harness.KILO.label == "Kilo"
        assert Harness.CURSOR.label == "Cursor"
        assert Harness.COPILOT.label == "GitHub Copilot"
        assert Harness.ROO.label == "Roo Code"
        assert Harness.CURSOR.persona_filename == "awino.mdc"
        assert Harness.COPILOT.persona_filename == "awino.chatmode.md"
        assert Harness.ROO.installs_persona_file is False
        assert Harness.CLAUDE.supports_skills is True
        assert Harness.CURSOR.supports_skills is False

    def test_existing_harness_install_still_works(
        self, fake_home: Path, smith_home: Path
    ) -> None:
        claude_root = fake_home / ".claude"
        (claude_root / "agents").mkdir(parents=True)
        target = Target(Harness.CLAUDE, claude_root, "global")
        actions = harness.install(smith_home, target)
        persona = _persona_action(actions, "awino.md")
        assert persona.outcome == "INSTALLED"
        assert "tools:" in (claude_root / "agents" / "awino.md").read_text(encoding="utf-8")

    def test_skill_drift_stays_empty_for_cline_and_codex(
        self, project: Path, smith_home: Path
    ) -> None:
        assert harness.skill_drift(smith_home, Target(Harness.CLINE, project, "project")) == []
        assert harness.skill_drift(smith_home, Target(Harness.CODEX, project, "project")) == []
