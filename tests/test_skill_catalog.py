from __future__ import annotations

from pathlib import Path

from smith.skill_catalog import SkillCatalog


def write_skill(root: Path, name: str, description: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n",
        encoding="utf-8",
    )
    return path


def test_discovery_precedence_and_canonical_aliases(tmp_path: Path) -> None:
    project, global_, bundled = (tmp_path / name for name in ("project", "global", "bundled"))
    project_path = write_skill(project, "awino-rpi", "project research planning")
    write_skill(global_, "awino-rpi", "global copy")
    write_skill(bundled, "smith-rpi", "legacy copy")
    write_skill(bundled, "awino-rpi", "bundled copy")

    catalog = SkillCatalog(project, global_, bundled)

    assert [skill.name for skill in catalog.skills] == ["awino-rpi"]
    assert catalog.skills[0].path == project_path.resolve()
    alias = catalog.resolve("smith-rpi")
    assert alias is not None
    assert alias.skill.name == "awino-rpi"
    assert alias.deprecated_alias


def test_recommendation_is_positive_deterministic_and_inspectable(tmp_path: Path) -> None:
    roots = [tmp_path / name for name in ("project", "global", "bundled")]
    write_skill(roots[0], "awino-rpi", "research plan implementation for complex changes")
    write_skill(roots[0], "awino-memory", "persist durable knowledge")
    catalog = SkillCatalog(*roots)

    recommendation = catalog.recommend("Please research and plan this complex change")

    assert recommendation is not None
    assert recommendation.skill.name == "awino-rpi"
    assert recommendation.score > 0
    assert "research" in recommendation.matched_description
    assert catalog.recommend("xyzzy plugh") is None
