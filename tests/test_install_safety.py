from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from smith.harness import Harness, Target, _link_or_copy, install
from smith.ownership import manifest_path


@pytest.fixture
def smith_home(tmp_path: Path) -> Path:
    home = tmp_path / "source"
    (home / "agents").mkdir(parents=True)
    (home / "skills" / "awino-test").mkdir(parents=True)
    (home / "agents" / "awino.md").write_text("---\nname: awino\n---\nbody\n", encoding="utf-8")
    (home / "skills" / "awino-test" / "SKILL.md").write_text("v1\n", encoding="utf-8")
    return home


def force_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Path, "symlink_to", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, b"", b""),
    )


def test_foreign_real_directory_is_never_deleted(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("new", encoding="utf-8")
    destination = tmp_path / "skill"
    destination.mkdir()
    (destination / "mine.txt").write_text("keep", encoding="utf-8")

    outcome, detail = _link_or_copy(source, destination)

    assert outcome == "FAILED"
    assert "not installer-owned" in detail
    assert (destination / "mine.txt").read_text(encoding="utf-8") == "keep"


def test_unchanged_installer_copy_refreshes_and_repeat_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_copy(monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("v1", encoding="utf-8")
    destination = tmp_path / "skill"
    assert _link_or_copy(source, destination)[0] == "COPIED"
    (source / "SKILL.md").write_text("v2", encoding="utf-8")
    assert _link_or_copy(source, destination)[0] == "COPIED"
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "v2"
    assert _link_or_copy(source, destination)[0] == "SKIPPED"


def test_modified_owned_copy_is_backed_up_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_copy(monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("v1", encoding="utf-8")
    destination = tmp_path / "skill"
    assert _link_or_copy(source, destination)[0] == "COPIED"
    (destination / "SKILL.md").write_text("local", encoding="utf-8")

    outcome, detail = _link_or_copy(source, destination)

    assert outcome == "FAILED"
    backup = Path(detail.split("backup: ", 1)[1])
    assert (backup / "SKILL.md").read_text(encoding="utf-8") == "local"
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "local"


def test_modified_persona_is_backed_up_and_refused(smith_home: Path, tmp_path: Path) -> None:
    target = Target(Harness.CLAUDE, tmp_path / ".claude", "project")
    target.root.mkdir()
    assert not any(action.failed for action in install(smith_home, target, skills=False))
    target.persona_path.write_text("local persona\n", encoding="utf-8")

    actions = install(smith_home, target, skills=False)

    assert actions[0].failed
    backup = Path(actions[0].detail.split("backup: ", 1)[1])
    assert backup.read_text(encoding="utf-8") == "local persona\n"
    assert target.persona_path.read_text(encoding="utf-8") == "local persona\n"


def test_manifest_is_deterministic_and_records_hashes(smith_home: Path, tmp_path: Path) -> None:
    target = Target(Harness.CLAUDE, tmp_path / ".claude", "project")
    target.root.mkdir()
    install(smith_home, target, skills=False)
    first = manifest_path(target.root).read_bytes()
    install(smith_home, target, skills=False)
    second = manifest_path(target.root).read_bytes()
    payload = json.loads(second)

    assert first == second
    assert payload["version"] == 1
    assert payload["entries"]["agents/awino.md"]["sha256"]


def test_exact_link_is_skipped(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"
    try:
        destination.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert _link_or_copy(source, destination)[0] == "SKIPPED"
