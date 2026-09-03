"""Lesson recall: turn stored lessons into retrieved ones.

`lessons.md` is an episodic store - dated, append-only, one failure per line.
Printing "38 lessons" at startup is storage reporting on itself. Recall is the
other half: given what we are about to do, which two or three lessons were
earned doing something like it? Deterministic token overlap, no model call, so
what surfaces is auditable and the same every time for the same objective.
"""

from __future__ import annotations

import re
from pathlib import Path

_TOKEN = re.compile(r"[a-z][a-z0-9_]{2,}")
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "not",
        "from",
        "for",
        "with",
        "into",
        "onto",
        "this",
        "that",
        "these",
        "those",
        "when",
        "then",
        "than",
        "are",
        "was",
        "were",
        "is",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "should",
        "can",
        "could",
        "may",
        "might",
        "must",
        "its",
        "it's",
        "you",
        "your",
        "our",
        "we",
        "they",
        "them",
        "their",
        "there",
        "here",
        "which",
        "who",
        "what",
        "how",
        "why",
        "all",
        "any",
        "each",
        "one",
        "two",
        "run",
        "add",
        "use",
        "used",
        "using",
        "make",
        "made",
        "get",
        "got",
        "set",
    ]
)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP}


def recall_lessons(lessons_path: Path, objective: str, *, limit: int = 3) -> list[str]:
    """The lessons most relevant to `objective`, best first, at most `limit`."""
    if not lessons_path.is_file():
        return []
    want = _tokens(objective)
    if not want:
        return []
    scored: list[tuple[int, int, str]] = []
    for index, line in enumerate(lessons_path.read_text(encoding="utf-8").splitlines()):
        if not line.startswith("- ["):
            continue
        overlap = len(want & _tokens(line))
        if overlap:
            scored.append((-overlap, -index, line.strip()))
    return [line for _, _, line in sorted(scored)[:limit]]
