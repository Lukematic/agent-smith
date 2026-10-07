"""Backup-first, fast-forward-only source update foundation."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from awino.ownership import MANIFEST_NAME
from awino.paths import project_state_dir, user_config_dir

AUTO_FILE = "update.yaml"
AUTO_EVERY_HOURS = 20
FETCH_TIMEOUT_SECONDS = 8


class PreflightError(RuntimeError):
    def __init__(self, message: str, backup: Path):
        super().__init__(message)
        self.backup = backup


def _git(
    source: Path, *args: str, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=source, capture_output=True, text=True, check=False, timeout=timeout
    )


def _copy(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def snapshot(source: Path, project: Path, harness_paths: list[Path]) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = source / "backups" / timestamp
    destination.mkdir(parents=True)
    state = project_state_dir(project)
    project_items = [
        state / "project.yaml",
        state / "memory",
        state / "run",
        state / "state" / "run",
        state / "specs",
        state / "plans",
        project / "specs",
        project / "thoughts" / "plans",
        project / ".seeds",
    ]
    for item in project_items:
        if item.exists():
            _copy(item, destination / "project" / item.relative_to(project))
    for item in harness_paths:
        if item.exists():
            _copy(item, destination / "harness" / item.name)
        manifest = item.parent / MANIFEST_NAME
        if manifest.is_file():
            _copy(manifest, destination / "harness" / MANIFEST_NAME)
    return destination


def update_preflight(source: Path, project: Path, harness_paths: list[Path]) -> Path:
    backup = snapshot(source, project, harness_paths)
    status = _git(source, "status", "--porcelain")
    if status.returncode != 0:
        raise PreflightError(f"git status failed: {status.stderr.strip()}", backup)
    dirty = [line for line in status.stdout.splitlines() if not line.endswith(" backups/")]
    if dirty:
        raise PreflightError("source clone is dirty; refusing fetch/pull", backup)
    fetch = _git(source, "fetch", "--quiet", "origin")
    if fetch.returncode != 0:
        raise PreflightError(f"git fetch failed: {fetch.stderr.strip()}", backup)
    upstream = _git(source, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode != 0:
        raise PreflightError("source branch has no upstream; refusing pull", backup)
    counts = _git(source, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if counts.returncode != 0:
        raise PreflightError("cannot compare source with upstream", backup)
    ahead, behind = (int(value) for value in counts.stdout.split())
    if ahead:
        raise PreflightError("source has diverged/local commits; refusing pull", backup)
    if behind:
        pull = _git(source, "pull", "--quiet", "--ff-only")
        if pull.returncode != 0:
            raise PreflightError(f"fast-forward pull failed: {pull.stderr.strip()}", backup)
    return backup


def cached_freshness(source: Path) -> str:
    """Describe source freshness using only already-local Git metadata.

    Startup must never change the running code or incur a network dependency. This
    intentionally does not call ``git fetch``; ``awino update`` is the explicit
    operation that refreshes remote metadata and may fast-forward the clone.
    """
    inside = _git(source, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return "not a Git checkout; run the installed-package update procedure"
    upstream = _git(source, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode != 0:
        return "no upstream configured; run awino update only after configuring one"
    counts = _git(source, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if counts.returncode != 0:
        return "cached upstream comparison unavailable; run awino update --check"
    ahead, behind = counts.stdout.split()
    return f"cached upstream {upstream.stdout.strip()}: ahead={ahead} behind={behind}"


def restore(backup: Path, project: Path, harness_paths: list[Path]) -> list[Path]:
    """Restore user-owned project and harness files from a preflight snapshot."""
    if not backup.is_dir():
        raise FileNotFoundError(f"backup does not exist: {backup}")
    restored: list[Path] = []
    project_backup = backup / "project"
    if project_backup.is_dir():
        for item in sorted(project_backup.rglob("*")):
            if item.is_file():
                destination = project / item.relative_to(project_backup)
                _copy(item, destination)
                restored.append(destination)
    harness_backup = backup / "harness"
    for destination in harness_paths:
        source = harness_backup / destination.name
        if source.exists():
            _copy(source, destination)
            restored.append(destination)
    return restored


# ── the update check: explicit, or opt-in once a day ────────────────────────


def check_remote(source: Path, timeout: float = FETCH_TIMEOUT_SECONDS) -> tuple[int | None, str]:
    """Fetch the upstream and count commits this clone is behind.

    Returns (behind, detail); behind is None when the check could not run
    (not a clone, no upstream, offline). Only remote-tracking refs change: the
    running code is never touched, which stays ``awino update``'s job.
    """
    inside = _git(source, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None, "not a Git checkout"
    upstream = _git(source, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode != 0:
        return None, "no upstream configured"
    try:
        fetched = _git(source, "fetch", "--quiet", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"no answer from the remote within {timeout:g}s"
    if fetched.returncode != 0:
        return None, "could not reach the remote"
    counts = _git(source, "rev-list", "--count", "HEAD..@{u}")
    if counts.returncode != 0:
        return None, "upstream comparison failed"
    behind = int(counts.stdout.strip() or 0)
    return behind, f"{upstream.stdout.strip()}: behind={behind}"


def _auto_path() -> Path:
    return user_config_dir() / AUTO_FILE


def auto_settings() -> dict:
    path = _auto_path()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, yaml.YAMLError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_auto(data: dict) -> Path:
    path = _auto_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def set_auto(on: bool) -> Path:
    data = auto_settings()
    data["auto_check"] = bool(on)
    return _save_auto(data)


def auto_check(source: Path, now: datetime | None = None) -> str | None:
    """The opt-in daily check that ``awino start`` runs.

    With ``awino update --auto on``, at most once every AUTO_EVERY_HOURS this
    fetches the upstream (time-boxed) and returns a line to show when new
    commits exist. Off by default: startup then stays network-free.
    """
    data = auto_settings()
    if not data.get("auto_check"):
        return None
    now = now or datetime.now(UTC)
    last = data.get("last_check")
    try:
        last_at = datetime.fromisoformat(str(last)) if last else None
    except ValueError:
        last_at = None
    if last_at is not None and (now - last_at).total_seconds() < AUTO_EVERY_HOURS * 3600:
        pending = int(data.get("behind") or 0)
        return _update_line(pending) if pending else None
    behind, _detail = check_remote(source)
    data["last_check"] = now.isoformat(timespec="seconds")
    if behind is not None:
        data["behind"] = behind
    _save_auto(data)
    return _update_line(behind) if behind else None


def _update_line(behind: int) -> str:
    return f"UPDATE AVAILABLE  {behind} new commit(s) upstream; run: awino update"


def clear_pending() -> None:
    """After a successful update nothing is pending until the next check."""
    data = auto_settings()
    if data.get("behind"):
        data["behind"] = 0
        _save_auto(data)
