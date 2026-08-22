"""Mission-first onboarding for a project.

The existing CLI exposed good individual commands—``context``, ``mission``,
``doctor``, ``work``, ``plan``—but made a newcomer know the order and interpret
contradictions between them. A sparse research-tool sandbox showed the cost:

- ``mission`` found a stated purpose, but never asked for goals, tenets, users, or
  success criteria;
- ``work`` reported that no tracker existed but did not offer one coherent choice;
- ``plan`` classified a complex MVP as a single prompt and immediately complained
  that no gate existed, before helping the user decide what to build.

Onboarding composes those capabilities into one handshake. It is deliberately
small: detect, reflect, ask the unresolved frontier one question at a time, and
persist only what the user confirms.

Grounding:

- chapters/4-context/3-context-patterns.md — progressively disclose project context.
- chapters/7-patterns/1-plan-build-review.md — understand before planning.
- chapters/9-mental-models/3-specs-as-source-code.md — confirmed intent becomes a
  versioned project artifact rather than evaporating with the conversation.
- chapters/12-long-horizon-agent-state/3-memory-and-intent.md — persistent intent
  is project state, not model memory.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from smith.mission import Confidence, Mission

ONBOARDING_FILE = ".smith/project.yaml"


@dataclass
class ProjectIntent:
    """Human-confirmed project intent, kept local to the project."""

    mission: str
    primary_user: str = ""
    goals: list[str] = field(default_factory=list)
    tenets: list[str] = field(default_factory=list)
    expectations: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    success_metric: str = ""
    confirmed_at: str = ""
    confirmed_by: str = "human"
    source: str = "confirmed"

    @property
    def complete(self) -> bool:
        return bool(
            self.mission
            and self.primary_user
            and self.goals
            and self.tenets
            and self.success_metric
        )

    @property
    def missing(self) -> list[str]:
        out: list[str] = []
        if not self.mission:
            out.append("mission")
        if not self.primary_user:
            out.append("primary_user")
        if not self.goals:
            out.append("goals")
        if not self.tenets:
            out.append("tenets")
        if not self.success_metric:
            out.append("success_metric")
        return out


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    why: str
    suggested: str = ""


QUESTIONS: tuple[Question, ...] = (
    Question(
        "mission",
        "What outcome should this project create for its user?",
        "The mission is the constraint every later architecture decision must serve.",
    ),
    Question(
        "primary_user",
        "Who is the first specific user, and what are they doing today instead?",
        "A tool for everyone is usually calibrated for nobody.",
    ),
    Question(
        "goals",
        "What must the first useful version do? List the smallest outcomes, not features.",
        "Outcome goals prevent framework and feature choices from becoming the plan.",
    ),
    Question(
        "tenets",
        "Which rules must never be violated, even when the happy path is easier?",
        "Tenets become harness gates rather than warnings in a prompt.",
    ),
    Question(
        "expectations",
        "What quality, usability, cost, privacy, or deployment expectations matter?",
        "Operational expectations decide which architecture is actually viable.",
    ),
    Question(
        "success_metric",
        "What observable result would prove the first version is useful?",
        "A project without a success signal cannot know when to stop building.",
    ),
)


def path_for(project: Path) -> Path:
    return project / ONBOARDING_FILE


def load(project: Path) -> ProjectIntent | None:
    path = path_for(project)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ProjectIntent(
        **{k: v for k, v in data.items() if k in ProjectIntent.__dataclass_fields__}
    )


def save(project: Path, intent: ProjectIntent) -> Path:
    path = path_for(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    if intent.source == "confirmed" and not intent.confirmed_at:
        intent.confirmed_at = datetime.now(UTC).isoformat()
    path.write_text(
        yaml.safe_dump(asdict(intent), sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return path


def seed_from_mission(found: Mission) -> ProjectIntent:
    """Use repository evidence as a draft, never as silent confirmation."""
    return ProjectIntent(
        mission=found.statement or "",
        non_goals=list(found.non_goals),
        source=f"drafted from {found.confidence}",
        confirmed_at="",
    )


def frontier(intent: ProjectIntent) -> list[Question]:
    """Questions whose prerequisites are already available.

    Mission comes first. Once it is present, ask the rest one at a time. This keeps
    later questions from hinging on an answer the user has not given yet.
    """
    if not intent.mission:
        return [QUESTIONS[0]]
    by_key = {q.key: q for q in QUESTIONS}
    return [by_key[key] for key in intent.missing if key in by_key]


def apply(intent: ProjectIntent, key: str, value: str) -> ProjectIntent:
    """Apply one user answer without interpreting it beyond list splitting."""
    if key not in ProjectIntent.__dataclass_fields__:
        raise ValueError(f"unknown onboarding field {key!r}")
    if key in {"goals", "tenets", "expectations", "non_goals"}:
        parsed = [part.strip() for part in value.replace("\n", ";").split(";") if part.strip()]
        setattr(intent, key, parsed)
    else:
        setattr(intent, key, value.strip())
    return intent


def as_json(intent: ProjectIntent, questions: list[Question]) -> str:
    return json.dumps(
        {
            "intent": asdict(intent),
            "complete": intent.complete,
            "missing": intent.missing,
            "next_questions": [asdict(question) for question in questions],
        },
        indent=2,
    )


def confirmation_required(found: Mission, intent: ProjectIntent | None) -> bool:
    if intent and intent.source == "confirmed" and intent.confirmed_at:
        return False
    return found.confidence is not Confidence.STATED or not intent or not intent.complete
