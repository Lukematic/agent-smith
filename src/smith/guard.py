"""Pre-push identity guard.

The stale-duplicate-clone incident: a lesson was committed in a clone nobody
reads, twice. `git push` does not care which checkout it runs from; this does.
`awino push` refuses unless the clone about to push *is* the canonical root
(the A.W.I.N.O. home `SmithPaths.discover()` resolves) and its `origin` is the
canonical remote. Both checks are string equality on resolved paths and URLs -
arithmetic, not judgement - so they run before every push and never guess.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

CANONICAL_REMOTE = "https://github.com/Lukematic/agent-smith.git"


@dataclass(frozen=True)
class PushVerdict:
    ok: bool
    reason: str
    checked: tuple[str, ...]


def _origin(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _norm_remote(url: str) -> str:
    """Same repository regardless of userinfo, scheme, trailing slash, or .git."""
    u = url.strip().rstrip("/").removesuffix(".git").lower()
    u = re.sub(r"^[a-z]+://(?:[^@/]+@)?", "", u)
    return re.sub(r"^git@([^:]+):", r"\1/", u)


def check_push_identity(
    root: Path, *, canonical_root: Path, canonical_remote: str = CANONICAL_REMOTE
) -> PushVerdict:
    """Refuse unless `root` is the canonical clone pointing at the canonical remote."""
    checked = ("root", "remote")
    here = root.resolve()
    canon = canonical_root.resolve()
    if here != canon:
        return PushVerdict(
            False,
            f"this checkout is {here}, not the canonical A.W.I.N.O. root {canon}; "
            "pushing from a duplicate clone strands the commit where nobody reads it",
            checked,
        )
    origin = _origin(here)
    if origin is None:
        return PushVerdict(False, "no 'origin' remote configured", checked)
    if _norm_remote(origin) != _norm_remote(canonical_remote):
        return PushVerdict(
            False,
            f"origin remote is {origin}, expected {canonical_remote}",
            checked,
        )
    return PushVerdict(True, f"canonical clone {canon} -> {origin}", checked)


CANONICAL_MARKER = ".canonical-root"


def canonical_root_for(home: Path, user_home: Path | None = None) -> Path:
    """The path this machine has recorded as the one true A.W.I.N.O. clone.

    Recorded once, in the user's profile (not inside any clone, or every clone
    would carry its own claim to be canonical), on the first successful push.
    Until recorded, the running clone is canonical by default.
    """
    base = user_home or Path.home()
    marker = base / ".awino" / CANONICAL_MARKER
    if marker.is_file():
        return Path(marker.read_text(encoding="utf-8").strip())
    return home


def record_canonical_root(home: Path, user_home: Path | None = None) -> Path:
    base = user_home or Path.home()
    marker = base / ".awino" / CANONICAL_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(home.resolve()), encoding="utf-8")
    return marker
