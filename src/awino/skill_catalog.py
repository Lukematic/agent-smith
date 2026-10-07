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
        preferred = clear_intent(request, set(self._skills)) or _intent_skill(words)
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


@dataclass(frozen=True)
class SkillDoc:
    """A skill's documentation: purpose and when to use it, parsed from SKILL.md.

    ``purpose`` is the frontmatter description. ``when_to_use`` is the
    one-line answer to "when do I reach for this skill", parsed from an
    explicit section or use-line, falling back to the description's first
    sentence. A doc with neither is undocumented -- the registry says so
    and buddy's docs-coverage flags it.
    """

    name: str
    path: Path
    source: str
    purpose: str
    when_to_use: str

    @property
    def documented(self) -> bool:
        return bool(self.purpose or self.when_to_use)


_USE_HEADING_RE = re.compile(r"(?im)^#{1,4}\s*(when to use|use this skill when|usage)\s*$")
_USE_LINE_RE = re.compile(
    r"(?m)^(Use this skill [^.\n]*\.?|Use when [^.\n]*\.?|Use for [^.\n]*\.?)\s*$"
)


def _one_line(text: str, limit: int = 140) -> str:
    line = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def _when_to_use(text: str, purpose: str) -> str:
    body = text.split("---", 2)[2] if text.startswith("---") else text
    heading = _USE_HEADING_RE.search(body)
    if heading:
        for line in body[heading.end() :].splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return _one_line(stripped)
    line = _USE_LINE_RE.search(body)
    if line:
        return _one_line(line.group(1))
    if purpose:
        return _one_line(purpose.split(". ")[0])
    return ""


def describe(skill: Skill) -> SkillDoc:
    """Parse a skill's SKILL.md into its registry entry. Never hardcoded."""
    try:
        text = skill.path.read_text(encoding="utf-8")
    except OSError:
        return SkillDoc(skill.name, skill.path, skill.source, "", "")
    purpose = skill.description.strip()
    return SkillDoc(
        name=skill.name,
        path=skill.path,
        source=skill.source,
        purpose=purpose,
        when_to_use=_when_to_use(text, purpose),
    )


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


# ── intent phrases: how people actually ask for each skill ──────────────────
#
# Word overlap with skill descriptions alone routed 13 of 36 realistic requests
# (tests/test_routing_intents.py); most of the rest came back "ambiguous" and
# stopped the human with a "which one?" question. These phrases are checked on
# the raw request first. A strong phrase (10) settles it unless another
# skill's strong phrase also fires; a weak one (4) only tips the balance.
_STRONG, _WEAK = 10, 4
_ERRORISH = r"\w+(error|exception)|traceback|stack ?trace|segfault|crash(es|ed|ing)?"
INTENT_PHRASES: dict[str, tuple[tuple[str, int], ...]] = {
    "awino-debug": (
        (rf"\b({_ERRORISH})\b", _STRONG),
        (r"\b(bug|failing|fails|failed|broken|regression|not working|doesn'?t work)\b", _STRONG),
        (r"\b(fix|debug|reproduce|flaky)\b", _WEAK),
    ),
    "awino-triage": (
        (
            r"\b((my|the|our|this|an?)\s+(\w+\s+)?(agents?|model|assistant)|claude|kilo|cursor|"
            r"copilot|codex|roo|cline|gemini|gpt)\b.{0,40}\b(keeps?|ignor\w*|hallucinat\w*|"
            r"loops?|looping|stuck|forgets?|wrong|misbehav\w*|refus\w*|skips?|lies|fabricat\w*)",
            _STRONG + 6,
        ),
        (r"\bwhy (does|did|is) (my|the|our) (\w+ )?agent\b", _STRONG + 6),
    ),
    "awino-consult": (
        (
            r"^\s*(what('?s| is| are)|how (should|do|does|can) (i|we|you)|which|why (should|would)|"
            r"when (should|do)|is it better|explain)\b",
            _WEAK,
        ),
        (
            r"\b(harness|context (window|management|engineering)|manage context|prompt caching|"
            r"multi-agent pattern|agent pattern|tool design|subagents? vs|orchestration pattern)\b",
            _STRONG,
        ),
    ),
    "awino-discover": (
        (
            r"\b(i have an idea|an idea for|new (app|product|project|startup) idea|"
            r"(repo|project|folder) is (empty|new)|what (are|should) we (be )?building|"
            r"figure out what we('?re| are) building|not sure what (to build|this project is)|"
            r"requirements for|who is (it|this) for|build something|"
            r"(don'?t|do not) know what (to build|yet|it should be)|not sure what (to build|it should do))\b",
            _STRONG,
        ),
    ),
    "awino-rpi": (
        (
            r"\b(refactor\w*|migrat\w*|restructur\w*|rewrite|re-architect\w*|port (it|this|the)|"
            r"across (all )?(the )?(services|modules|files|codebase|repo)|"
            r"(move|switch|convert) (the |our |this )?[\w-]+( [\w-]+)? (from|to) [\w-]+|"
            r"replace [\w-]+ with [\w-]+)\b",
            _STRONG,
        ),
        (
            r"\b(add|implement|build|introduce)\b.{0,40}\b(endpoint|feature|support|module|api|page)\b",
            _STRONG,
        ),
    ),
    "awino-author-agent": (
        (
            r"\b(build|create|make|write|design|set up|spin up)( me)? (an?|the|new|my|our) "
            r"(\w+ )?(sub-?agent|agent|assistant|bot)\b",
            _STRONG,
        ),
    ),
    "awino-author-tool": (
        (
            r"\b(i need|build|make|create|write)( me)? (an?|the) (\w+ )?(tool|script|hook|"
            r"mcp server|cli command|recipe|linter)\b",
            _STRONG,
        ),
        (
            r"\b(hook or (a )?skill|skill or (a )?hook|should (this|it) be an? (hook|skill|script|tool|mcp))",
            _STRONG,
        ),
    ),
    "awino-memory": (
        (
            r"(^\s*remember\b|\bremember (that|this)\b|\bdon'?t forget\b|\bkeep in mind\b|"
            r"\bnote (that|for later)\b|\bwhat (did|have) we (decide|agree|choose)\w*|"
            r"\bwhat was decided\b|\bwhy did we (choose|decide|pick)\b)",
            _STRONG,
        ),
    ),
    "awino-self-update": (
        (
            r"\b(update yourself|update awino|upgrade (yourself|awino)|refresh (your|the) knowledge|"
            r"self[- ]update|are you up to date|latest version of awino|update the knowledge)\b",
            _STRONG,
        ),
    ),
    "awino-visualize": (
        (
            r"\b(diagram|chart|graph|plot|visuali[sz]\w*|dashboard|schematic|flowchart|"
            r"slide deck|slides|deck|infographic|mermaid)\b",
            _STRONG,
        ),
    ),
    "awino-config-review": (
        (
            r"\b(review|audit|check|sanity[- ]check|look over)\b.{0,50}\b(config\w*|pyproject|toml|"
            r"ci|workflows?|justfile|makefile|lockfile|dependencies|settings|pre-commit)\b",
            _STRONG,
        ),
    ),
    "awino-bootstrap": (
        (
            r"\b(set ?up (this|the|a|my|our)? ?(project|repo|environment|venv|toolchain|dev env\w*)|"
            r"scaffold\w*|bootstrap\w*|initiali[sz]e (the |this )?(project|repo)|create a venv|"
            r"install (the )?dependencies)\b",
            _STRONG,
        ),
    ),
    "awino-evidence": (
        (
            r"\b(evidence|citations?|cite|with sources|literature|peer[- ]reviewed|"
            r"systematic review|meta-analys\w*|what does the research say|papers? on)\b",
            _STRONG,
        ),
    ),
    "awino-reproducibility": (
        (
            r"\b(reproducib\w*|run ids?|snapshots?|provenance|audit trail|"
            r"experiment tracking|rerun\w* (it|the)|data pipeline)\b",
            _STRONG,
        ),
    ),
    "awino-ralph": (
        (
            r"\b(until (all |every |the )*tests? pass\w*|keep (iterating|trying|going|retrying)|"
            r"retry until|loop until|iterate until|until (it|they) (pass|work)|as many attempts)",
            _STRONG + 6,
        ),
    ),
    "awino-delegate": (
        (
            r"\b(in parallel|parallel (agents|workers|workstreams)|split (this|it|the work) "
            r"(across|between|into|among)|fan (it )?out|several agents|multiple agents|"
            r"independent (workstreams|parts|pieces))\b",
            _STRONG + 6,
        ),
    ),
    "awino-brain": (
        (
            r"\b(brainstorm\w*|sponsors?|white ?papers?|proposal|brain mode|problem space|"
            r"new to (this|the) (domain|field|space)|where (i|we) (could|can|might) help|"
            r"decision makers?)\b",
            _STRONG + 6,
        ),
    ),
}
_INTENT_RE = {
    skill: tuple((re.compile(pattern, re.I), weight) for pattern, weight in phrases)
    for skill, phrases in INTENT_PHRASES.items()
}


def intent_scores(request: str) -> dict[str, int]:
    """Phrase scores per canonical skill for a raw request (nonzero only)."""
    text = request or ""
    scores: dict[str, int] = {}
    for skill, phrases in _INTENT_RE.items():
        score = sum(weight for pattern, weight in phrases if pattern.search(text))
        if score:
            scores[skill] = score
    return scores


def clear_intent(request: str, available: set[str] | None = None) -> str | None:
    """The one skill the phrases settle on, or None when nothing strong fires or
    two skills are within a weak phrase of each other."""
    scores = {
        k: v for k, v in intent_scores(request).items() if available is None or k in available
    }
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, score = ranked[0]
    if score < _STRONG:
        return None
    if len(ranked) > 1 and score - ranked[1][1] < _WEAK:
        return None
    return top


def _intent_skill(words: set[str]) -> str | None:
    # Compared against stemmed tokens, so listed in stemmed form.
    concrete_failure = {"bug", "error", "exception", "fail", "failure", "pytest"}
    vague_agent = {"agent", "misbehav", "behav", "badly", "keep", "ignor", "wrong"}
    presentation = {
        "present",
        "presentation",
        "slide",
        "deck",
        "talk",
        "pitch",
        "persuad",
        "persuas",
        "slogan",
        "spis",
    }
    # Brain mode first: "prepare a sponsor presentation" is a thinking job
    # that ends in slides, not a request for a slide.
    brain = {"brain", "brainstorm", "sponsor", "whitepaper", "proposal"}
    if words & brain:
        return "awino-brain"
    if words & presentation:
        return "awino-visualize"
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
