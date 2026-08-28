"""One-task-per-session state for onboarded projects.

Each session gets its own state file, keyed by session_id
(state_root/session/<id>.json) - mirroring session_log.py's convention.
A small pointer file (state_root/session/.active) records which session_id
is "current" for callers that ask without naming one, such as the
UserPromptSubmit hook. Before this fix, every session shared one
state_root/session.json: a second session with a different id silently
overwrote the first's run_id binding with no record of what was replaced,
and enforce_one_task (the entire reason this file exists) could not
distinguish two genuinely concurrent sessions from one continuing session.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class SessionState:
    session_id: str
    started_at: str
    run_id: str | None = None


def _session_dir(state_root: Path) -> Path:
    return state_root / "session"


def _active_pointer(state_root: Path) -> Path:
    return _session_dir(state_root) / ".active"


def path_for(state_root: Path, session_id: str) -> Path:
    return _session_dir(state_root) / f"{session_id}.json"


def start(state_root: Path, session_id: str) -> SessionState:
    current = load(state_root, session_id)
    if current is not None:
        _set_active(state_root, session_id)
        return current
    state = SessionState(session_id=session_id, started_at=datetime.now(UTC).isoformat())
    _write(state_root, state)
    _set_active(state_root, session_id)
    return state


def _set_active(state_root: Path, session_id: str) -> None:
    pointer = _active_pointer(state_root)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(session_id, encoding="utf-8")


def _active_session_id(state_root: Path) -> str | None:
    pointer = _active_pointer(state_root)
    if not pointer.is_file():
        return None
    value = pointer.read_text(encoding="utf-8").strip()
    return value or None


def load(state_root: Path, session_id: str | None = None) -> SessionState | None:
    """Load one session's state. With no session_id, load whichever session
    most recently called start() in this project - the same convenience the
    single-file design used to provide, now backed by a real per-session
    file instead of one file every session overwrote."""
    resolved = session_id or _active_session_id(state_root)
    if resolved is None:
        return None
    path = path_for(state_root, resolved)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SessionState(**data)


def _write(state_root: Path, state: SessionState) -> None:
    path = path_for(state_root, state.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def bind_run(
    state_root: Path, run_id: str, *, enforce_one_task: bool, session_id: str | None = None
) -> None:
    state = load(state_root, session_id)
    if state is None:
        return
    if enforce_one_task and state.run_id and state.run_id != run_id:
        raise RuntimeError(
            f"session {state.session_id} already served run {state.run_id}; "
            "start a new Claude Code session for the new task"
        )
    state.run_id = run_id
    _write(state_root, state)
