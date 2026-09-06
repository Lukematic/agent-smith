from __future__ import annotations

import json
from pathlib import Path

from smith import harness


def _home(tmp_path: Path) -> Path:
    home = tmp_path / ".smith"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "awino.md").write_text(
        "---\nname: awino\ndescription: test persona\n---\nbody\n", encoding="utf-8"
    )
    return home


def test_kilo_repair_creates_default_agent_and_canonical_persona(tmp_path: Path) -> None:
    home = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    actions = harness.repair_kilo_project(home, project)

    config = json.loads((project / ".kilo" / "kilo.json").read_text(encoding="utf-8"))
    assert config["default_agent"] == "awino"
    assert (project / ".kilo" / "agent" / "awino.md").is_file()
    assert not harness.kilo_project_drift(home, project)
    assert all(not action.failed for action in actions)


def test_kilo_repair_skips_incomplete_source_installation(tmp_path: Path) -> None:
    actions = harness.repair_kilo_project(tmp_path / "missing", tmp_path / "project")

    assert actions[0].outcome == "SKIPPED"
    assert not (tmp_path / "project" / ".kilo").exists()


def test_kilo_repair_removes_only_the_managed_legacy_persona(tmp_path: Path) -> None:
    home = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    legacy = project / ".kilo" / "agents" / "awino.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old installer persona\n", encoding="utf-8")
    from smith import ownership

    ownership.record(project / ".kilo", legacy, "persona")

    actions = harness.repair_kilo_project(home, project)

    assert not legacy.exists()
    assert any(action.outcome == "REMOVED" for action in actions)


def test_kilo_repair_preserves_a_human_modified_legacy_persona(tmp_path: Path) -> None:
    home = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    legacy = project / ".kilo" / "agents" / "awino.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("human persona\n", encoding="utf-8")

    actions = harness.repair_kilo_project(home, project)

    assert legacy.read_text(encoding="utf-8") == "human persona\n"
    assert any(action.outcome == "SKIPPED" for action in actions)


def test_kilo_repair_preserves_unrelated_configuration(tmp_path: Path) -> None:
    home = _home(tmp_path)
    project = tmp_path / "project"
    config = project / ".kilo" / "kilo.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"model": "labthing/model", "default_agent": "code"}), encoding="utf-8"
    )

    harness.repair_kilo_project(home, project)

    repaired = json.loads(config.read_text(encoding="utf-8"))
    assert repaired["model"] == "labthing/model"
    assert repaired["default_agent"] == "awino"


def test_kilo_persona_declares_real_startup_boundary(tmp_path: Path) -> None:
    home = _home(tmp_path)
    rendered = harness._persona_for(harness.Harness.KILO, home / "agents" / "awino.md")
    assert "awino start" in rendered
    assert "awino best" in rendered
    assert "already open, human-selected session" in rendered


def test_kilo_target_uses_the_same_canonical_persona_path_as_repair(tmp_path: Path) -> None:
    target = harness.Target(harness.Harness.KILO, tmp_path / ".kilo", "project")

    assert target.persona_path == tmp_path / ".kilo" / "agent" / "awino.md"


def test_cached_freshness_never_fetches(tmp_path: Path, monkeypatch) -> None:
    from smith import updater

    calls: list[tuple[str, ...]] = []

    def fake_git(_source: Path, *args: str):
        calls.append(args)
        if args[:2] == ("rev-parse", "--is-inside-work-tree"):
            return __import__("subprocess").CompletedProcess(args, 0, "true\n", "")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return __import__("subprocess").CompletedProcess(args, 0, "origin/main\n", "")
        return __import__("subprocess").CompletedProcess(args, 0, "0 2\n", "")

    monkeypatch.setattr(updater, "_git", fake_git)

    assert "behind=2" in updater.cached_freshness(tmp_path)
    assert all("fetch" not in call and "pull" not in call for call in calls)
