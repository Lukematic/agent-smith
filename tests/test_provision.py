"""Self-provisioning: drop A.W.I.N.O. into any repo and it repairs the
environment loudly - one printed line per action, one question per decision a
human must make, and nothing silent anywhere.

The rebase analogy the human asked for: `awino update` already snapshots
project-specific state (mission, lessons, ledger) and restores it after the
pull. Provisioning is the missing second half - after restore, re-create
whatever the environment lost (.venv, project file, tracker) and prove the
toolchain still fires.

plan() is pure: it reads the project and returns typed steps. apply() executes
them, routing every step that needs a human decision through the ask callback
instead of guessing. That split is what makes the behavior testable without a
terminal and honest with one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.provision import (
    Action,
    Step,
    StepKind,
    apply_steps,
    plan,
)


def _bare(tmp_path: Path) -> Path:
    project = tmp_path / "bare"
    (project / ".git").mkdir(parents=True)
    return project


def _with_pyproject(tmp_path: Path) -> Path:
    project = _bare(tmp_path)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "field"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return project


class TestPlanIsPureAndComplete:
    def test_a_bare_repo_plans_scaffold_project_file_question_and_tracker_question(
        self, tmp_path: Path
    ) -> None:
        project = _bare(tmp_path)
        steps = plan(project)
        kinds = [s.kind for s in steps]
        assert StepKind.SCAFFOLD_STATE in kinds
        assert StepKind.INIT_PROJECT in kinds
        assert StepKind.INIT_TRACKER in kinds
        # No venv step yet: with no project file there is nothing to sync against.
        assert StepKind.CREATE_VENV not in kinds

    def test_a_pyproject_repo_without_venv_plans_create_venv_with_no_question(
        self, tmp_path: Path
    ) -> None:
        project = _with_pyproject(tmp_path)
        steps = plan(project)
        venv = next(s for s in steps if s.kind is StepKind.CREATE_VENV)
        assert venv.needs_question is False

    def test_project_file_and_tracker_steps_require_a_question(self, tmp_path: Path) -> None:
        project = _bare(tmp_path)
        steps = plan(project)
        init = next(s for s in steps if s.kind is StepKind.INIT_PROJECT)
        tracker = next(s for s in steps if s.kind is StepKind.INIT_TRACKER)
        assert init.needs_question is True
        assert tracker.needs_question is True

    def test_a_fully_provisioned_repo_plans_nothing(self, tmp_path: Path) -> None:
        project = _with_pyproject(tmp_path)
        (project / ".venv").mkdir()
        (project / ".seeds").mkdir()
        (project / ".smith" / "run").mkdir(parents=True)
        (project / ".smith" / "memory").mkdir(parents=True)
        assert plan(project) == []

    def test_plan_writes_nothing(self, tmp_path: Path) -> None:
        project = _bare(tmp_path)
        before = set(project.rglob("*"))
        plan(project)
        assert set(project.rglob("*")) == before

    def test_requirements_txt_repo_plans_a_venv_question(self, tmp_path: Path) -> None:
        project = _bare(tmp_path)
        (project / "requirements.txt").write_text("pytest\n", encoding="utf-8")
        steps = plan(project)
        venv = next(s for s in steps if s.kind is StepKind.CREATE_VENV)
        # pip-installing someone's requirements file is a decision, not a default.
        assert venv.needs_question is True


class TestApplyIsLoudAndConsentful:
    def test_apply_scaffolds_smith_and_reports_it(self, tmp_path: Path) -> None:
        project = _bare(tmp_path)
        steps = [s for s in plan(project) if s.kind is StepKind.SCAFFOLD_STATE]
        actions = apply_steps(project, steps, ask=lambda q: False)
        assert (project / ".smith" / "run").is_dir()
        assert (project / ".smith" / "memory").is_dir()
        assert actions == [Action(StepKind.SCAFFOLD_STATE, "CREATED", ".smith/{run,memory}")]

    def test_a_declined_question_step_is_skipped_and_reported(self, tmp_path: Path) -> None:
        project = _bare(tmp_path)
        steps = [s for s in plan(project) if s.kind is StepKind.INIT_TRACKER]
        actions = apply_steps(project, steps, ask=lambda q: False)
        assert not (project / ".seeds").exists()
        assert actions[0].outcome == "DECLINED"

    def test_every_question_reaches_the_ask_callback_verbatim(self, tmp_path: Path) -> None:
        project = _bare(tmp_path)
        asked: list[str] = []

        def ask(question: str) -> bool:
            asked.append(question)
            return False

        apply_steps(project, [s for s in plan(project) if s.needs_question], ask=ask)
        assert len(asked) >= 2
        assert all(q.strip().endswith("?") for q in asked)

    def test_apply_never_touches_mission_or_memory(self, tmp_path: Path) -> None:
        project = _with_pyproject(tmp_path)
        smith = project / ".smith"
        (smith / "memory").mkdir(parents=True)
        lessons = smith / "memory" / "lessons.md"
        lessons.write_text("- [2026-01-01] a durable lesson\n", encoding="utf-8")
        project_yaml = smith / "project.yaml"
        project_yaml.write_text("mission: keep me\n", encoding="utf-8")
        before_lessons = lessons.read_bytes()
        before_yaml = project_yaml.read_bytes()

        apply_steps(project, plan(project), ask=lambda q: True, run=lambda cmd, cwd: (0, "ok"))

        assert lessons.read_bytes() == before_lessons
        assert project_yaml.read_bytes() == before_yaml

    def test_command_steps_use_the_injected_runner_and_report_failure(self, tmp_path: Path) -> None:
        project = _with_pyproject(tmp_path)
        calls: list[str] = []

        def run(cmd: str, cwd: Path) -> tuple[int, str]:
            calls.append(cmd)
            return 1, "uv exploded"

        actions = apply_steps(
            project,
            [s for s in plan(project) if s.kind is StepKind.CREATE_VENV],
            ask=lambda q: True,
            run=run,
        )
        assert calls == ["uv sync"]
        assert actions[0].outcome == "FAILED"
        assert "uv exploded" in actions[0].detail


class TestVerifyToolchainFires:
    def test_verify_step_is_planned_when_a_task_runner_exists(self, tmp_path: Path) -> None:
        project = _with_pyproject(tmp_path)
        (project / "justfile").write_text("test:\n    pytest -q\n", encoding="utf-8")
        steps = plan(project)
        assert any(s.kind is StepKind.VERIFY_TOOLCHAIN for s in steps)

    def test_verify_step_runs_the_discovered_command(self, tmp_path: Path) -> None:
        project = _with_pyproject(tmp_path)
        (project / "justfile").write_text("test:\n    pytest -q\n", encoding="utf-8")
        ran: list[str] = []

        def run(cmd: str, cwd: Path) -> tuple[int, str]:
            ran.append(cmd)
            return 0, "all green"

        steps = [s for s in plan(project) if s.kind is StepKind.VERIFY_TOOLCHAIN]
        actions = apply_steps(project, steps, ask=lambda q: True, run=run)
        assert ran == ["just test"]
        assert actions[0].outcome == "VERIFIED"


class TestStepShape:
    def test_steps_are_frozen_and_carry_a_human_readable_reason(self, tmp_path: Path) -> None:
        project = _bare(tmp_path)
        for step in plan(project):
            assert isinstance(step, Step)
            assert step.reason
            with pytest.raises(AttributeError):
                step.kind = StepKind.SCAFFOLD_STATE  # type: ignore[misc]


class TestRecipeMustExist:
    def test_justfile_without_a_test_recipe_plans_no_verify_step(self, tmp_path: Path) -> None:
        project = _with_pyproject(tmp_path)
        (project / "justfile").write_text("lint:\n    ruff check .\n", encoding="utf-8")
        assert not any(s.kind is StepKind.VERIFY_TOOLCHAIN for s in plan(project))

    def test_makefile_without_a_test_target_is_not_offered(self, tmp_path: Path) -> None:
        from smith.provision import discover_verification

        project = _with_pyproject(tmp_path)
        (project / "Makefile").write_text("lint:\n\truff check .\n", encoding="utf-8")
        assert discover_verification(project) is None


class TestProjectEnv:
    """gate commands must run in the target project's environment, not the one
    awino itself was launched from via `uv run --project <other>`."""

    def test_env_prefers_the_project_venv_and_drops_the_inherited_one(self, tmp_path: Path) -> None:
        import os

        from smith.provision import project_env

        project = _with_pyproject(tmp_path)
        scripts = project / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        scripts.mkdir(parents=True)
        inherited = {
            "PATH": r"C:\other\.venv\Scripts;C:\Windows",
            "VIRTUAL_ENV": r"C:\other\.venv",
            "HOME": "x",
        }
        env = project_env(project, base=inherited)
        assert env["VIRTUAL_ENV"] == str(project / ".venv")
        assert env["PATH"].split(os.pathsep)[0] == str(scripts)
        assert r"C:\other\.venv\Scripts" not in env["PATH"]
        assert env["HOME"] == "x"

    def test_env_without_a_project_venv_still_scrubs_the_inherited_one(
        self, tmp_path: Path
    ) -> None:

        from smith.provision import project_env

        project = _bare(tmp_path)
        inherited = {"PATH": r"C:\other\.venv\Scripts;C:\Windows", "VIRTUAL_ENV": r"C:\other\.venv"}
        env = project_env(project, base=inherited)
        assert "VIRTUAL_ENV" not in env
        assert r"C:\other\.venv\Scripts" not in env["PATH"]
        assert "C:\\Windows" in env["PATH"]
