"""951d: a pre-push identity guard. `awino push` refuses when the clone about to
push is not the canonical one - the exact stale-duplicate-clone incident that
landed lessons in a clone nobody was reading."""

from __future__ import annotations

import subprocess
from pathlib import Path

from smith.guard import PushVerdict, check_push_identity


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path, name: str, remote: str) -> Path:
    p = tmp_path / name
    p.mkdir()
    _git(p, "init", "-q", "-b", "main")
    _git(p, "config", "user.email", "t@x")
    _git(p, "config", "user.name", "t")
    _git(p, "remote", "add", "origin", remote)
    (p / "f").write_text("x", encoding="utf-8")
    _git(p, "add", "f")
    _git(p, "commit", "-qm", "init")
    return p


def test_canonical_clone_passes(tmp_path: Path) -> None:
    clone = _repo(tmp_path, "canon", "https://example.invalid/awino.git")
    verdict = check_push_identity(
        clone, canonical_root=clone, canonical_remote="https://example.invalid/awino.git"
    )
    assert verdict.ok


def test_a_different_checkout_of_the_same_remote_is_refused(tmp_path: Path) -> None:
    canon = _repo(tmp_path, "canon", "https://example.invalid/awino.git")
    dupe = _repo(tmp_path, "dupe", "https://example.invalid/awino.git")
    verdict = check_push_identity(
        dupe, canonical_root=canon, canonical_remote="https://example.invalid/awino.git"
    )
    assert not verdict.ok
    assert "canonical" in verdict.reason.lower()
    assert str(canon) in verdict.reason


def test_wrong_remote_is_refused(tmp_path: Path) -> None:
    clone = _repo(tmp_path, "canon", "https://example.invalid/other.git")
    verdict = check_push_identity(
        clone, canonical_root=clone, canonical_remote="https://example.invalid/awino.git"
    )
    assert not verdict.ok
    assert "remote" in verdict.reason.lower()


def test_verdict_names_what_it_checked() -> None:
    v = PushVerdict(ok=True, reason="ok", checked=("root", "remote"))
    assert v.checked == ("root", "remote")


def test_canonical_root_defaults_to_running_clone_until_recorded(tmp_path: Path) -> None:
    from smith.guard import canonical_root_for, record_canonical_root

    home = tmp_path / "clone"
    home.mkdir()
    assert canonical_root_for(home, user_home=tmp_path) == home
    record_canonical_root(home, user_home=tmp_path)
    other = tmp_path / "dupe"
    other.mkdir()
    assert canonical_root_for(other, user_home=tmp_path) == home.resolve()


def test_remote_equality_ignores_userinfo_scheme_and_suffix() -> None:
    from smith.guard import _norm_remote

    a = _norm_remote("https://Lukematic@github.com/Lukematic/agent-smith.git")
    b = _norm_remote("https://github.com/Lukematic/agent-smith")
    c = _norm_remote("git@github.com:Lukematic/agent-smith.git")
    assert a == b == c
