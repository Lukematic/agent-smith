"""Deterministic discovery and routing for skills visible to A.W.I.N.O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_WORDS = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a",
    "into",
    "onto",
    "my",
    "its",
    "an",
    "and",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "when",
    "with",
}


@dataclass(frozen=True)
class Skill:
    """One canonical skill selected according to source precedence."""

    name: str
    description: str
    path: Path
    source: str
    precedence: int


@dataclass(frozen=True)
class Resolution:
    """The canonical result of resolving a requested skill name."""

    skill: Skill
    requested: str
    deprecated_alias: bool


@dataclass(frozen=True)
class Recommendation:
    """An inspectable positive lexical match against a request."""

    skill: Skill
    score: int
    matched_name: tuple[str, ...]
    matched_description: tuple[str, ...]


class SkillCatalog:
    """Canonical skills from project, global, then bundled roots."""

    def __init__(self, project_root: Path, global_root: Path, bundled_root: Path) -> None:
        self.roots = (
            ("project", project_root, 0),
            ("global", global_root, 1),
            ("bundled", bundled_root, 2),
        )
        self._skills = self._discover()

    @property
    def skills(self) -> tuple[Skill, ...]:
        return tuple(sorted(self._skills.values(), key=lambda skill: skill.name))

    def resolve(self, name: str) -> Resolution | None:
        requested = name.strip()
        skill = self._skills.get(requested)
        if skill is not None:
            return Resolution(skill, requested, False)
        if requested.startswith("smith-"):
            canonical = f"awino-{requested.removeprefix('smith-')}"
            skill = self._skills.get(canonical)
            if skill is not None:
                return Resolution(skill, requested, True)
        return None

    def recommend(self, request: str) -> Recommendation | None:
        words = _tokens(request)
        if not words:
            return None
        preferred = _intent_skill(words)
        if preferred is not None and preferred in self._skills:
            skill = self._skills[preferred]
            description_matches = tuple(sorted(words & _tokens(skill.description)))
            return Recommendation(skill, 100, (), description_matches)
        ranked: list[Recommendation] = []
        for skill in self.skills:
            name_matches = tuple(sorted(words & _tokens(skill.name)))
            description_matches = tuple(sorted(words & _tokens(skill.description)))
            score = 3 * len(name_matches) + len(description_matches)
            if score:
                ranked.append(Recommendation(skill, score, name_matches, description_matches))
        if not ranked:
            return None
        return min(
            ranked,
            key=lambda item: (-item.score, item.skill.precedence, item.skill.name),
        )

    def _discover(self) -> dict[str, Skill]:
        discovered: dict[str, Skill] = {}
        for source, root, precedence in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/SKILL.md")):
                skill = _read_skill(path, source, precedence)
                if skill is not None and skill.name not in discovered:
                    discovered[skill.name] = skill

        # A.W.I.N.O. names are canonical. Former Smith names remain resolvable aliases.
        for name in tuple(discovered):
            if name.startswith("smith-") and f"awino-{name.removeprefix('smith-')}" in discovered:
                del discovered[name]
        # Older project installs could place the former persona in the skills
        # directory. A persona is not a routable workflow capability.
        discovered.pop("agent-smith", None)
        return discovered


def _stem(word: str) -> str:
    """Conservative English stemming: plurals and common verb endings only.

    Routing compares a human's words against skill descriptions written by
    someone else; "refactor" must meet "refactors" and "migration" must meet
    "migrations" or ordinary phrasing goes ambiguous. Deliberately shallow - a
    Porter stemmer would merge words that should stay apart.
    """
    if len(word) <= 3:
        return word
    for suffix, replacement in (
        ("ations", "ation"),
        ("ations", "ate"),
        ("ings", ""),
        ("ing", ""),
        ("ies", "y"),
        ("es", "e"),
        ("ss", "ss"),
        ("s", ""),
    ):
        if suffix == "ss":
            if word.endswith("ss"):
                return word
            continue
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)] + replacement
            # "splitting" -> "splitt" -> "split": undo consonant doubling.
            if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiouls":
                stem = stem[:-1]
            return stem
    return word


def _tokens(value: str) -> set[str]:
    return {_stem(word) for word in _WORDS.findall(value.lower()) if word not in _STOP_WORDS}


def _intent_skill(words: set[str]) -> str | None:
    # Compared against stemmed tokens, so listed in stemmed form.
    concrete_failure = {"bug", "error", "exception", "fail", "failure", "pytest"}
    vague_agent = {"agent", "misbehav", "behav", "badly", "keep", "ignor", "wrong"}
    if words & concrete_failure:
        return "awino-debug"
    if "agent" in words and len(words & vague_agent) >= 2:
        return "awino-triage"
    return None


def _read_skill(path: Path, source: str, precedence: int) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return Skill(path.parent.name, "", path.resolve(), source, precedence)
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    metadata = yaml.safe_load(parts[1]) or {}
    name = str(metadata.get("name") or path.parent.name).strip()
    description = str(metadata.get("description") or "").strip()
    if not name:
        return None
    return Skill(name, description, path.resolve(), source, precedence)
