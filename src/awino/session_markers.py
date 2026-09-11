"""Session-end markers: a count of how often the session-end order fired.

``playbook.run_event`` returns text lines and appends no markers to state, so
before this module there was no per-firing record of session-end at all.
``awino best --end`` and buddy's ``--fix`` catch-up each append one JSON line
per firing to ``<state_root>/session_ends.jsonl``; the buddy report counts
those lines, so the number is measured from real state, never claimed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_FILENAME = "session_ends.jsonl"
_STARTS_FILENAME = "session_starts.jsonl"


def _marker_path(state_root: Path) -> Path:
    return state_root / _FILENAME


def record_session_end(state_root: Path) -> Path:
    """Append one session-end marker line; return the marker file's path.

    Each call is one firing of the session-end order, so one call appends
    exactly one line. The directory is created when missing because the
    session-end order itself must be able to run on a fresh state root.
    """
    path = _marker_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(UTC).isoformat(), "event": "session_end"}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return path


def count_session_ends(state_root: Path) -> int:
    """Count recorded session-end markers; 0 when the file is absent.

    A missing file means the order never fired under this module, which is
    a real zero, not an "unmeasured". Non-blank lines are counted as written:
    each line is one recorded firing.
    """
    path = _marker_path(state_root)
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            count += 1
    return count


def last_session_end_time(state_root: Path) -> str | None:
    """ISO timestamp of the most recent session-end marker, or None.

    The checklist's session-end summary measures "what moved this session"
    against this: moves after the previous marker belong to the session that
    just ended. Blank and malformed lines are skipped, never counted.
    """
    path = _marker_path(state_root)
    if not path.is_file():
        return None
    latest: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        at = record.get("at") if isinstance(record, dict) else None
        if isinstance(at, str) and at and (latest is None or at > latest):
            latest = at
    return latest


def record_session_start(state_root: Path) -> Path:
    """Append one session-start marker line; return the marker file's path.

    The operator's unpaused self-action: orienting is read-only, and the one
    write it may perform is this append-only, project-local, reversible
    marker. It lives in its own ``session_starts.jsonl`` so the session-end
    count (which the buddy report measures from ``session_ends.jsonl``) is
    never inflated by orientations.
    """
    path = state_root / _STARTS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(UTC).isoformat(), "event": "session_start"}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return path


def count_session_starts(state_root: Path) -> int:
    """Count recorded session-start markers; 0 when the file is absent."""
    path = state_root / _STARTS_FILENAME
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            count += 1
    return count


def last_session_start_time(state_root: Path) -> str | None:
    """ISO timestamp of the most recent session-start marker, or None."""
    path = state_root / _STARTS_FILENAME
    if not path.is_file():
        return None
    latest: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        at = record.get("at") if isinstance(record, dict) else None
        if isinstance(at, str) and at and (latest is None or at > latest):
            latest = at
    return latest
