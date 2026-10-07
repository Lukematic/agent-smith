"""Staying current: `awino update --check`, the opt-in daily check that a new
session runs, and updates that bring new editor modes along."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from awino import modes, updater

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=GIT_ENV, check=True
    )
    return out.stdout


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    return fake


@pytest.fixture()
def clone(tmp_path: Path) -> tuple[Path, Path]:
    """(an installed clone, a second checkout that can push upstream)."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    (seed / "a.txt").write_text("1\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "one")
    git(seed, "push", "-q", "origin", "HEAD:main")
    installed = tmp_path / "installed"
    git(tmp_path, "clone", "-q", str(origin), str(installed))
    return installed, seed


def _push_new_commit(seed: Path) -> None:
    (seed / "a.txt").write_text("2\n")
    git(seed, "commit", "-qam", "two")
    git(seed, "push", "-q", "origin", "HEAD:main")


def test_check_remote_sees_new_commits_without_changing_code(clone) -> None:
    installed, seed = clone
    assert updater.check_remote(installed)[0] == 0
    _push_new_commit(seed)
    behind, detail = updater.check_remote(installed)
    assert behind == 1 and "behind=1" in detail
    assert (installed / "a.txt").read_text() == "1\n"  # running code untouched


def test_check_remote_explains_what_it_could_not_do(tmp_path: Path) -> None:
    assert updater.check_remote(tmp_path) == (None, "not a Git checkout")


def test_auto_check_is_off_by_default_and_offline(home: Path, clone, monkeypatch) -> None:
    installed, _seed = clone
    monkeypatch.setattr(updater, "check_remote", lambda *_a, **_k: pytest.fail("fetched"))
    assert updater.auto_check(installed) is None


def test_auto_check_runs_once_a_day_and_remembers(home: Path, clone, monkeypatch) -> None:
    installed, seed = clone
    updater.set_auto(True)
    _push_new_commit(seed)
    now = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    line = updater.auto_check(installed, now=now)
    assert line is not None and line.startswith("UPDATE AVAILABLE  1 new commit")

    calls: list[int] = []
    real = updater.check_remote
    monkeypatch.setattr(updater, "check_remote", lambda s: calls.append(1) or real(s))
    # Two hours later: no new fetch, but the waiting update is still announced.
    assert updater.auto_check(installed, now=now + timedelta(hours=2)) == line
    assert calls == []
    # A day later it checks again.
    updater.auto_check(installed, now=now + timedelta(hours=21))
    assert calls == [1]

    updater.clear_pending()
    assert updater.auto_check(installed, now=now + timedelta(hours=22)) is None
    saved = yaml.safe_load((home / ".awino" / "update.yaml").read_text())
    assert saved["auto_check"] is True


def test_auto_check_survives_being_offline(home: Path, tmp_path: Path) -> None:
    updater.set_auto(True)
    assert updater.auto_check(tmp_path) is None  # not a clone: quiet, no crash
    assert updater.auto_settings()["last_check"]


# ── updates bring new modes, only where A.W.I.N.O. modes already live ───────


def _modes_file(project: Path, slugs: list[str]) -> Path:
    path = project / ".kilocodemodes"
    entries = [{"slug": s, "name": s, "roleDefinition": "mine", "groups": ["read"]} for s in slugs]
    path.write_text(yaml.safe_dump({"customModes": entries}), encoding="utf-8")
    return path


def _slugs(path: Path) -> list[str]:
    return [m["slug"] for m in yaml.safe_load(path.read_text())["customModes"]]


def test_update_adds_missing_awino_modes(home: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    path = _modes_file(project, ["mine", "awino", "awino-consult"])
    added = modes.add_missing(Path("/tmp/awino"), project)
    assert "awino-brain" in {slug for _t, slug in added}
    slugs = _slugs(path)
    assert slugs[:3] == ["mine", "awino", "awino-consult"]
    assert "awino-brain" in slugs
    edited = yaml.safe_load(path.read_text())["customModes"][1]
    assert edited["roleDefinition"] == "mine"  # an existing awino mode is never overwritten


def test_update_leaves_mode_files_without_awino_alone(home: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    path = _modes_file(project, ["mine"])
    before = path.read_text()
    assert modes.add_missing(Path("/tmp/awino"), project) == []
    assert path.read_text() == before
