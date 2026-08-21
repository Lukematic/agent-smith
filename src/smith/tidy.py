"""Folder hygiene: find clutter, archive it, never silently delete.

Cleanup is a gate, not a chore. Clutter is measurable: stray docs at the root,
cache files with no provenance, empty directories, files duplicated across two
locations. Each finding is archived to ``archive/YYYY-MM-DD/`` rather than
deleted, because a wrong deletion is unrecoverable and a wrong archive is not.
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from smith.paths import SmithPaths

# Files that belong at the root. Anything else is clutter until proven otherwise.
ROOT_ALLOWED = frozenset(
    {
        "AGENT_SMITH.md",
        "README.md",
        "bootstrap.ps1",
        "bootstrap.sh",
        "install.ps1",
        "install.sh",
        "justfile",
        "plugin.json",
        "pyproject.toml",
        "uv.lock",
        ".gitattributes",
        ".gitignore",
        ".python-version",
    }
)
ROOT_ALLOWED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".ruff_cache",
        ".pytest_cache",
        ".smith",
        "agents",
        "archive",
        "docs",
        "emitted",
        "hooks",
        "knowledge",
        "memory",
        "skills",
        "specs",
        "state",
        "src",
        "templates",
        "tests",
    }
)
# Directories Smith never inspects. The venv is not our clutter, and walking it
# turns a hygiene check into 49 findings of noise, which trains you to ignore it.
IGNORED_TREES = frozenset({".git", ".venv", "node_modules", "archive", "site-packages"})
DISPOSABLE_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache")


def _ignored(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in IGNORED_TREES for part in parts)


class Finding(StrEnum):
    STRAY_ROOT_FILE = "STRAY_ROOT_FILE"
    STRAY_ROOT_DIR = "STRAY_ROOT_DIR"
    ORPHANED_CACHE = "ORPHANED_CACHE"
    EMPTY_DIR = "EMPTY_DIR"
    DUPLICATE_CONTENT = "DUPLICATE_CONTENT"
    DISPOSABLE = "DISPOSABLE"


@dataclass(frozen=True)
class Clutter:
    kind: Finding
    path: Path
    detail: str
    archivable: bool = True


class Tidier:
    """Detects and archives clutter inside a Smith installation."""

    def __init__(self, paths: SmithPaths) -> None:
        self.paths = paths

    def scan(self, orphaned_cache: list[Path] | None = None) -> list[Clutter]:
        found: list[Clutter] = []
        found += self._stray_root()
        found += self._empty_dirs()
        found += self._duplicates()
        found += self._disposable()
        for path in orphaned_cache or []:
            found.append(
                Clutter(
                    Finding.ORPHANED_CACHE,
                    path,
                    "cache file with no manifest entry",
                    archivable=False,
                )
            )
        return found

    def _stray_root(self) -> list[Clutter]:
        out: list[Clutter] = []
        for item in sorted(self.paths.root.iterdir()):
            if item.is_dir():
                if item.name not in ROOT_ALLOWED_DIRS:
                    out.append(
                        Clutter(Finding.STRAY_ROOT_DIR, item, "unexpected directory at root")
                    )
            elif item.name not in ROOT_ALLOWED:
                out.append(
                    Clutter(
                        Finding.STRAY_ROOT_FILE,
                        item,
                        "docs belong in docs/, not at the root",
                    )
                )
        return out

    def _empty_dirs(self) -> list[Clutter]:
        keep = {self.paths.archive, self.paths.emitted, self.paths.specs, self.paths.cache}
        out: list[Clutter] = []
        for directory in sorted(self.paths.root.rglob("*")):
            if not directory.is_dir():
                continue
            if _ignored(directory, self.paths.root):
                continue
            if any(p in DISPOSABLE_DIRS for p in directory.parts):
                continue
            if directory in keep:
                continue
            # A .gitkeep is an explicit statement that an empty directory is intentional.
            if any(p.name in {".gitkeep", ".gitignore"} for p in directory.iterdir()):
                continue
            if not any(directory.iterdir()):
                out.append(
                    Clutter(Finding.EMPTY_DIR, directory, "empty directory", archivable=False)
                )
        return out

    def _duplicates(self) -> list[Clutter]:
        """Identical content in two places means one is stale. Single source of truth."""
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for md in self.paths.root.rglob("*.md"):
            if _ignored(md, self.paths.root) or "cache" in md.parts:
                continue
            digest = hashlib.sha256(md.read_bytes()).hexdigest()
            by_hash[digest].append(md)
        out: list[Clutter] = []
        for group in by_hash.values():
            if len(group) < 2:
                continue
            canonical, *copies = sorted(group, key=lambda p: len(p.parts))
            for copy in copies:
                out.append(
                    Clutter(
                        Finding.DUPLICATE_CONTENT,
                        copy,
                        f"identical to {canonical.relative_to(self.paths.root)}",
                    )
                )
        return out

    def _disposable(self) -> list[Clutter]:
        out: list[Clutter] = []
        for name in DISPOSABLE_DIRS:
            for directory in self.paths.root.rglob(name):
                if directory.is_dir() and not _ignored(directory, self.paths.root):
                    out.append(
                        Clutter(
                            Finding.DISPOSABLE,
                            directory,
                            "regenerated build artifact",
                            archivable=False,
                        )
                    )
        return out

    def archive(self, items: list[Clutter]) -> tuple[Path, list[Path]]:
        """Move archivable clutter into a dated archive directory."""
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        destination = self.paths.archive / stamp
        destination.mkdir(parents=True, exist_ok=True)
        moved: list[Path] = []
        for item in items:
            if not item.archivable or not item.path.exists():
                continue
            target = destination / item.path.name
            counter = 1
            while target.exists():
                target = destination / f"{item.path.stem}-{counter}{item.path.suffix}"
                counter += 1
            shutil.move(str(item.path), str(target))
            moved.append(target)
        if not moved:
            # Do not leave an empty dated directory behind: that is new clutter.
            with contextlib.suppress(OSError):
                destination.rmdir()
        return destination, moved

    def clean(self, items: list[Clutter]) -> list[Path]:
        """Delete only what is provably regenerable."""
        removed: list[Path] = []
        for item in items:
            if item.kind not in {Finding.DISPOSABLE, Finding.ORPHANED_CACHE, Finding.EMPTY_DIR}:
                continue
            if not item.path.exists():
                continue
            if item.path.is_dir():
                shutil.rmtree(item.path, ignore_errors=True)
            else:
                item.path.unlink(missing_ok=True)
            removed.append(item.path)
        return removed

