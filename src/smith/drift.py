"""Symbol drift: a name that leaves the code but stays in the prose.

Every recurring incident this project logged this week had one shape: an
identifier lived in code, its *name* lived in help text, a rubric, a skill doc,
or a string literal, and each check greped only one side. `ReviewVerdict.BLOCKED`
had zero code references and was deleted; `--verdict` help still advertised it.
The Ralph skill told agents to run a script that never existed.

This reads the diff for removed definitions - `def`, `class`, `CONSTANT =`, and
enum members including their string value - and greps the *string form* of each
across source, tests, docs, skills, agents, and hooks. A hit is a finding with a
file and line. `gate check` reports it; it does not guess which side is right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_DEF = re.compile(r"^-\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
_CONST = re.compile(r"^-\s*([A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=\s*(.*)$")
_STR = re.compile(r"""^["']([A-Za-z0-9_-]{4,})["']\s*$""")
_ADDED_DEF = re.compile(r"^\+\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
_ADDED_CONST = re.compile(r"^\+\s*([A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=")

SCAN_DIRS = ("src", "tests", "docs", "skills", "agents", "hooks")
SCAN_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt"}
_TOO_COMMON = frozenset({"main", "test", "run", "data", "name", "path", "value", "self"})


@dataclass(frozen=True)
class DriftFinding:
    symbol: str
    path: Path
    line: int
    text: str


def removed_symbols(diff: str) -> set[str]:
    """Names defined on a `-` line and not re-defined on a `+` line."""
    removed: set[str] = set()
    added: set[str] = set()
    for raw in diff.splitlines():
        if m := _ADDED_DEF.match(raw):
            added.add(m.group(1))
            continue
        if m := _ADDED_CONST.match(raw):
            added.add(m.group(1))
            continue
        if m := _DEF.match(raw):
            removed.add(m.group(1))
            continue
        if m := _CONST.match(raw):
            removed.add(m.group(1))
            if s := _STR.match(m.group(2).strip()):
                removed.add(s.group(1))
    return {s for s in removed - added if len(s) >= 4 and s.lower() not in _TOO_COMMON}


def _pattern_for(symbol: str) -> re.Pattern[str]:
    """Identifiers match as whole words anywhere. Lower-case string values are
    ordinary English (`blocked`), so they only count when used *as a value*:
    inside a quoted string or backticks, or after a CLI flag - not as a word in
    a running sentence."""
    core = re.escape(symbol)
    if symbol.isupper() or "_" in symbol or symbol[0].isupper():
        return re.compile(r"(?<![\w-])" + core + r"(?![\w-])")
    inside_quotes = r"""["'][^"'\n]*\b""" + core + r"""\b[^"'\n]*["']"""
    backticked = r"`" + core + r"`"
    after_flag = r"--\w+[= ]" + core + r"\b"
    # "one of: approved, changes-requested, blocked" - a value in an enumerated list
    in_list = r"(?:one of|either|values?|options?)[^\n]*\b" + core + r"\b"
    return re.compile("|".join((inside_quotes, backticked, after_flag, in_list)), re.I)


def symbol_drift(diff: str, root: Path) -> list[DriftFinding]:
    """Every surviving reference to a removed symbol, anywhere we ship prose or code."""
    symbols = removed_symbols(diff)
    if not symbols:
        return []
    patterns = {s: _pattern_for(s) for s in symbols}
    findings: list[DriftFinding] = []
    roots = [root / d for d in SCAN_DIRS if (root / d).is_dir()] or [root]
    for base in roots:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in {".venv", "__pycache__", ".git", "node_modules"} for part in path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, text in enumerate(lines, 1):
                for symbol, pattern in patterns.items():
                    if pattern.search(text):
                        findings.append(DriftFinding(symbol, path, number, text.strip()[:140]))
    return findings
