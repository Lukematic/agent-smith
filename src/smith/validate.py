"""Artifact validation for authored skills and agents.

A validator with false positives gets ignored, which is worse than no validator.
So checks that cannot apply report SKIP, advisory findings report WARN, and only
genuine contract violations report FAIL and block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

MIN_BODY_CHARS = 200
MAX_DESCRIPTION_CHARS = 500
AND_SMELL_THRESHOLD = 3
REQUIRED_SECTIONS = ("Failure Modes", "Completion")

# An artifact that authors or analyses orchestrators legitimately needs write
# access. "Is an orchestrator" is not the same as "writes about orchestrators".
META_VERBS = re.compile(
    r"\b(author|creat\w*|emit\w*|generat\w*|scaffold\w*|writ\w*|design\w*|review\w*"
    r"|triag\w*|lint\w*|document\w*|validat\w*|decompos\w*|assess\w*)\b",
    re.IGNORECASE,
)
ORCHESTRATOR_WORDS = re.compile(r"orchestrat|coordinator", re.IGNORECASE)
MUTATION_TOOLS = re.compile(r"\b(Write|Edit|Bash)\b", re.IGNORECASE)
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
CHAPTER_CITE = re.compile(r"chapters/\d+-")
EVIDENCE_WORDS = re.compile(r"\b(paste|output|verif\w*|command)\b", re.IGNORECASE)


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


class Kind(StrEnum):
    AGENT = "agent"
    SKILL = "skill"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass
class Report:
    path: Path
    kind: Kind
    checks: list[Check]

    def count(self, status: Status) -> int:
        return sum(1 for c in self.checks if c.status is status)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.WARN]

    def ok(self, *, strict: bool = False) -> bool:
        if self.failures:
            return False
        return not (strict and self.warnings)


def split_frontmatter(text: str) -> tuple[dict[str, str], str, list[str]]:
    """Return parsed frontmatter, body, and structural problems."""
    lines = text.splitlines()
    problems: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, text, ["frontmatter does not open with --- on line 1"]

    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1)
    if end < 0:
        return {}, text, ["frontmatter never closes"]

    fields: dict[str, str] = {}
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        fields[key.strip()] = value.strip()

    return fields, "\n".join(lines[end + 1 :]), problems


def infer_kind(path: Path, text: str) -> Kind:
    if path.name == "SKILL.md":
        return Kind.SKILL
    parts = {p.lower() for p in path.parts}
    if "agents" in parts:
        return Kind.AGENT
    if re.search(r"^allowed-tools:", text, re.MULTILINE):
        return Kind.SKILL
    return Kind.AGENT


def duplicated_headings(body: str) -> list[str]:
    """Repeated headings signal a document that was never read end to end."""
    headings = [h.strip().lower() for h in re.findall(r"^#{1,4}\s+(.+)$", body, re.MULTILINE)]
    seen: set[str] = set()
    dupes: list[str] = []
    for h in headings:
        if h in seen and h not in dupes:
            dupes.append(h)
        seen.add(h)
    return dupes


def validate_text(path: Path, text: str, kind: Kind | None = None) -> Report:
    resolved = kind or infer_kind(path, text)
    fields, body, structural = split_frontmatter(text)
    checks: list[Check] = []

    def add(name: str, status: Status, detail: str = "") -> None:
        checks.append(Check(name, status, detail))

    def gate(name: str, ok: bool, detail: str) -> None:
        add(name, Status.PASS if ok else Status.FAIL, detail)

    def advise(name: str, ok: bool, detail: str) -> None:
        add(name, Status.PASS if ok else Status.WARN, detail)

    # ── structure ────────────────────────────────────────────────────────────
    gate("frontmatter_valid", not structural, "; ".join(structural) or "opens and closes")

    name = fields.get("name", "")
    gate("has_name", bool(name), name or "name is required")

    description = fields.get("description", "")
    if resolved is Kind.SKILL:
        gate("has_description", bool(description), "skills need a description to be discoverable")
    else:
        advise("has_description", bool(description), "optional for agents but needed to list well")

    tools = fields.get("tools") or fields.get("allowed-tools") or ""
    add(
        "declares_tools",
        Status.PASS if tools else Status.SKIP,
        tools or "no explicit tool list, inherits session tools",
    )

    # ── the silent killer ────────────────────────────────────────────────────
    # A colon inside a description value breaks discovery with no error at all.
    if description:
        gate(
            "no_colon_in_description",
            ":" not in description,
            "colons in a description cause SILENT discovery failure",
        )
        advise(
            "description_length",
            len(description) <= MAX_DESCRIPTION_CHARS,
            f"{len(description)} chars, target under {MAX_DESCRIPTION_CHARS}",
        )
    else:
        add("no_colon_in_description", Status.SKIP, "no description")
        add("description_length", Status.SKIP, "no description")

    # ── naming ───────────────────────────────────────────────────────────────
    if name:
        gate("name_kebab_case", bool(KEBAB.match(name)), name)
        stem = path.stem
        parent = path.parent.name
        located = stem == name or (stem == "SKILL" and parent == name)
        gate("name_matches_location", located, f"name={name} file={stem} dir={parent}")
    else:
        add("name_kebab_case", Status.SKIP, "no name")
        add("name_matches_location", Status.SKIP, "no name")

    # ── least privilege ──────────────────────────────────────────────────────
    identity = f"{name} {description}"
    if not tools:
        add("orchestrator_unarmed", Status.SKIP, "no declared tool list")
    elif not ORCHESTRATOR_WORDS.search(identity):
        add("orchestrator_unarmed", Status.SKIP, "not an orchestrator by identity")
    elif META_VERBS.search(identity):
        add(
            "orchestrator_unarmed",
            Status.SKIP,
            "authors or analyses orchestrators rather than being one",
        )
    else:
        gate(
            "orchestrator_unarmed",
            not MUTATION_TOOLS.search(tools),
            "coordinators must not declare Write, Edit, or Bash",
        )

    # ── single responsibility ────────────────────────────────────────────────
    if description:
        ands = len(re.findall(r"\band\b", description, re.IGNORECASE))
        advise(
            "single_responsibility",
            ands < AND_SMELL_THRESHOLD,
            f"description contains {ands} 'and', consider splitting",
        )
    else:
        add("single_responsibility", Status.SKIP, "no description")

    # ── required sections ────────────────────────────────────────────────────
    for section in REQUIRED_SECTIONS:
        key = f"section_{section.replace(' ', '_').lower()}"
        present = re.search(
            rf"^#{{1,3}}\s*{re.escape(section)}", body, re.MULTILINE | re.IGNORECASE
        )
        gate(key, bool(present), f"## {section} is required")

    # ── grounding and teeth ──────────────────────────────────────────────────
    gate("cites_book", bool(CHAPTER_CITE.search(body)), "must cite at least one chapter path")
    gate(
        "completion_has_evidence",
        bool(EVIDENCE_WORDS.search(body)),
        "completion must demand real evidence",
    )
    gate("body_not_empty", len(body.strip()) > MIN_BODY_CHARS, f"body is {len(body.strip())} chars")

    # ── no duplication ───────────────────────────────────────────────────────
    dupes = duplicated_headings(body)
    advise("no_duplicate_headings", not dupes, ", ".join(dupes) or "headings unique")

    return Report(path=path, kind=resolved, checks=checks)


def validate_file(path: Path, kind: Kind | None = None) -> Report:
    return validate_text(path, path.read_text(encoding="utf-8"), kind)


# Documentation is not an artifact. A directory README describes a folder; it has
# no frontmatter, no failure modes, and no completion protocol, so validating it
# as a skill produces noise that trains the reader to ignore the validator.
NOT_ARTIFACTS = frozenset({"README.md", "CHANGELOG.md", "LICENSE.md", "CONTRIBUTING.md"})


def discover(targets: list[Path]) -> list[Path]:
    """Expand directories into the artifact files inside them."""
    found: list[Path] = []
    for target in targets:
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            for candidate in sorted(target.rglob("*.md")):
                if candidate.name in NOT_ARTIFACTS:
                    continue
                if (
                    candidate.name == "SKILL.md"
                    or "agents" in {p.lower() for p in candidate.parts}
                    or candidate.parent.name == "emitted"
                ):
                    found.append(candidate)
    return found


BROKEN_SELFTEST = """---
name: bad-orchestrator
description: Coordinates work and delegates tasks and manages state: reports results
tools: Read, Write, Edit, Bash
---

Short body.
"""
"""A deliberately broken artifact.

A validator is not verified by artifacts that pass. ``awino validate --selftest``
runs this through and asserts it blocks.
"""
