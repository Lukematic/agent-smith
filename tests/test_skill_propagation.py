"""Hash-verified skill propagation: prove an installed skill copy matches
source, or say precisely how it does not - never by substring matching.

The regression this guards: the source `awino-self-update` skill legitimately
says "the former .smith/scripts/registry_build.ps1 no longer exists - this
replaced it" as explanatory prose. A grep for that filename matches this
sentence and produces a false positive. Content-hash comparison does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.harness import Harness, Target, refresh_skills, skill_drift


def _make_skill(root: Path, name: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    return skill_dir


@pytest.fixture
def smith_home(tmp_path: Path) -> Path:
    home = tmp_path / "smith-home"
    (home / "skills").mkdir(parents=True)
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "awino.md").write_text("---\nname: awino\n---\n\nbody", encoding="utf-8")
    return home


@pytest.fixture
def target(tmp_path: Path) -> Target:
    root = tmp_path / "installed" / "claude"
    root.mkdir(parents=True)
    return Target(Harness.CLAUDE, root, "project")


class TestFalsePositiveRegression:
    def test_a_matching_copy_mentioning_a_retired_script_name_is_not_flagged(
        self, smith_home: Path, target: Target
    ) -> None:
        body = (
            "# awino-self-update\n\n"
            "`awino drift` is the diff (the former `.smith/scripts/registry_build.ps1` "
            "no longer exists - this replaced it).\n"
        )
        _make_skill(smith_home / "skills", "awino-self-update", body)
        _make_skill(target.skills_root, "awino-self-update", body)

        drift = skill_drift(smith_home, target)

        assert len(drift) == 1
        assert drift[0].skill == "awino-self-update"
        assert drift[0].state == "current"


class TestByteDifferenceIsDrift:
    def test_a_byte_difference_is_reported_as_drifted(
        self, smith_home: Path, target: Target
    ) -> None:
        _make_skill(smith_home / "skills", "awino-consult", "source version\n")
        installed = _make_skill(target.skills_root, "awino-consult", "old version\n")
        from smith import ownership

        ownership.record(target.skills_root, installed, "copy")

        drift = skill_drift(smith_home, target)

        assert len(drift) == 1
        assert drift[0].state == "drifted"


class TestMissingInstalledSkillIsAbsent:
    def test_a_skill_present_in_source_but_missing_here_is_absent(
        self, smith_home: Path, target: Target
    ) -> None:
        _make_skill(smith_home / "skills", "awino-triage", "source content\n")

        drift = skill_drift(smith_home, target)

        assert len(drift) == 1
        assert drift[0].skill == "awino-triage"
        assert drift[0].state == "absent"


class TestRefreshRepairsOnlyInstallerOwnedDrift:
    def test_refresh_updates_a_drifted_installer_owned_copy(
        self, smith_home: Path, target: Target
    ) -> None:
        _make_skill(smith_home / "skills", "awino-consult", "new content\n")
        installed = _make_skill(target.skills_root, "awino-consult", "old content\n")
        from smith import ownership

        ownership.record(target.skills_root, installed, "copy")

        refresh_skills(smith_home, target)

        assert (installed / "SKILL.md").read_text(encoding="utf-8") == "new content\n"
        after = skill_drift(smith_home, target)
        assert after[0].state == "current"

    def test_refresh_preserves_a_human_modified_copy_and_backs_it_up_first(
        self, smith_home: Path, target: Target
    ) -> None:
        _make_skill(smith_home / "skills", "awino-consult", "new content\n")
        installed = _make_skill(target.skills_root, "awino-consult", "original\n")
        from smith import ownership

        ownership.record(target.skills_root, installed, "copy")
        # A human edits the installed copy after installation - unchanged() will
        # now report False against the recorded hash.
        (installed / "SKILL.md").write_text("human edited this\n", encoding="utf-8")

        refresh_skills(smith_home, target)

        assert (installed / "SKILL.md").read_text(encoding="utf-8") == "human edited this\n"
        backups = list(target.skills_root.glob(".awino-backups/**/SKILL.md"))
        assert len(backups) >= 1


class TestRefreshIsIdempotent:
    def test_a_second_refresh_reports_zero_changes(self, smith_home: Path, target: Target) -> None:
        _make_skill(smith_home / "skills", "awino-consult", "content\n")
        installed = _make_skill(target.skills_root, "awino-consult", "old\n")
        from smith import ownership

        ownership.record(target.skills_root, installed, "copy")

        first = refresh_skills(smith_home, target)
        second = refresh_skills(smith_home, target)

        assert any(action.outcome != "SKIPPED" for action in first)
        assert all(action.outcome == "SKIPPED" for action in second)
