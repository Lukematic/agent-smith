"""Continuous hygiene ("one clean"): dead code, docs coverage, docs drift.

Two tiers for dead code, because a check that takes minutes does not belong
in the default report:

- fast tier (``buddy check``, ``buddy health``): a static ruff pass for
  unused imports (F401). Seconds, no test run.
- deep tier (``buddy health --deep``): the suite runs under coverage and
  modules/functions with zero executed statements are flagged as dead-code
  candidates. Minutes -- it runs the whole suite.

Docs hygiene is mechanical too: every registered CLI command must be
mentioned in ``docs/`` (coverage), the generated ``docs/commands.md``
reference must match live ``--help`` output (drift), and ``docs/`` must not
invoke commands that do not exist (dead references).

What these checks do NOT catch, stated honestly:

- dynamically dispatched code (``getattr``, entry points, plugin hooks);
- enum members or string vocabularies meant for humans or pluggable
  reviewers -- zero code references is not death
  (e.g. ``ReviewVerdict.BLOCKED`` is reviewer vocabulary);
- ruff has no unreachable-code rule, so the fast tier sees unused imports
  only; unreachable code is the deep tier's job via zero-coverage lines;
- the drift check compares the generated reference against ``--help``
  summaries only: option/flag renames, output-format changes, and behavior
  changes invisible to the one-line summary are not caught, and curated
  prose in hand-written guides is out of scope by design.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

COMMANDS_REFERENCE = "commands.md"


@dataclass(frozen=True)
class HygieneFinding:
    """One hygiene problem: what kind, where, and what it means."""

    kind: str  # "dead_code" | "docs_coverage" | "docs_drift"
    target: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind} {self.target}: {self.detail}"


# ── dead code: fast tier (ruff) ─────────────────────────────────────────────


def _ruff_command() -> list[str] | None:
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [sys.executable, "-m", "ruff"] if probe.returncode == 0 else None


def ruff_diagnostics(root: Path, *, timeout: float = 120.0) -> list[dict] | None:
    """Ruff F401 (unused import) diagnostics for the project, or None.

    None means ruff could not run here (not installed, timed out, crashed):
    the caller reports the pass as skipped, never as clean.
    """
    cmd = _ruff_command()
    if cmd is None:
        return None
    try:
        proc = subprocess.run(
            [*cmd, "check", "--select", "F401", "--output-format", "json", str(root)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 0:
        return []
    if proc.returncode != 1:
        return None
    try:
        data = json.loads(proc.stdout or "[]")
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def dead_code_from_ruff(
    diagnostics: list[dict], root: Path
) -> list[HygieneFinding]:
    """Turn ruff JSON diagnostics into dead-code findings."""
    findings: list[HygieneFinding] = []
    for item in diagnostics:
        if not isinstance(item, dict) or item.get("code") != "F401":
            continue
        location = item.get("location") or {}
        filename = item.get("filename", "")
        try:
            rel = Path(filename).relative_to(root).as_posix()
        except ValueError:
            rel = filename
        row = location.get("row", "?")
        message = str(item.get("message", "unused import")).strip()
        findings.append(
            HygieneFinding("dead_code", f"{rel}:{row}", message)
        )
    return findings


# ── dead code: deep tier (coverage) ─────────────────────────────────────────


def coverage_available() -> bool:
    try:
        import coverage  # noqa: F401

        return True
    except ImportError:
        return False


def deep_source_dir(root: Path) -> Path | None:
    """The package directory the deep tier measures.

    The A.W.I.N.O. repo measures ``src/awino``; other src-layout projects
    measure their first package; anything else is not measurable.
    """
    awino_pkg = root / "src" / "awino"
    if (awino_pkg / "__init__.py").is_file():
        return awino_pkg
    src = root / "src"
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if child.is_dir() and (child / "__init__.py").is_file():
                return child
    return None


def _function_ranges(path: Path) -> list[tuple[str, int, int]]:
    """(qualified name, first line, last line) for every function in the file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []
    out: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                last = max(
                    (n.lineno for n in ast.walk(child) if hasattr(n, "lineno")),
                    default=child.lineno,
                )
                out.append((f"{prefix}{child.name}", child.lineno, last))
                visit(child, f"{prefix}{child.name}.")

    visit(tree, "")
    return out


def dead_from_coverage(
    data: dict, root: Path
) -> list[HygieneFinding]:
    """Flag modules/functions with zero executed statements.

    ``data`` is parsed ``coverage json`` output. A module whose statements
    never executed, and a function none of whose lines executed, are
    dead-code candidates -- candidates, because entry points, plugin hooks,
    and human vocabulary can be intentionally unreferenced.
    """
    findings: list[HygieneFinding] = []
    files = data.get("files", {})
    if not isinstance(files, dict):
        return findings
    for raw_path in sorted(files):
        fdata = files[raw_path]
        if not isinstance(fdata, dict):
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        summary = fdata.get("summary", {})
        if not isinstance(summary, dict):
            continue
        statements = summary.get("num_statements", 0)
        covered = summary.get("covered_lines", 0)
        if not isinstance(statements, int) or statements <= 0:
            continue
        if covered == 0:
            findings.append(
                HygieneFinding(
                    "dead_code",
                    rel,
                    f"module never executed: {statements} statement(s), 0 covered",
                )
            )
            continue
        executed = set(fdata.get("executed_lines", []) or [])
        for name, first, last in _function_ranges(path):
            # The `def` line runs at import time, so it does not count as the
            # function executing: only the body lines do. (A one-line
            # `def f(): ...` cannot be distinguished from its definition, so
            # it is left alone rather than misflagged.)
            body = set(range(first + 1, last + 1)) if last > first else {first}
            if not (body & executed):
                findings.append(
                    HygieneFinding(
                        "dead_code",
                        f"{rel}:{first}",
                        f"function '{name}' never executed",
                    )
                )
    return findings


def run_coverage_deep(
    root: Path, *, timeout: float = 1500.0
) -> tuple[list[HygieneFinding] | None, str]:
    """Run the suite under coverage and flag unexecuted code.

    Returns (findings, note). findings is None when the deep tier cannot
    run here (no coverage package, no measurable source dir, pytest failed
    to start); the note always says what happened.
    """
    if not coverage_available():
        return None, "'coverage' is not installed in this environment"
    source = deep_source_dir(root)
    if source is None:
        return None, "no measurable package directory (expected src/<package>)"
    tmp = Path(tempfile.mkdtemp(prefix="awino-deep-"))
    data_file = tmp / ".coverage"
    json_path = tmp / "coverage.json"
    env = {**os.environ, "COVERAGE_FILE": str(data_file)}
    note = ""
    try:
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--source={source}",
                "-m",
                "pytest",
                "-q",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=root,
            env=env,
        )
        if run.returncode != 0:
            note = (
                f"pytest exited {run.returncode} under coverage; "
                "findings below are from a partial run"
            )
        report = subprocess.run(
            [sys.executable, "-m", "coverage", "json", "-o", str(json_path)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
            env=env,
        )
        if report.returncode != 0 or not json_path.is_file():
            return None, f"coverage report failed: {report.stderr.strip()[:200]}"
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"deep coverage run failed: {exc}"
    except ValueError as exc:
        return None, f"coverage JSON unparseable: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for leftover in root.glob(".coverage*"):
            with contextlib.suppress(OSError):
                leftover.unlink()
    findings = dead_from_coverage(data, root)
    return findings, note or f"measured {source.relative_to(root)}"


# ── docs: coverage ───────────────────────────────────────────────────────────
# A command is documented when docs/ mentions it: an inline code span naming
# it (with or without the `awino ` prefix) or a fenced example invoking it.


def doc_mentions(docs_dir: Path) -> set[str]:
    """Every way docs/ names a command: code spans + fenced `awino ...` lines."""
    mentions: set[str] = set()
    if not docs_dir.is_dir():
        return mentions
    for md in sorted(docs_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for span in re.findall(r"`([^`\n]+)`", text):
            span = span.strip()
            if span:
                mentions.add(span)
        for fence in re.findall(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL):
            for line in fence.splitlines():
                line = line.strip().removeprefix("$ ").strip()
                if line.startswith("awino ") and len(line) > 6:
                    mentions.add(line)
    return mentions


def undocumented_commands(
    commands: list[str], mentions: set[str]
) -> list[str]:
    """Registered commands that no doc mention names."""
    missing: list[str] = []
    for command in commands:
        words = command.split()
        hit = any(
            mention == command
            or mention == f"awino {command}"
            or (len(words) > 1 and command in mention)
            for mention in mentions
        )
        if not hit:
            missing.append(command)
    return missing


_AWINO_INVOKE_RE = re.compile(r"^awino\s+(.+)$")
_PLACEHOLDER_RE = re.compile(r"^<.*>$")


def dead_doc_refs(mentions: set[str], commands: list[str]) -> list[str]:
    """Doc mentions that invoke a command which is not registered.

    Only the leading command path is checked: flags, arguments, and
    placeholders after it are ignored. `awino --help` (no command path)
    is not a reference. A mention naming a real command *group*
    (a strict prefix of registered commands, e.g. `awino buddy` or
    `awino gate plan ...`) is alive: groups are invocable and documented
    as families.
    """
    registered = set(commands)
    dead: list[str] = []
    for mention in sorted(mentions):
        match = _AWINO_INVOKE_RE.match(mention)
        if not match:
            continue
        tokens = [
            token
            for token in match.group(1).split()
            if not token.startswith("-")
            and token not in ("...", "…")
            and not _PLACEHOLDER_RE.match(token)
        ]
        if not tokens:
            continue
        for width in range(len(tokens), 0, -1):
            if " ".join(tokens[:width]) in registered:
                break
        else:
            path = " ".join(tokens)
            if any(cmd.startswith(path + " ") for cmd in registered):
                continue  # a real command group, not a dead reference
            dead.append(mention)
    return dead


# ── docs: the generated command reference + drift ────────────────────────────
# docs/commands.md is generated from live --help output by `buddy --fix`,
# the same pattern as docs/skills.md ("generated, do not edit by hand").
# Drift = the live short help no longer matches the recorded purpose.


def _cell(purpose: str) -> str:
    return re.sub(r"\s+", " ", purpose).strip().replace("|", "\\|")


def _expected_cell(purpose: str) -> str:
    if purpose.strip():
        return _cell(purpose)
    return "(no help text -- describe this command) [DRAFT]"


def render_commands_reference(entries: list[tuple[str, str]]) -> str:
    """Render docs/commands.md from (command, short help) entries."""
    lines = [
        "# Command reference",
        "",
        "Generated by `buddy --fix`. Do not edit by hand: it is regenerated",
        "from live `--help` output. Curated prose lives in `user-guide.md`.",
        "",
        f"{len(entries)} registered command(s).",
        "",
        "| Command | Purpose |",
        "| --- | --- |",
    ]
    for name, purpose in entries:
        cell = _expected_cell(purpose)
        lines.append(f"| `awino {name}` | {cell} |")
    lines += [
        "",
        "Rows marked [DRAFT] need a human: the command has no help text, so",
        "nothing honest could be written for it.",
        "",
    ]
    return "\n".join(lines)


def parse_commands_reference(docs_dir: Path) -> dict[str, str]:
    """The recorded {command: purpose} from docs/commands.md, or {}."""
    path = docs_dir / COMMANDS_REFERENCE
    recorded: dict[str, str] = {}
    if not path.is_file():
        return recorded
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return recorded
    for line in text.splitlines():
        match = re.match(r"^\|\s*`awino ([^`]+)`\s*\|\s*(.*?)\s*\|$", line)
        if match:
            recorded[match.group(1).strip()] = match.group(2).strip()
    return recorded


def reference_drift(
    entries: list[tuple[str, str]], docs_dir: Path
) -> list[HygieneFinding]:
    """Commands whose live help text differs from the generated reference."""
    recorded = parse_commands_reference(docs_dir)
    if not recorded:
        return []  # no reference yet: that is coverage's finding, not drift's
    findings: list[HygieneFinding] = []
    for name, purpose in entries:
        if name not in recorded:
            continue
        if recorded[name] != _expected_cell(purpose):
            findings.append(
                HygieneFinding(
                    "docs_drift",
                    f"awino {name}",
                    "help text changed since docs/commands.md was generated "
                    "(run `buddy --fix` to regenerate)",
                )
            )
    return findings


def write_commands_reference(
    docs_dir: Path, entries: list[tuple[str, str]]
) -> Path:
    """Regenerate docs/commands.md from live --help output."""
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / COMMANDS_REFERENCE
    path.write_text(render_commands_reference(entries), encoding="utf-8")
    return path
