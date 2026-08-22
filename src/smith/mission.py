"""Mission discovery: read the project's purpose instead of assuming it.

Smith runs in repositories it did not create. Without knowing what a project is
*for*, every judgement it makes is generic: it will suggest tests for a
scratchpad, propose CI for a private notebook, and treat a teaching repository
like a production service.

So mission is read, not assumed. The sources are ordered by how deliberately a
human authored them:

1. **Agent instructions** (`AGENTS.md`, `CLAUDE.md`, `.goosehints`) are written
   *for* an agent, so they are the most direct statement of intent available.
2. **Project metadata** (`description` in `pyproject.toml` or `package.json`) is
   declared and maintained.
3. **README prose** is written for humans and usually accurate.
4. **The tracker** shows what is actually being worked on, which sometimes
   contradicts the stated purpose. That contradiction is information.
5. **Structure** is the weakest signal and the last resort.

What Smith must never do is invent a mission. An agent acting confidently on a
fabricated purpose is worse than one that asks, because the fabrication propagates
into every downstream plan.
"""

from __future__ import annotations

import contextlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

AGENT_INSTRUCTION_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".goosehints",
    ".cursorrules",
)
MISSION_HEADINGS = (
    "mission",
    "purpose",
    "goal",
    "goals",
    "objective",
    "objectives",
    "why",
    "overview",
    "about",
    "what is this",
    "motivation",
)
NON_GOAL_HEADINGS = ("non-goals", "out of scope", "not in scope", "won't do", "will not")


class Kind(StrEnum):
    """What kind of project this is. Changes what good advice looks like."""

    LIBRARY = "library"
    APPLICATION = "application"
    CLI = "cli"
    SERVICE = "service"
    RESEARCH = "research"
    TEACHING = "teaching"
    NOTES = "notes"
    MONOREPO = "monorepo"
    TOOLING = "tooling"
    UNKNOWN = "unknown"

    @property
    def expectations(self) -> str:
        """What is reasonable to demand of this kind of project.

        This is the load-bearing part: holding a notes repository to a library's
        standards produces advice nobody will follow, and a validator nobody
        follows is worse than none.
        """
        return {
            Kind.LIBRARY: "public API stability, tests, and typed interfaces matter most",
            Kind.APPLICATION: "end-to-end behaviour and deployability matter more than API purity",
            Kind.CLI: "argument surface, exit codes, and help text are the contract",
            Kind.SERVICE: "observability, error handling, and rollback matter most",
            Kind.RESEARCH: "reproducibility and provenance matter more than code polish",
            Kind.TEACHING: "clarity and explanation matter more than abstraction",
            Kind.NOTES: "findability matters; do not demand tests or CI",
            Kind.MONOREPO: "per-package boundaries and shared tooling matter most",
            Kind.TOOLING: "the tool must not break the projects that depend on it",
            Kind.UNKNOWN: "ask before assuming what good looks like here",
        }[self]

    @property
    def expects_tests(self) -> bool:
        return self in {Kind.LIBRARY, Kind.APPLICATION, Kind.CLI, Kind.SERVICE, Kind.TOOLING}


class Confidence(StrEnum):
    STATED = "stated"
    DERIVED = "derived"
    GUESSED = "guessed"
    UNKNOWN = "unknown"

    @property
    def trustworthy(self) -> bool:
        return self in {Confidence.STATED, Confidence.DERIVED}


@dataclass(frozen=True)
class Evidence:
    """One piece of support for a mission claim, with its origin."""

    source: str
    text: str

    def __str__(self) -> str:
        return f"{self.source}: {self.text}"


@dataclass
class Mission:
    """What a project is for, and how confident Smith is about that."""

    project: str
    statement: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    kind: Kind = Kind.UNKNOWN
    non_goals: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    agent_instructions: list[str] = field(default_factory=list)
    open_work: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.statement is not None and self.confidence.trustworthy

    @property
    def summary(self) -> str:
        if self.statement:
            return self.statement
        return "unknown: no mission statement found in any authored source"

    def advice(self) -> list[str]:
        """What Smith should do differently given this mission."""
        out: list[str] = []
        if not self.known:
            out.append(
                "ask the human what this project is for before proposing structural change; "
                f"add a Purpose section to a README or {AGENT_INSTRUCTION_FILES[0]} to make it durable"
            )
        if not self.agent_instructions:
            out.append(
                f"no agent instructions found; a short {AGENT_INSTRUCTION_FILES[0]} "
                "is the highest-leverage file for making agent work consistent here"
            )
        out.append(f"calibrate to a {self.kind} project: {self.kind.expectations}")
        if not self.kind.expects_tests:
            out.append("do not demand tests or CI here; gates should stay documentation-shaped")
        if self.non_goals:
            out.append(
                f"respect {len(self.non_goals)} stated non-goal(s): work outside them is scope creep"
            )
        return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _sections(text: str) -> dict[str, str]:
    """Split Markdown into heading -> body, lowercased headings."""
    out: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^#{1,4}\s+(.+?)\s*$", line)
        if heading:
            if current is not None:
                out[current] = "\n".join(buffer).strip()
            current = heading.group(1).strip().lower().rstrip(":")
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        out[current] = "\n".join(buffer).strip()
    return out


def _first_sentences(body: str, limit: int = 2) -> str:
    """Take the opening prose, skipping badges, code, and blank lines."""
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("!", "[!", "```", "|", "<!--", "#", "-", "*", ">")):
            continue
        lines.append(line)
        if len(" ".join(lines)) > 200:
            break
    prose = " ".join(lines)
    if not prose:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", prose)
    return " ".join(parts[:limit]).strip()


def _bullets(body: str) -> list[str]:
    out: list[str] = []
    for raw in body.splitlines():
        item = re.match(r"^\s*[-*+]\s+(.+)$", raw)
        if item:
            cleaned = re.sub(r"[*`_]", "", item.group(1)).strip()
            if cleaned:
                out.append(cleaned)
    return out


def detect_kind(root: Path, text: str) -> tuple[Kind, str]:
    """Classify the project from structure and stated language."""
    lowered = text.lower()

    def has(*names: str) -> bool:
        return any((root / n).exists() for n in names)

    if has("pyproject.toml"):
        try:
            data = tomllib.loads(_read(root / "pyproject.toml"))
        except tomllib.TOMLDecodeError:
            data = {}
        if data.get("project", {}).get("scripts"):
            return Kind.CLI, "pyproject declares console scripts"
    if has("package.json"):
        data = {}
        with contextlib.suppress(json.JSONDecodeError):
            data = json.loads(_read(root / "package.json"))
        if data.get("bin"):
            return Kind.CLI, "package.json declares a bin entry"
        if data.get("workspaces"):
            return Kind.MONOREPO, "package.json declares workspaces"

    if has("Dockerfile", "docker-compose.yml", "helm", "k8s"):
        return Kind.SERVICE, "container or deployment manifests present"
    if has("notebooks") or list(root.glob("*.ipynb")):
        return Kind.RESEARCH, "notebooks present"
    if any(word in lowered for word in ("lesson", "tutorial", "curriculum", "teaching", "course")):
        return Kind.TEACHING, "teaching language in the project description"
    if any(word in lowered for word in ("agent", "harness", "skill", "mcp", "cli tool")):
        return Kind.TOOLING, "the project describes agent or developer tooling"
    if has("src") and has("pyproject.toml", "setup.py"):
        return Kind.LIBRARY, "packaged source layout"
    if has("packages", "apps"):
        return Kind.MONOREPO, "packages or apps directory present"

    markdown = len(list(root.glob("*.md"))) + len(list(root.glob("*/*.md")))
    code = len(list(root.glob("**/*.py"))) + len(list(root.glob("**/*.ts")))
    if markdown > 5 and code < 3:
        return Kind.NOTES, f"{markdown} markdown files and almost no code"
    if code:
        return Kind.APPLICATION, "code present with no packaging or deployment signals"
    return Kind.UNKNOWN, "no clear structural signal"


def discover(root: Path, tracker=None) -> Mission:
    """Read a project's mission from its most deliberately authored sources."""
    mission = Mission(project=root.name)
    corpus: list[str] = []

    # 1. Agent instructions: written for an agent, so the most direct statement.
    for name in AGENT_INSTRUCTION_FILES:
        path = root / name
        if not path.is_file():
            continue
        mission.agent_instructions.append(name)
        text = _read(path)
        corpus.append(text)
        sections = _sections(text)
        for heading in MISSION_HEADINGS:
            if heading in sections:
                statement = _first_sentences(sections[heading])
                if statement and mission.statement is None:
                    mission.statement = statement
                    mission.confidence = Confidence.STATED
                    mission.evidence.append(Evidence(f"{name} #{heading}", statement))
        if mission.statement is None:
            opening = _first_sentences(text)
            if opening:
                mission.statement = opening
                mission.confidence = Confidence.DERIVED
                mission.evidence.append(Evidence(f"{name} opening", opening))
        for heading in NON_GOAL_HEADINGS:
            if heading in sections:
                mission.non_goals.extend(_bullets(sections[heading]))

    # 2. Declared metadata.
    if (root / "pyproject.toml").is_file():
        text = _read(root / "pyproject.toml")
        corpus.append(text)
        try:
            described = tomllib.loads(text).get("project", {}).get("description")
        except tomllib.TOMLDecodeError:
            described = None
        if described:
            mission.evidence.append(Evidence("pyproject description", described))
            if mission.statement is None:
                mission.statement = described
                mission.confidence = Confidence.STATED
    if (root / "package.json").is_file():
        text = _read(root / "package.json")
        corpus.append(text)
        try:
            described = json.loads(text).get("description")
        except json.JSONDecodeError:
            described = None
        if described:
            mission.evidence.append(Evidence("package.json description", described))
            if mission.statement is None:
                mission.statement = described
                mission.confidence = Confidence.STATED

    # 3. README prose.
    readme = root / "README.md"
    if readme.is_file():
        text = _read(readme)
        corpus.append(text)
        sections = _sections(text)
        for heading in MISSION_HEADINGS:
            if heading in sections:
                statement = _first_sentences(sections[heading])
                if statement:
                    mission.evidence.append(Evidence(f"README #{heading}", statement))
                    if mission.statement is None:
                        mission.statement = statement
                        mission.confidence = Confidence.STATED
                    break
        for heading in NON_GOAL_HEADINGS:
            if heading in sections:
                mission.non_goals.extend(_bullets(sections[heading]))
        if mission.statement is None:
            opening = _first_sentences(text)
            if opening:
                mission.statement = opening
                mission.confidence = Confidence.DERIVED
                mission.evidence.append(Evidence("README opening", opening))

    # 4. The tracker: what is actually being worked on right now.
    if tracker is not None:
        try:
            state, _ = tracker.state()
            if state.usable:
                issues = tracker.list_open(limit=20)
                mission.open_work = [issue.title for issue in issues[:8]]
                if mission.open_work and mission.statement is None:
                    # Weakest inference: current work is not a purpose statement,
                    # so it is marked as a guess and never presented as stated.
                    mission.statement = f"inferred from open work: {mission.open_work[0]}"
                    mission.confidence = Confidence.GUESSED
                    mission.evidence.append(Evidence("tracker", f"{len(issues)} open issues"))
        except (OSError, AttributeError):
            pass

    kind, kind_reason = detect_kind(root, " ".join(corpus))
    mission.kind = kind
    mission.evidence.append(Evidence("structure", f"{kind} because {kind_reason}"))

    # Deduplicate non-goals while preserving order.
    mission.non_goals = list(dict.fromkeys(mission.non_goals))
    return mission
