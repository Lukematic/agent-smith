"""Session-scoped record of user asks, instructions, and corrections.

A gate run is not open for most of a real conversation - TaskClass.QUESTION
opens zero gates (CONTRACTS[TaskClass.QUESTION] == ()), so ordinary
clarifying-question exchanges never touch the Ledger, evidence.jsonl, or a
Checkpoint. Everything about that exchange lives only as tokens in the
model's own context window: nothing computes "was this already asked?" or
"does this contradict an earlier instruction?" before the agent acts, and a
long transcript, a topic digression, or compaction is enough to lose it.

This module is the minimum mechanism a three-expert review converged on to
close that gap: a lightweight, always-on, session-scoped log independent of
whether a Ledger run is open, written by the UserPromptSubmit hook (which
already receives the full prompt text and previously discarded it) and
checked by a deterministic lookup rather than trusted to the model's own
attention - the same asymmetry the Ledger already uses elsewhere: the agent
supplies content, the harness computes the answer.

Deliberately NOT a second competing tracker (see the COMPETING_CONTROLLER
lesson): this holds conversational asks/instructions for the current
session only, not tasks, priorities, or dependencies - that remains Seeds'
job.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "do",
        "does",
        "you",
        "i",
        "to",
        "for",
        "of",
        "please",
        "should",
        "want",
        "would",
        "like",
    }
)


def _normalize(text: str) -> str:
    """Lowercase, drop punctuation and stopwords, so near-identical asks
    compare equal even when phrased slightly differently across turns."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    kept = [w for w in words if w not in _STOPWORDS]
    return " ".join(kept)


@dataclass(frozen=True)
class Ask:
    """One user-facing event: a user turn, an agent question, or a correction."""

    turn: int
    ts: str
    kind: str  # "user_turn" | "agent_question" | "correction"
    text: str
    text_norm: str
    run_id: str | None = None
    resolved_by_turn: int | None = None


def log_path(state_root: Path, session_id: str) -> Path:
    return state_root / "session" / f"{session_id}.jsonl"


def _read_all(path: Path) -> list[Ask]:
    if not path.is_file():
        return []
    out: list[Ask] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(Ask(**json.loads(line)))
    return out


def _next_turn(path: Path) -> int:
    existing = _read_all(path)
    return (existing[-1].turn + 1) if existing else 1


def append(
    state_root: Path,
    session_id: str,
    kind: str,
    text: str,
    *,
    run_id: str | None = None,
) -> Ask:
    """Record one user-facing event. Called by the UserPromptSubmit hook for
    every incoming prompt, and by 'awino note' for an explicit correction."""
    path = log_path(state_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    ask = Ask(
        turn=_next_turn(path),
        ts=datetime.now(UTC).isoformat(),
        kind=kind,
        text=text,
        text_norm=_normalize(text),
        run_id=run_id,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(ask)) + "\n")
    return ask


def find_duplicate_question(
    state_root: Path,
    session_id: str,
    text: str,
    *,
    threshold: float = 0.6,
    kind: str = "user_turn",
) -> Ask | None:
    """Has something equivalent already been logged this session as ``kind``?

    Deterministic word-overlap, not embeddings: no extra dependency, and the
    threshold is legible - a reviewer can see exactly why two turns did or
    did not match. ``kind`` selects which prior events count: "user_turn"
    for detecting the human having to repeat themselves (a UserPromptSubmit
    hook can never see what the agent itself asked or said), or
    "agent_question" for detecting the agent about to re-ask its own
    earlier planning question. Returns the earliest matching prior entry,
    or None.
    """
    norm = _normalize(text)
    words = set(norm.split())
    if not words:
        return None
    for ask in _read_all(log_path(state_root, session_id)):
        if ask.kind != kind:
            continue
        other = set(ask.text_norm.split())
        if not other:
            continue
        overlap = len(words & other) / max(len(words), len(other))
        if overlap >= threshold:
            return ask
    return None


def unresolved_questions(state_root: Path, session_id: str) -> list[Ask]:
    """Agent-asked questions with no recorded resolution yet."""
    asks = _read_all(log_path(state_root, session_id))
    return [a for a in asks if a.kind == "agent_question" and a.resolved_by_turn is None]


def corrections(state_root: Path, session_id: str) -> list[Ask]:
    """Corrections the human made this session, most recent first."""
    return list(
        reversed([a for a in _read_all(log_path(state_root, session_id)) if a.kind == "correction"])
    )
