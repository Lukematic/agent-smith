"""Stances: how the controller talks to the human.

A stance is not a skill. Skills are procedures a worker follows; a stance is
the controller's conversational posture - advisor, teacher, devil's advocate -
switched by the *human's own words*, not by a name they must remember. That
asymmetry is the point: the human said "I think we should...", so the useful
partner response is the strongest case for the other side, without being asked.

detect() is deterministic keyword matching, not a model call, so switches are
unit-testable and auditable. The caller (persona, `awino start`, the prompt
hook) must never switch silently: whenever detect() returns a stance different
from the current one, it prints one `STANCE -> <name> (<why>)` line.

The default stance persists per project in `.awino/project.yaml` under
`stance:`, so a repo whose owner wants permanent steel-manning gets it in
every session without asking.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from awino.paths import project_state_dir, user_config_dir


@dataclass(frozen=True)
class Stance:
    name: str
    trigger_description: str
    rules: str

    @staticmethod
    def by_name(name: str) -> Stance:
        for stance in STANCES:
            if stance.name == name:
                return stance
        raise ValueError(f"unknown stance: {name}")


STANCES: tuple[Stance, ...] = (
    Stance(
        "advisor",
        "default; always on unless another stance matches",
        (
            "Lead with the uncomfortable truth.\n"
            "Label claims [Certain]/[Likely]/[Guessing].\n"
            "Disagree in three lines: why, the alternative, the risk in their approach.\n"
            "No validation phrases; rewrite instead of agreeing."
        ),
    ),
    Stance(
        "first-principles",
        "the human is decomposing a problem: break down, fundamentals, from scratch",
        (
            "Separate facts from assumptions in a table.\n"
            "Rebuild the problem from only the facts.\n"
            "Name the one assumption most worth challenging."
        ),
    ),
    Stance(
        "steel-man",
        'the human states a position: "I think", "we should", "my plan is"',
        (
            "Make the strongest evidence-backed case for the opposite position.\n"
            "Then name which part of it they should take most seriously.\n"
            "Only after that, give your own view."
        ),
    ),
    Stance(
        "assumption-audit",
        'the human states a conclusion: "so that means", "which implies", "so it\'s"',
        (
            "List every assumption the conclusion needs.\n"
            "Rate each: well-supported / reasonable-unverified / potentially false.\n"
            "For any potentially-false one, state what breaks if it is wrong."
        ),
    ),
    Stance(
        "teach-back",
        'learning language: "teach", "explain to me", "I don\'t understand"',
        (
            "Mental map first: how the concepts connect, what depends on what.\n"
            "Three concrete examples a beginner would recognize.\n"
            "The 20% that carries 80% of the understanding.\n"
            "Then ask them to explain it back, and stop them at the first gap."
        ),
    ),
    Stance(
        "research-intake",
        'research language: "research", "look into", "what do we know about"',
        (
            "No information yet. Five sub-questions first.\n"
            "Flag each: settled vs actively debated.\n"
            "Ask which thread to pull before answering anything."
        ),
    ),
    Stance(
        "expert",
        'lived-experience language: "honestly", "as a human", "how would you"',
        (
            "First-person, lived-experience answer.\n"
            "Mistakes and nuance over textbook structure.\n"
            "One practical example that anchors the point."
        ),
    ),
)

# Order matters: more specific intents are checked before broader ones, so
# "honestly, I think we should..." steel-mans rather than going expert.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "teach-back",
        re.compile(
            r"\b(teach|explain (it|this|to me)|i don'?t understand|walk me through|help me understand)\b",
            re.I,
        ),
    ),
    (
        "research-intake",
        re.compile(r"\b(research|look into|what do we know about|literature)\b", re.I),
    ),
    (
        "first-principles",
        re.compile(
            r"\b(break (this|it) down|first principles?|fundamentals?|from scratch|decompose)\b",
            re.I,
        ),
    ),
    (
        "assumption-audit",
        re.compile(
            r"\b(so that means|which implies|so it'?s|that means the|what am i missing)\b",
            re.I,
        ),
    ),
    (
        "steel-man",
        re.compile(
            r"\b(i think we|we should|my plan is|i believe|i'?m convinced"
            r"|leaning towards?|challenge this|push back on|play devil'?s advocate"
            r"|give me the other side|poke holes in)\b",
            re.I,
        ),
    ),
    (
        "expert",
        re.compile(
            r"\b(honestly|as a human|how would you (handle|deal|feel)|been through)\b", re.I
        ),
    ),
)


def detect(text: str) -> Stance | None:
    """The stance the human's words call for, or None to keep the current one."""
    for name, pattern in _RULES:
        if pattern.search(text):
            return Stance.by_name(name)
    return None


def profile_path() -> Path:
    """Path to the user-level profile; AWINO_PROFILE overrides for tests.

    A pre-rename ``~/.smith/profile.yaml`` is migrated to ``~/.awino/`` on
    first use.
    """
    override = os.environ.get("AWINO_PROFILE")
    return Path(override) if override else user_config_dir() / "profile.yaml"


_CHALLENGE_ME = re.compile(r"^challenge_me:\s*true\s*$", re.M | re.I)


def baseline_stance(profile: Path | None = None) -> str:
    """The baseline stance when no specific stance matches.

    With ``challenge_me: true`` in ~/.awino/profile.yaml the baseline is
    advisor (the one with the disagree-in-three-lines rules); otherwise the
    baseline is "default", i.e. no forced stance and behavior is unchanged.
    """
    path = profile if profile is not None else profile_path()
    if path.is_file():
        if _CHALLENGE_ME.search(path.read_text(encoding="utf-8")):
            return "advisor"
    return "default"


def resolve_stance(prompt: str, current: str | None = None) -> Stance | None:
    """Resolve a prompt to a stance: a specific match wins, else the baseline.

    With ``challenge_me: true`` a neutral prompt resolves to advisor instead
    of None; otherwise None (keep current) is returned exactly as detect()
    would, so callers see no behavior change.
    """
    detected = detect(prompt)
    if detected is not None:
        return detected
    if baseline_stance() == "advisor":
        return Stance.by_name("advisor")
    return Stance.by_name(current) if current else None


_STANCE_LINE = re.compile(r"^stance:\s*(\S+)\s*$", re.M)


def load_default(project: Path) -> str:
    """The project's configured default stance; advisor when unset."""
    config = project_state_dir(project) / "project.yaml"
    if config.is_file():
        match = _STANCE_LINE.search(config.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return "advisor"


def save_default(project: Path, name: str) -> None:
    """Persist the default stance in project.yaml, preserving everything else."""
    Stance.by_name(name)  # raises on unknown
    config = project_state_dir(project) / "project.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    if _STANCE_LINE.search(text):
        text = _STANCE_LINE.sub(f"stance: {name}", text)
    else:
        text = (text.rstrip("\n") + f"\nstance: {name}\n").lstrip("\n")
    config.write_text(text, encoding="utf-8")
