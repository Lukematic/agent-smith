"""One-task-per-session state for onboarded projects."""

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


def path_for(state_root: Path) -> Path:
    return state_root / "session.json"


def start(state_root: Path, session_id: str) -> SessionState:
    path = path_for(state_root)
    current = load(state_root)
    if current and current.session_id == session_id:
        return current
    state = SessionState(session_id=session_id, started_at=datetime.now(UTC).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    return state


def load(state_root: Path) -> SessionState | None:
    path = path_for(state_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SessionState(**data)


def bind_run(state_root: Path, run_id: str, *, enforce_one_task: bool) -> None:
    state = load(state_root)
    if state is None:
        return
    if enforce_one_task and state.run_id and state.run_id != run_id:
        raise RuntimeError(
            f"session {state.session_id} already served run {state.run_id}; "
            "start a new Claude Code session for the new task"
        )
    state.run_id = run_id
    path_for(state_root).write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
