"""Real regression: check_clone_freshness (health.py) only ever warned about
a stale/behind clone - it never fixed anything, which is exactly why lessons
landed in a stale duplicate clone twice in one real session before a human
noticed. fix_clone_freshness closes that gap for the one case that is safe
to fix mechanically: a clean clone that is only behind its own remote.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from smith.fix import Outcome
from smith.fix import fix_clone_freshness as _fix
from smith.paths import SmithPaths


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _clone_with_remote(tmp_path: Path) -> tuple[Path, SmithPaths]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-b", "main")

    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    _git(origin, "config", "user.email", "test@example.com")
    _git(origin, "config", "user.name", "test")
    (origin / "README.md").write_text("first\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-m", "first")
    _git(origin, "remote", "add", "origin", str(remote))
    _git(origin, "push", "-u", "origin", "main")
    return remote, SmithPaths(root=origin)


class TestFixCloneFreshness:
    def test_already_in_sync_is_skipped_not_falsely_fixed(self, tmp_path: Path) -> None:
        _remote, paths = _clone_with_remote(tmp_path)
        repair = _fix(paths)
        assert repair.outcome is Outcome.SKIPPED

    def test_clean_but_behind_clone_is_actually_pulled(self, tmp_path: Path) -> None:
        remote, paths = _clone_with_remote(tmp_path)
        # A second clone publishes a real new commit to the shared remote.
        other = tmp_path / "other-clone"
        _git(tmp_path, "clone", str(remote), str(other))
        _git(other, "config", "user.email", "test@example.com")
        _git(other, "config", "user.name", "test")
        (other / "second.md").write_text("second\n", encoding="utf-8")
        _git(other, "add", "second.md")
        _git(other, "commit", "-m", "second")
        _git(other, "push", "origin", "main")

        assert not (paths.root / "second.md").exists()
        repair = _fix(paths)

        assert repair.outcome is Outcome.FIXED
        assert (paths.root / "second.md").exists()

    def test_a_local_unpushed_commit_is_reported_manual_not_silently_pulled_over(
        self, tmp_path: Path
    ) -> None:
        _remote, paths = _clone_with_remote(tmp_path)
        (paths.root / "local.md").write_text("local only\n", encoding="utf-8")
        _git(paths.root, "add", "local.md")
        _git(paths.root, "commit", "-m", "local, not pushed")

        repair = _fix(paths)
        assert repair.outcome is Outcome.MANUAL
        assert "not yet pushed" in repair.detail

    def test_uncommitted_tracked_changes_are_reported_manual_not_pulled_over(
        self, tmp_path: Path
    ) -> None:
        _remote, paths = _clone_with_remote(tmp_path)
        (paths.root / "README.md").write_text("dirty edit\n", encoding="utf-8")

        repair = _fix(paths)
        assert repair.outcome is Outcome.MANUAL
        assert "uncommitted" in repair.detail

    def test_non_git_directory_is_skipped_not_an_error(self, tmp_path: Path) -> None:
        paths = SmithPaths(root=tmp_path)
        repair = _fix(paths)
        assert repair.outcome is Outcome.SKIPPED
