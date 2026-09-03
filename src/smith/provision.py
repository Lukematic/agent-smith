"""Self-provisioning: repair a project's environment loudly, with consent.

The design splits planning from doing, the same way the ledger splits claiming
from verifying:

- ``plan(project)`` is a pure read. It returns typed steps describing what is
  missing and whether fixing it is A.W.I.N.O.'s call (scaffolding its own state
  directory, syncing a declared environment) or a human's call (creating a
  project file in someone's repo, initializing a tracker, pip-installing a
  requirements file).
- ``apply_steps`` executes them: every action prints through its returned
  ``Action`` record, every human decision routes through the ``ask`` callback,
  and command execution goes through an injectable ``run`` so tests never
  depend on the machine's real toolchain.

Nothing here touches ``project.yaml``, ``memory/``, or the run ledger. Those
are project-specific state, preserved across ``awino update`` by
``updater.snapshot``/``restore``; provisioning is the environment half that
snapshot/restore deliberately does not own.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

Ask = Callable[[str], bool]
Run = Callable[[str, Path], tuple[int, str]]


class StepKind(StrEnum):
    SCAFFOLD_STATE = "scaffold-state"
    CREATE_VENV = "create-venv"
    INIT_PROJECT = "init-project"
    INIT_TRACKER = "init-tracker"
    VERIFY_TOOLCHAIN = "verify-toolchain"


@dataclass(frozen=True)
class Step:
    kind: StepKind
    reason: str
    needs_question: bool
    question: str | None = None
    command: str | None = None


@dataclass(frozen=True)
class Action:
    kind: StepKind
    outcome: str  # CREATED | RAN | VERIFIED | DECLINED | FAILED | SKIPPED
    detail: str


def _task_runner_command(project: Path) -> str | None:
    """The project's own test entry point, if one is declared."""
    if (project / "justfile").is_file() or (project / "Justfile").is_file():
        return "just test"
    if (project / "Makefile").is_file() or (project / "makefile").is_file():
        return "make test"
    return None


def plan(project: Path) -> list[Step]:
    """What this project's environment is missing. Pure read, no writes."""
    steps: list[Step] = []

    state_dir = project / ".smith"
    if not ((state_dir / "run").is_dir() and (state_dir / "memory").is_dir()):
        steps.append(
            Step(
                StepKind.SCAFFOLD_STATE,
                "no project state directory; the ledger and lessons need a home",
                needs_question=False,
            )
        )

    has_pyproject = (project / "pyproject.toml").is_file()
    has_requirements = any(project.glob("requirements*.txt"))
    has_venv = (project / ".venv").is_dir()

    if not has_pyproject and not has_requirements:
        steps.append(
            Step(
                StepKind.INIT_PROJECT,
                "no pyproject.toml or requirements file; nothing declares dependencies",
                needs_question=True,
                question="No pyproject.toml here. Run 'uv init' to create one?",
                command="uv init --bare",
            )
        )
    elif not has_venv:
        if has_pyproject:
            steps.append(
                Step(
                    StepKind.CREATE_VENV,
                    "pyproject.toml declares an environment but .venv is missing",
                    needs_question=False,
                    command="uv sync",
                )
            )
        else:
            steps.append(
                Step(
                    StepKind.CREATE_VENV,
                    "requirements file present but no .venv",
                    needs_question=True,
                    question=("Create .venv and pip install -r the requirements file?"),
                    command="uv venv && uv pip install -r requirements.txt",
                )
            )

    if not (project / ".seeds").is_dir():
        steps.append(
            Step(
                StepKind.INIT_TRACKER,
                "no Seeds tracker; work cannot be tracked across sessions",
                needs_question=True,
                question="Init a Seeds tracker here (sd init)?",
                command="sd init",
            )
        )

    runner = _task_runner_command(project)
    if runner is not None:
        steps.append(
            Step(
                StepKind.VERIFY_TOOLCHAIN,
                f"a task runner is declared; prove it fires: {runner}",
                needs_question=False,
                command=runner,
            )
        )

    return steps


def _default_run(command: str, cwd: Path) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
        check=False,
    )
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return completed.returncode, "\n".join(output.splitlines()[-6:])


def apply_steps(
    project: Path,
    steps: list[Step],
    *,
    ask: Ask,
    run: Run = _default_run,
) -> list[Action]:
    """Execute the plan: act, ask, or decline - never silently."""
    actions: list[Action] = []
    for step in steps:
        if step.needs_question:
            assert step.question is not None
            if not ask(step.question):
                actions.append(Action(step.kind, "DECLINED", step.question))
                continue

        if step.kind is StepKind.SCAFFOLD_STATE:
            (project / ".smith" / "run").mkdir(parents=True, exist_ok=True)
            (project / ".smith" / "memory").mkdir(parents=True, exist_ok=True)
            actions.append(Action(step.kind, "CREATED", ".smith/{run,memory}"))
            continue

        assert step.command is not None
        code, output = run(step.command, project)
        if code != 0:
            actions.append(Action(step.kind, "FAILED", f"{step.command}: {output}"))
            continue
        if step.kind is StepKind.VERIFY_TOOLCHAIN:
            actions.append(Action(step.kind, "VERIFIED", step.command))
        elif step.kind is StepKind.CREATE_VENV:
            actions.append(Action(step.kind, "CREATED", ".venv"))
        else:
            actions.append(Action(step.kind, "RAN", step.command))
    return actions
