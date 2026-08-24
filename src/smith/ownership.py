"""Deterministic ownership records for non-destructive harness installation."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

MANIFEST_NAME = ".awino-install-manifest.json"


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_NAME


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load(root: Path) -> dict:
    path = manifest_path(root)
    if not path.is_file():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"version": 1, "entries": {}}
    return data


def entry(root: Path, destination: Path) -> dict | None:
    try:
        key = destination.relative_to(root).as_posix()
    except ValueError:
        return None
    return _load(root)["entries"].get(key)


def record(root: Path, destination: Path, kind: str, content_hash: str | None = None) -> None:
    data = _load(root)
    key = destination.relative_to(root).as_posix()
    data["entries"][key] = {"kind": kind, "sha256": content_hash or sha256_path(destination)}
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def unchanged(root: Path, destination: Path) -> bool:
    owned = entry(root, destination)
    return bool(owned and destination.exists() and owned.get("sha256") == sha256_path(destination))


def backup(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = path.parent / ".awino-backups" / timestamp / path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir() and not path.is_symlink():
        shutil.copytree(path, destination)
    else:
        shutil.copy2(path, destination)
    return destination


def safe_write(root: Path, destination: Path, content: str, kind: str) -> tuple[str, str]:
    encoded_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if destination.exists():
        if sha256_path(destination) == encoded_hash:
            record(root, destination, kind, encoded_hash)
            return "SKIPPED", "already current"
        if not unchanged(root, destination):
            saved = backup(destination)
            return "FAILED", f"destination changed or not installer-owned; backup: {saved}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    record(root, destination, kind, encoded_hash)
    return "INSTALLED", kind
