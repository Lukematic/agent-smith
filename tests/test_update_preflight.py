from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from awino.updater import PreflightError, restore, update_preflight


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    source = tmp_path / "source"
    git(tmp_path, "clone", str(remote), str(source))
    git(source, "config", "user.email", "test@example.test")
    git(source, "config", "user.name", "Test")
    (source / "plugin.json").write_text("{}", encoding="utf-8")
    (source / "knowledge").mkdir()
    (source / "knowledge" / "REGISTRY.yaml").write_text("chapters: []\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    git(source, "push", "-u", "origin", "HEAD")
    return source, remote


def test_dirty_clone_refuses_before_pull_but_writes_backup(tmp_path: Path) -> None:
    source, _ = repo(tmp_path)
    project = tmp_path / "project"
    (project / ".awino" / "memory").mkdir(parents=True)
    (project / ".awino" / "project.yaml").write_text("name: mine\n", encoding="utf-8")
    (project / ".awino" / "memory" / "facts.md").write_text("fact\n", encoding="utf-8")
    (project / ".seeds").mkdir()
    (project / ".seeds" / "issues.jsonl").write_text('{"id":"6303"}\n', encoding="utf-8")
    (source / "local.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(PreflightError) as raised:
        update_preflight(source, project, harness_paths=[])

    backup = raised.value.backup
    assert (backup / "project" / ".awino" / "project.yaml").is_file()
    assert (backup / "project" / ".awino" / "memory" / "facts.md").is_file()
    assert (backup / "project" / ".seeds" / "issues.jsonl").is_file()


def test_clean_clone_fetches_and_fast_forwards(tmp_path: Path) -> None:
    source, remote = repo(tmp_path)
    other = tmp_path / "other"
    git(tmp_path, "clone", str(remote), str(other))
    git(other, "config", "user.email", "test@example.test")
    git(other, "config", "user.name", "Test")
    (other / "upstream.txt").write_text("new\n", encoding="utf-8")
    git(other, "add", ".")
    git(other, "commit", "-m", "upstream")
    git(other, "push")

    backup = update_preflight(source, tmp_path / "project", harness_paths=[])

    assert backup.is_dir()
    assert (source / "upstream.txt").read_text(encoding="utf-8") == "new\n"


def test_backup_includes_harness_config_and_manifest(tmp_path: Path) -> None:
    source, _ = repo(tmp_path)
    harness = tmp_path / "custom_modes.yaml"
    harness.write_text("# keep\ncustomModes: []\n", encoding="utf-8")
    manifest = harness.parent / ".awino-install-manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    backup = update_preflight(source, tmp_path / "project", harness_paths=[harness])

    assert (
        (backup / "harness" / "custom_modes.yaml").read_text(encoding="utf-8").startswith("# keep")
    )
    assert (backup / "harness" / ".awino-install-manifest.json").is_file()


def test_local_commit_refuses_pull_as_diverged(tmp_path: Path) -> None:
    source, _ = repo(tmp_path)
    (source / "local.txt").write_text("local\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "-m", "local")

    with pytest.raises(PreflightError, match="diverged/local commits"):
        update_preflight(source, tmp_path / "project", harness_paths=[])


def test_restore_recovers_project_and_harness_state(tmp_path: Path) -> None:
    source, _ = repo(tmp_path)
    project = tmp_path / "project"
    memory = project / ".awino" / "memory"
    memory.mkdir(parents=True)
    lesson = memory / "lessons.md"
    lesson.write_text("before\n", encoding="utf-8")
    harness = tmp_path / "custom_modes.yaml"
    harness.write_text("before\n", encoding="utf-8")
    backup = update_preflight(source, project, harness_paths=[harness])
    lesson.write_text("after\n", encoding="utf-8")
    harness.write_text("after\n", encoding="utf-8")

    restored = restore(backup, project, harness_paths=[harness])

    assert restored == [lesson, harness]
    assert lesson.read_text(encoding="utf-8") == "before\n"
    assert harness.read_text(encoding="utf-8") == "before\n"


# ── a clone with local changes is never a dead end ──────────────────────────


def _push_upstream_change(tmp_path: Path, remote: Path, name: str, text: str) -> None:
    other = tmp_path / "other"
    git(tmp_path, "clone", str(remote), str(other))
    git(other, "config", "user.email", "o@example.test")
    git(other, "config", "user.name", "Other")
    (other / name).write_text(text, encoding="utf-8")
    git(other, "add", ".")
    git(other, "commit", "-m", "upstream change")
    git(other, "push")


def test_dirty_refusal_names_the_files_and_the_way_out(tmp_path: Path) -> None:
    source, _ = repo(tmp_path)
    (source / "plugin.json").write_text('{"edited": true}', encoding="utf-8")
    (source / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(PreflightError) as raised:
        update_preflight(source, tmp_path / "project", harness_paths=[])
    message = str(raised.value)
    assert "dirty" in message and "plugin.json" in message and "notes.txt" in message
    assert "awino update --keep-local" in message


def test_keep_local_updates_and_puts_changes_back(tmp_path: Path) -> None:
    source, remote = repo(tmp_path)
    _push_upstream_change(tmp_path, remote, "NEW.md", "from upstream\n")
    (source / "plugin.json").write_text('{"edited": true}', encoding="utf-8")
    (source / "notes.txt").write_text("mine", encoding="utf-8")
    notes: list[str] = []
    update_preflight(source, tmp_path / "project", [], keep_local=True, report=notes)
    assert (source / "NEW.md").read_text(encoding="utf-8") == "from upstream\n"
    assert (source / "plugin.json").read_text(encoding="utf-8") == '{"edited": true}'
    assert (source / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert any(n.startswith("RESTORED") for n in notes)


def test_keep_local_conflict_updates_and_keeps_changes_in_the_stash(tmp_path: Path) -> None:
    source, remote = repo(tmp_path)
    _push_upstream_change(tmp_path, remote, "plugin.json", '{"upstream": true}')
    (source / "plugin.json").write_text('{"mine": true}', encoding="utf-8")
    notes: list[str] = []
    update_preflight(source, tmp_path / "project", [], keep_local=True, report=notes)
    assert (source / "plugin.json").read_text(encoding="utf-8") == '{"upstream": true}'
    assert any(n.startswith("KEPT_ASIDE") and "plugin.json" in n for n in notes)
    stashed = subprocess.run(
        ["git", "stash", "show", "-p"], cwd=source, capture_output=True, text=True, check=True
    ).stdout
    assert '{"mine": true}' in stashed
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=source, capture_output=True, text=True, check=True
    ).stdout
    assert status.strip() == ""  # clean, on the new version
