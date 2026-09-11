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
