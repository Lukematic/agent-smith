"""Tests for the RPI loop driver (src/smith/loops.py) and its CLI
(src/smith/cli/loopctl.py).

All state lives in tmp dirs: the driver gets an explicit loops_dir and
project_root, and the CLI runs under AWINO_PROJECT pointing at a tmp project.
The real repo state is never touched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smith import loops
from smith.cli.loopctl import loop_app

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"

RESEARCH_OK = """# Research: rpi loop driver

## Metadata
- date 2026-09-11, branch challenge/tested-fixes, commit abc123
- scope: src/smith/loops.py and src/smith/cli/loopctl.py examined in full;
  the gate ledger was read, not modified

## Where it lives
| Concern | File | Lines |
|---|---|---|
| driver | src/smith/loops.py | 1-200 |
| cli | src/smith/cli/loopctl.py | 1-150 |

## How it works
The driver sequences phases; validation lives in src/smith/loops.py:100 and
state persists via LoopState at src/smith/loops.py:140. The CLI in
src/smith/cli/loopctl.py:1 wires the driver to typer commands.

## Flow
run rpi -> next -> approve -> next -> handoff to the gate ledger.

## Existing conventions to imitate
_gate.py_ helpers _echo/_workspace are reused by loopctl.

## Open questions
None; the ledger layout was read from src/smith/enforce.py:381.
"""

PLAN_OK = """# Plan: rpi loop driver

## Source research
thoughts/research/2026-09-11-0800-rpi-loop-driver.md

## Phases
- [ ] research: validate artifact shape
- [ ] plan: validate sections and scope paths
- [ ] implement: verify the gate-ledger handoff

## Scope
- `src/smith/loops.py`
- `src/smith/cli/loopctl.py`
- `tests/test_loops.py`

## Tests
pytest tests/test_loops.py -q must pass.

## Rollback
Delete the three new files and drop the registration lines.

## Acceptance criteria
- `awino loop run rpi --task ...` prints the phase-1 prompt
- `awino loop next` advances only on valid artifacts
"""


def _project(tmp_path: Path) -> Path:
    """A fake project with the files the plan fixture claims are in scope."""
    project = tmp_path / "project"
    (project / "src" / "smith" / "cli").mkdir(parents=True)
    (project / "tests").mkdir(parents=True)
    for rel in (
        "src/smith/loops.py",
        "src/smith/cli/loopctl.py",
        "tests/test_loops.py",
    ):
        (project / rel).write_text("# placeholder\n", encoding="utf-8")
    return project


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return _project(tmp_path)


@pytest.fixture()
def driver(project: Path, tmp_path: Path) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
    )


def _write_research(driver: loops.RpiDriver, state: loops.LoopState, text: str) -> None:
    path = driver.project_root / state.research_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_plan(driver: loops.RpiDriver, state: loops.LoopState, text: str) -> None:
    path = driver.project_root / state.plan_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestPromptImport:
    def test_phase_prompts_come_from_the_skill_document(self, driver: loops.RpiDriver) -> None:
        research = loops.phase_prompt_text(SKILL_MD, "research")
        plan = loops.phase_prompt_text(SKILL_MD, "plan")
        implement = loops.phase_prompt_text(SKILL_MD, "implement")
        assert "Phase 1" in research and "RESEARCH_CONTAMINATION" in research
        assert "Phase 2" in plan and "Human reviews" in plan
        assert "Phase 3" in implement and "PLAN_DRIFT" in implement

    def test_missing_skill_section_refuses_to_improvise(self, tmp_path: Path) -> None:
        fake = tmp_path / "SKILL.md"
        fake.write_text("# no phases here\n", encoding="utf-8")
        with pytest.raises(loops.LoopError, match="no '## Phase 1' section"):
            loops.phase_prompt_text(fake, "research")


class TestResearchValidation:
    def test_valid_research_passes(self, driver: loops.RpiDriver) -> None:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        assert driver.validate_current(state) == []

    def test_missing_research_artifact_names_the_path(
        self, driver: loops.RpiDriver
    ) -> None:
        state = driver.new("add an RPI loop driver")
        missing = driver.validate_current(state)
        assert len(missing) == 1
        assert "research artifact missing" in missing[0]
        assert state.research_artifact in missing[0]

    def test_short_research_rejected_with_char_count(
        self, driver: loops.RpiDriver
    ) -> None:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, "too short, see src/smith/loops.py:1\n")
        missing = driver.validate_current(state)
        assert len(missing) == 1
        assert "too short" in missing[0]
        assert re.search(r"\d+ chars", missing[0])

    def test_research_without_file_line_refs_rejected(
        self, driver: loops.RpiDriver
    ) -> None:
        text = RESEARCH_OK
        text = re.sub(r"\S+:\d+", "some file", text)
        assert len(text) >= loops.RESEARCH_MIN_CHARS
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, text)
        missing = driver.validate_current(state)
        assert len(missing) == 1
        assert "file:line" in missing[0]


class TestPlanValidation:
    def _research_done(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        assert driver.validate_current(state) == []
        driver.advance(state)
        return driver.load(state.id)

    def test_valid_plan_passes(self, driver: loops.RpiDriver) -> None:
        state = self._research_done(driver)
        _write_plan(driver, state, PLAN_OK)
        assert driver.validate_current(state) == []

    def test_plan_missing_section_names_it(self, driver: loops.RpiDriver) -> None:
        state = self._research_done(driver)
        text = "\n".join(
            line for line in PLAN_OK.splitlines() if line.strip() != "## Rollback"
        )
        _write_plan(driver, state, text)
        missing = driver.validate_current(state)
        assert missing == ["plan missing required section: 'rollback'"]

    def test_plan_nonexistent_scope_path_names_it(self, driver: loops.RpiDriver) -> None:
        state = self._research_done(driver)
        text = PLAN_OK.replace(
            "`src/smith/cli/loopctl.py`", "`src/smith/cli/does-not-exist.py`"
        )
        _write_plan(driver, state, text)
        missing = driver.validate_current(state)
        assert missing == [
            "scope path does not exist in repo: 'src/smith/cli/does-not-exist.py'"
        ]

    def test_plan_accepts_acceptance_synonym(self, driver: loops.RpiDriver) -> None:
        state = self._research_done(driver)
        text = PLAN_OK.replace("## Acceptance criteria", "## Acceptance")
        _write_plan(driver, state, text)
        assert driver.validate_current(state) == []


class TestApprovalGate:
    def _at_plan(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        driver.advance(state)
        state = driver.load(state.id)
        _write_plan(driver, state, PLAN_OK)
        assert driver.validate_current(state) == []
        return state

    def test_next_past_plan_without_approval_is_refused(
        self, driver: loops.RpiDriver
    ) -> None:
        state = self._at_plan(driver)
        with pytest.raises(loops.ApprovalRequired, match="not approved"):
            driver.advance(state)
        assert driver.load(state.id).phase == "plan"

    def test_approval_then_advance(self, driver: loops.RpiDriver) -> None:
        state = self._at_plan(driver)
        driver.approve_plan(state, by="Luke", reason="plan is explicit enough")
        reloaded = driver.load(state.id)
        assert driver.plan_approved(reloaded)
        approval = reloaded.approvals[-1]
        assert approval["by"] == "Luke"
        assert approval["reason"] == "plan is explicit enough"
        assert "at" in approval
        assert driver.advance(reloaded) == "implement"


class TestImplementHandoff:
    def _at_implement(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        driver.advance(state)
        state = driver.load(state.id)
        _write_plan(driver, state, PLAN_OK)
        driver.approve_plan(state, by="Luke", reason="ok")
        driver.advance(state)
        return driver.load(state.id)

    def test_implement_validates_against_an_open_rpi_run(
        self, driver: loops.RpiDriver
    ) -> None:
        state = self._at_implement(driver)
        assert driver.validate_current(state) == []

    def test_implement_without_open_run_is_missing(self, project: Path, tmp_path: Path) -> None:
        driver = loops.RpiDriver(
            project_root=project,
            loops_dir=tmp_path / "loops",
            skill_md=SKILL_MD,
            open_rpi_run=lambda: None,
        )
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        driver.advance(state)
        state = driver.load(state.id)
        _write_plan(driver, state, PLAN_OK)
        driver.approve_plan(state, by="Luke", reason="ok")
        driver.advance(state)
        state = driver.load(state.id)
        missing = driver.validate_current(state)
        assert len(missing) == 1
        assert "no open gate run with --loop rpi" in missing[0]

    def test_advance_from_implement_records_handoff_not_completion(
        self, driver: loops.RpiDriver
    ) -> None:
        state = self._at_implement(driver)
        assert driver.advance(state) == "done"
        done = driver.load(state.id)
        assert done.phase == "done"
        assert done.gate_run_id == "run-123"
        assert done.handoff is not None
        assert "gate ledger" in done.handoff["note"]


class TestThreeStrikes:
    def test_three_failed_validations_lock_the_loop(
        self, driver: loops.RpiDriver
    ) -> None:
        state = driver.new("add an RPI loop driver")
        # Never write the artifact: every validation fails.
        for _ in range(3):
            assert driver.validate_current(state)
            driver.record_failure(state)
            state = driver.load(state.id)
        assert state.locked
        assert state.attempts["research"] == 3
        with pytest.raises(loops.LoopLocked, match="locked"):
            driver.advance(state)

    def test_state_survives_reload(self, driver: loops.RpiDriver) -> None:
        state = driver.new("add an RPI loop driver")
        driver.record_failure(state)
        reloaded = driver.load(state.id)
        assert reloaded.attempts["research"] == 1
        assert reloaded.task == "add an RPI loop driver"
        assert driver.current_id() == state.id


# ── CLI ──────────────────────────────────────────────────────────────────────

runner = CliRunner()


@pytest.fixture()
def cli_env(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AWINO_PROJECT", str(project))
    return project


def _artifact_path(output: str) -> Path:
    for line in output.splitlines():
        if line.startswith("ARTIFACT"):
            return Path(line.split(None, 1)[1].strip())
    raise AssertionError("no ARTIFACT line in output")


class TestLoopCli:
    def test_run_rpi_prints_phase_1_prompt(self, cli_env: Path) -> None:
        result = runner.invoke(
            loop_app, ["run", "rpi", "--task", "add an RPI loop driver"]
        )
        assert result.exit_code == 0, result.output
        assert "LOOP" in result.output
        assert "Phase 1" in result.output
        assert "RESEARCH_CONTAMINATION" in result.output
        assert "ARTIFACT" in result.output

    def test_next_with_missing_artifact_prints_what_is_missing(
        self, cli_env: Path
    ) -> None:
        assert runner.invoke(loop_app, ["run", "rpi", "--task", "x"]).exit_code == 0
        result = runner.invoke(loop_app, ["next"])
        assert result.exit_code == 1
        assert "VALIDATION_FAILED" in result.output
        assert "research artifact missing" in result.output

    def test_full_rpi_flow_end_to_end(self, cli_env: Path) -> None:
        project = cli_env
        run_result = runner.invoke(
            loop_app, ["run", "rpi", "--task", "add an RPI loop driver"]
        )
        assert run_result.exit_code == 0, run_result.output
        research_path = project / _artifact_path(run_result.output)
        research_path.parent.mkdir(parents=True, exist_ok=True)
        research_path.write_text(RESEARCH_OK, encoding="utf-8")

        next_result = runner.invoke(loop_app, ["next"])
        assert next_result.exit_code == 0, next_result.output
        assert "ADVANCED  phase=plan" in next_result.output
        assert "Phase 2" in next_result.output
        plan_path = project / _artifact_path(next_result.output)

        # Plan validates but the approval gate refuses the advance.
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(PLAN_OK, encoding="utf-8")
        refused = runner.invoke(loop_app, ["next"])
        assert refused.exit_code == 1
        assert "REFUSED" in refused.output
        assert "approve" in refused.output.lower()

        status = runner.invoke(loop_app, ["status"])
        assert status.exit_code == 0
        assert "phase: plan" in status.output
        assert "approval: pending" in status.output

        approved = runner.invoke(
            loop_app, ["approve", "--by", "Luke", "--reason", "explicit enough"]
        )
        assert approved.exit_code == 0, approved.output
        assert "APPROVED" in approved.output

        advanced = runner.invoke(loop_app, ["next"])
        assert advanced.exit_code == 0, advanced.output
        assert "ADVANCED  phase=implement" in advanced.output
        assert "Phase 3" in advanced.output

        # No open rpi gate run yet: the handoff check fails by name.
        no_run = runner.invoke(loop_app, ["next"])
        assert no_run.exit_code == 1
        assert "no open gate run with --loop rpi" in no_run.output

        # Fake an open ledger run tagged --loop rpi.
        run_dir = project / ".smith" / "run" / "run-abc"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_dir.joinpath("run.json").write_text(
            json.dumps({"run_id": "run-abc", "loop": "rpi", "terminal_state": None}),
            encoding="utf-8",
        )
        handoff = runner.invoke(loop_app, ["next"])
        assert handoff.exit_code == 0, handoff.output
        assert "HANDOFF" in handoff.output
        assert "run-abc" in handoff.output

        done = runner.invoke(loop_app, ["next"])
        assert done.exit_code == 1
        assert "already done" in done.output

    def test_approve_outside_plan_is_refused(self, cli_env: Path) -> None:
        assert runner.invoke(loop_app, ["run", "rpi", "--task", "x"]).exit_code == 0
        result = runner.invoke(loop_app, ["approve", "--by", "Luke"])
        assert result.exit_code == 1
        assert "nothing to approve" in result.output

    def test_three_strikes_locks_the_loop(self, cli_env: Path) -> None:
        assert runner.invoke(loop_app, ["run", "rpi", "--task", "x"]).exit_code == 0
        for attempt in (1, 2):
            result = runner.invoke(loop_app, ["next"])
            assert result.exit_code == 1
            assert f"attempt={attempt}/3" in result.output
        third = runner.invoke(loop_app, ["next"])
        assert third.exit_code == 1
        assert "ESCALATED" in third.output
        fourth = runner.invoke(loop_app, ["next"])
        assert fourth.exit_code == 1
        assert "LOOP_LOCKED" in fourth.output
        status = runner.invoke(loop_app, ["status"])
        assert "locked: yes" in status.output
