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

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import yaml

from smith.mission import Confidence, Mission

ONBOARDING_FILE = ".smith/project.yaml"
PROJECT_SCHEMA_VERSION = 2


class EnvironmentDecision(StrEnum):
    USE_EXISTING = "use-existing"
    SETUP = "setup"
    SKIP = "skip"
    NOT_APPLICABLE = "not-applicable"


class TrackerDecision(StrEnum):
    USE_EXISTING = "use-existing"
    INITIALIZE = "initialize"
    SKIP = "skip"


class RunnerDecision(StrEnum):
    USE_DETECTED = "use-detected"
    USE_NATIVE = "use-native"
    DEFER_JUST = "defer-just"
    INSTALL_MISSING = "install-missing"


@dataclass
class BootstrapState:
    """Human-confirmed setup choices for the current project shape."""

    schema_version: int = 1
    environment: str = ""
    tracker: str = ""
    runner: str = ""
    detected_manager: str = ""
    detected_runner: str = ""
    environment_command: str = ""
    tracker_root: str = ""
    fingerprint: str = ""
    confirmed_by: str = ""
    confirmed_at: str = ""


@dataclass
class WorkflowPolicy:
    """Project-specific workflow rules, including mechanically enforceable ones."""

    one_task_per_session: bool = True
    planning_interview: str = "adaptive-grill"
    issue_required: bool = False
    issue_pattern: str = ""
    base_branch: str = ""
    branch_pattern: str = ""
    changelog_file: str = ""


@dataclass
class ProjectIntent:
    """Human-confirmed project intent, kept local to the project."""

    mission: str
    schema_version: int = PROJECT_SCHEMA_VERSION
    primary_user: str = ""
    goals: list[str] = field(default_factory=list)
    tenets: list[str] = field(default_factory=list)
    expectations: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    success_metric: str = ""
    workflow: WorkflowPolicy = field(default_factory=WorkflowPolicy)
    confirmed_at: str = ""
    confirmed_by: str = "human"
    source: str = "confirmed"
    bootstrap: BootstrapState | None = None

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
    values = {k: v for k, v in data.items() if k in ProjectIntent.__dataclass_fields__}
    values.setdefault("schema_version", 1)
    workflow = values.get("workflow") or {}
    if isinstance(workflow, dict):
        values["workflow"] = WorkflowPolicy(
            **{k: v for k, v in workflow.items() if k in WorkflowPolicy.__dataclass_fields__}
        )
    bootstrap = values.get("bootstrap")
    if isinstance(bootstrap, dict):
        values["bootstrap"] = BootstrapState(
            **{k: v for k, v in bootstrap.items() if k in BootstrapState.__dataclass_fields__}
        )
    return ProjectIntent(**values)


def save(project: Path, intent: ProjectIntent) -> Path:
    path = path_for(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    intent.schema_version = PROJECT_SCHEMA_VERSION
    if intent.source == "confirmed" and not intent.confirmed_at:
        intent.confirmed_at = datetime.now(UTC).isoformat()
    path.write_text(
        yaml.safe_dump(asdict(intent), sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return path


FINGERPRINT_FILES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile",
    "Pipfile.lock",
    "environment.yml",
    "environment.yaml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "justfile",
    "Justfile",
    ".justfile",
    "Makefile",
    "makefile",
)


def bootstrap_fingerprint(project: Path) -> str:
    """Fingerprint declaration identities without retaining their contents."""
    declarations = []
    for name in FINGERPRINT_FILES:
        path = project / name
        if path.is_file():
            declarations.append(
                {
                    "name": name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    for name in (".venv", "venv", ".virtualenv", ".seeds"):
        if (project / name).exists():
            declarations.append({"name": name, "present": True})
    encoded = json.dumps(declarations, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def bootstrap_current(project: Path, intent: ProjectIntent | None) -> bool:
    return bool(
        intent
        and intent.bootstrap
        and intent.bootstrap.confirmed_at
        and intent.bootstrap.fingerprint == bootstrap_fingerprint(project)
    )


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


def remember(intent: ProjectIntent, kind: str, value: str) -> ProjectIntent:
    """Store one explicit durable fact in its project-intent field."""
    normalized = kind.strip().lower().replace("_", "-")
    scalar = {
        "mission": "mission",
        "primary-user": "primary_user",
        "success-metric": "success_metric",
    }
    plural = {
        "goal": "goals",
        "tenet": "tenets",
        "expectation": "expectations",
        "non-goal": "non_goals",
    }
    if normalized in scalar:
        setattr(intent, scalar[normalized], value.strip())
        return intent
    if normalized in plural:
        field_name = plural[normalized]
        items = getattr(intent, field_name)
        cleaned = value.strip()
        if cleaned and cleaned not in items:
            items.append(cleaned)
        return intent
    raise ValueError(f"unknown memory kind {kind!r}")


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
