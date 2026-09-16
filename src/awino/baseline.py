"""Capture a repository baseline for A.W.I.N.O. recovery work.

Records, in one JSON document: the baseline commit identity, the working-tree
diff ("unique edits"), per-file hashes of tracked sources ("local hashes"),
packaging manifest diffs (pyproject.toml, uv.lock), and an inventory of any
``.awino/`` / ``.smith/`` state directories present in the tree.

Usage:
    python -m awino.baseline [--out PATH]

With no ``--out``, the JSON goes to stdout. Exit 0 on success; the capture
never fails just because the tree is dirty - a dirty tree is information.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFESTS = ("pyproject.toml", "uv.lock")
STATE_DIR_NAMES = (".awino", ".smith")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_identity() -> dict[str, str]:
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "describe": _git("describe", "--tags", "--always", "--dirty"),
        "remote": _git("config", "--get", "remote.origin.url"),
        "clean": "true" if not _git("status", "--porcelain") else "false",
    }


def _tracked_files() -> list[str]:
    out = _git("ls-files", "-z")
    return sorted(p for p in out.split("\0") if p)


def _file_hashes(files: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in files:
        path = REPO / rel
        if path.is_file():
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _unique_edits() -> dict[str, str]:
    """Working-tree changes vs the baseline commit: the operator's unique edits."""
    return {
        "status_porcelain": _git("status", "--porcelain"),
        "diff_stat": _git("diff", "HEAD", "--stat"),
        "diff": _git("diff", "HEAD"),
        "untracked": _git("ls-files", "--others", "--exclude-standard"),
    }


def _manifest_diffs() -> dict[str, str]:
    return {name: _git("diff", "HEAD", "--", name) for name in MANIFESTS}


def _state_inventory() -> dict[str, list[str]]:
    """Any .awino/ or .smith/ state directories inside the repo tree."""
    found: dict[str, list[str]] = {}
    for name in STATE_DIR_NAMES:
        hits = sorted(
            str(p.relative_to(REPO))
            for p in REPO.rglob(name)
            if p.is_dir() and ".git" not in p.parts
        )
        if hits:
            found[name] = hits
    return found


def capture() -> dict:
    tracked = _tracked_files()
    return {
        "repo": str(REPO),
        "git": _git_identity(),
        "environment": {
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "source_files": len(tracked),
        "file_hashes": _file_hashes(tracked),
        "unique_edits": _unique_edits(),
        "manifest_diffs": _manifest_diffs(),
        "state_inventory": _state_inventory(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="write JSON here")
    args = parser.parse_args(argv)
    document = capture()
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"baseline written to {args.out} ({document['source_files']} files hashed)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
