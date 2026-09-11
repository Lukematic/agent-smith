"""Tests for RPI pair-planning: the pair-plan phase between research and plan.

The pairing brief decomposes the work, proposes 2+ approaches with
trade-offs, and asks Qn: questions. The human answers (or declares defaults)
via `awino loop answer` / `awino loop default`; the loop cannot advance to
plan until every QID is answered. The plan prompt carries the recorded
decisions, and the plan validator traces each decision to a QID or an
explicit default.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import loops
from awino.cli.loopctl import loop_app
from awino.enforce import Ledger

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"

RESEARCH_OK = """# Research: auth migration

## Metadata
- date 2026-09-11, branch challenge/tested-fixes, commit abc123
- scope: src/auth.py examined in full

## Where it lives
| Concern | File | Lines |
|---|---|---|
| auth | src/auth.py | 1-200 |

## How it works
The authenticator validates tokens; see src/auth.py:42.

## Flow
login -> validate -> session.

## Existing conventions to imitate
Use the existing session store.

## Problem breakdown
1. Where tokens live and how they are read (src/auth.py:1-200).
2. How the session store is written during a migration window.
3. What "done" means for each phase of the cutover.

## Assumptions challenged
- "Migration requires downtime": challenged — src/auth.py:42 shows token
  validation is stateless, so a flag-gated path needs no outage.
- "The session store holds tokens": challenged — it holds sessions keyed by
  token hash, so both paths can coexist during the window.

## Angles considered
- The inverse: leave auth.py untouched and migrate callers instead.
- Conventions from src/session.py, which already does flag-gated rollouts.

## Applicability check (the lawyer move)
### Stated problem
Migrate auth with zero downtime.
### Reframed problem (or: the stated problem stands)
The stated problem stands: the migration is the work; the question was
never whether to migrate, only how.
### Evidence
src/auth.py:42 shows token validation is stateless, so the migration path
is a flag-gated rollout -- the problem is the cutover mechanism.
### User confirmation
User confirmed by Luke: solve the stated problem -- "Migrate auth with zero downtime.".

## Open questions
None.
"""

BRIEF_OK = """# Pairing brief: auth migration

## Sub-problems
- How to migrate tokens without downtime
- Where the session store lives

## Candidate approaches

### Big bang
Replace everything at once.
trade-off: risky but fast.
pro: one deploy. con: downtime risk.
effort: one day

### Strangler
Default recommendation: migrates incrementally behind a flag — exactly what
was asked, no more.
trade-off: slower but safe.
pro: zero downtime. con: two code paths for a while.
effort: three days

## Questions
Q1: Which approach do you prefer, big bang or strangler?
Q2: What is the downtime budget for the migration window?

## Required skills

- research: awino-rpi
- pair-plan: awino-rpi
- plan: awino-rpi
"""

PLAN_OK = """# Plan: auth migration

## Source research
thoughts/research/2026-09-11-0800-auth.md

## Phases
- [ ] research: read the auth code
- [ ] plan: write the migration plan
- [ ] implement: migrate behind a flag

## Scope
- `src/auth.py`

## Tests
pytest tests/test_auth.py -q must pass.

## Rollback
Revert the flag and redeploy.

## Acceptance criteria
- tokens validate after migration

## Decisions
- Chose the strangler approach (Q1); followed the default recommendation
  because downtime is unacceptable and strangler is exactly what was asked.
- Downtime budget is zero (Q2), so the flag stays until verified.
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "auth.py").write_text("# auth\n", encoding="utf-8")
    return project


@pytest.fixture()
def driver(project: Path, tmp_path: Path) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
    )


@pytest.fixture()
def event_driver(
    project: Path, tmp_path: Path, loop_ledger: Ledger
) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
        ledger=loop_ledger,
    )


@pytest.fixture()
def loop_ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / ".awino")


def _write(driver: loops.RpiDriver, state: loops.LoopState, kind: str, text: str) -> None:
    rel = {
        "research": state.research_artifact,
        "pair-plan": state.pairing_artifact,
        "plan": state.plan_artifact,
    }[kind]
    path = driver.project_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _confirm_problem(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    """Satisfy the lawyer-move gate in fixtures: the user confirmed the
    stated problem (the fixture research confirms it stands)."""
    driver.confirm_problem(state, by="Luke")


def _at_pair_plan(driver: loops.RpiDriver) -> loops.LoopState:
    """Research done, pairing brief written, now at the pair-plan phase."""
    state = driver.new("migrate auth")
    _write(driver, state, "research", RESEARCH_OK)
    assert driver.check(state) == []
    _confirm_problem(driver, state)
    _write(driver, state, "pair-plan", BRIEF_OK)
    assert driver.advance(state) == "pair-plan"
    return driver.load(state.id)


class TestPairBriefValidation:
    def test_valid_brief_passes(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        assert driver.check(state) == []

    def test_missing_brief_names_the_path(self, driver: loops.RpiDriver) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _confirm_problem(driver, state)
        assert driver.check(state) == []
        driver.advance(state)  # no brief -> skips to plan; force pair-plan
        state = driver.load(state.id)
        driver.reenter_phase(state, "pair-plan", "test")
        missing = driver.check(state)
        assert missing
        assert state.pairing_artifact in missing[0]

    def test_missing_sub_problems_named(self, driver: loops.RpiDriver) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _confirm_problem(driver, state)
        _write(
            driver, state, "pair-plan",
            BRIEF_OK.replace("## Sub-problems", "## Background"),
        )
        assert driver.check(state) == []
        driver.advance(state)
        missing = driver.check(state)
        assert any("sub-problems" in item for item in missing)

    def test_single_approach_rejected(self, driver: loops.RpiDriver) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _confirm_problem(driver, state)
        one = BRIEF_OK.split("### Strangler")[0]
        _write(driver, state, "pair-plan", one)
        assert driver.check(state) == []
        driver.advance(state)
        missing = driver.check(state)
        assert any("need at least 2" in item for item in missing)

    def test_approach_without_tradeoff_rejected(self, driver: loops.RpiDriver) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _confirm_problem(driver, state)
        no_tradeoff = BRIEF_OK.replace("trade-off:", "note:").replace("pro:", "plus:")
        no_tradeoff = no_tradeoff.replace("con:", "minus:")
        _write(driver, state, "pair-plan", no_tradeoff)
        assert driver.check(state) == []
        driver.advance(state)
        missing = driver.check(state)
        assert any("trade-off" in item for item in missing)

    def test_questions_not_in_qn_format_rejected(self, driver: loops.RpiDriver) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _confirm_problem(driver, state)
        _write(
            driver, state, "pair-plan",
            BRIEF_OK.replace("Q1:", "Question one:").replace("Q2:", "Question two:"),
        )
        assert driver.check(state) == []
        driver.advance(state)
        missing = driver.check(state)
        assert any("Qn:" in item for item in missing)

    def test_pair_plan_phase_order(self, driver: loops.RpiDriver) -> None:
        assert driver.phase_order == ("research", "pair-plan", "plan", "implement")


class TestPairingQuestions:
    def test_questions_extracted_in_order(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        questions = driver.pairing_questions(state)
        assert [qid for qid, _ in questions] == ["Q1", "Q2"]
        assert "big bang or strangler" in questions[0][1]

    def test_questions_print_verbatim(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        lines = driver.describe_pairing(state)
        assert any(
            "Q1: Which approach do you prefer, big bang or strangler?" in line
            for line in lines
        )

    def test_unanswered_lists_all_initially(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        assert driver.unanswered_questions(state) == ["Q1", "Q2"]


class TestPairAnswers:
    def test_answer_recorded_and_emits_human_answered(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        state = _at_pair_plan(event_driver)
        record = event_driver.record_pair_answer(
            state, "Q1", "answer", "strangler, downtime is unacceptable"
        )
        assert record["kind"] == "answer"
        assert record["text"] == "strangler, downtime is unacceptable"
        assert event_driver.unanswered_questions(state) == ["Q2"]
        events = loop_ledger.loop_events(state.id)
        answered = [e for e in events if e.kind == "human_answered"]
        assert len(answered) == 1
        assert "Q1" in answered[0].detail

    def test_default_recorded_with_reason(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        record = driver.record_pair_answer(
            state, "Q2", "default", "zero downtime assumed; cheapest safe choice"
        )
        assert record["kind"] == "default"
        assert driver.unanswered_questions(state) == ["Q1"]

    def test_unknown_question_refused(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        with pytest.raises(loops.LoopError, match="unknown question 'Q9'"):
            driver.record_pair_answer(state, "Q9", "answer", "nope")

    def test_reanswering_overwrites(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        driver.record_pair_answer(state, "Q1", "answer", "big bang")
        driver.record_pair_answer(state, "Q1", "answer", "strangler on reflection")
        assert state.pair_answers["Q1"]["text"] == "strangler on reflection"
        assert driver.unanswered_questions(state) == ["Q2"]


class TestPairingGate:
    def test_cannot_advance_with_unanswered_questions(
        self, driver: loops.RpiDriver
    ) -> None:
        state = _at_pair_plan(driver)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        with pytest.raises(loops.PairingIncomplete) as exc_info:
            driver.advance(state)
        assert exc_info.value.unanswered == ["Q2"]
        assert "Q2" in str(exc_info.value)

    def test_advance_allowed_when_all_answered(
        self, driver: loops.RpiDriver
    ) -> None:
        state = _at_pair_plan(driver)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        driver.record_pair_answer(state, "Q2", "default", "zero downtime assumed")
        assert driver.check(state) == []
        assert driver.advance(state) == "plan"

    def test_no_brief_skips_pair_plan(self, driver: loops.RpiDriver) -> None:
        """Backward compatibility: loops that never write a pairing brief
        keep the research -> plan -> implement path."""
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _confirm_problem(driver, state)
        assert driver.check(state) == []
        assert driver.advance(state) == "plan"

    def test_unpaired_plan_needs_no_decisions_section(
        self, driver: loops.RpiDriver
    ) -> None:
        """UNPAIRED_PLAN: when pair-planning was skipped, a plan without a
        Decisions section still validates (backward compatibility)."""
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _confirm_problem(driver, state)
        assert driver.check(state) == []
        assert driver.advance(state) == "plan"
        state = driver.load(state.id)
        plan_without_decisions = "\n".join(
            line for line in PLAN_OK.splitlines()
            if not line.startswith("## Decisions")
        )
        # Remove the decision bullets too (they were under ## Decisions).
        plan_without_decisions = "\n".join(
            line for line in plan_without_decisions.splitlines()
            if "Q1" not in line and "Q2" not in line
        )
        _write(driver, state, "plan", plan_without_decisions)
        assert driver.check(state) == []


class TestPlanDecisionTrace:
    def _at_plan_answered(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = _at_pair_plan(driver)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        driver.record_pair_answer(state, "Q2", "default", "zero downtime assumed")
        assert driver.check(state) == []
        assert driver.advance(state) == "plan"
        return driver.load(state.id)

    def test_plan_prompt_carries_human_decisions(
        self, driver: loops.RpiDriver
    ) -> None:
        state = self._at_plan_answered(driver)
        prompt = driver.prompt_block(state, "plan")
        assert "Human decisions so far" in prompt
        assert "strangler" in prompt
        assert "zero downtime assumed" in prompt
        assert "DEFAULT" in prompt

    def test_traced_plan_passes(self, driver: loops.RpiDriver) -> None:
        state = self._at_plan_answered(driver)
        _write(driver, state, "plan", PLAN_OK)
        assert driver.check(state) == []

    def test_untraced_decision_rejected(self, driver: loops.RpiDriver) -> None:
        state = self._at_plan_answered(driver)
        bad = PLAN_OK + "- Rewrite everything in Rust because it feels faster.\n"
        _write(driver, state, "plan", bad)
        missing = driver.check(state)
        assert any("does not trace" in item for item in missing)

    def test_unknown_qid_reference_rejected(self, driver: loops.RpiDriver) -> None:
        state = self._at_plan_answered(driver)
        bad = PLAN_OK + "- Do the thing (Q9).\n"
        _write(driver, state, "plan", bad)
        missing = driver.check(state)
        assert any("unknown question 'Q9'" in item for item in missing)

    def test_explicit_default_with_reason_passes(
        self, driver: loops.RpiDriver
    ) -> None:
        state = self._at_plan_answered(driver)
        plan = PLAN_OK + "- Use Postgres (default: the team already runs it).\n"
        _write(driver, state, "plan", plan)
        assert driver.check(state) == []


class TestPairPlanBack:
    def test_back_from_plan_to_pair_plan_allowed(
        self, driver: loops.RpiDriver
    ) -> None:
        state = _at_pair_plan(driver)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        driver.record_pair_answer(state, "Q2", "answer", "zero")
        assert driver.check(state) == []
        driver.advance(state)
        state = driver.load(state.id)
        assert state.phase == "plan"
        driver.reenter_phase(state, "pair-plan", "rethink the approaches")
        assert state.phase == "pair-plan"

    def test_back_retains_answers(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        driver.record_pair_answer(state, "Q2", "answer", "zero")
        assert driver.check(state) == []
        driver.advance(state)
        state = driver.load(state.id)
        driver.reenter_phase(state, "pair-plan", "rethink")
        assert set(state.pair_answers) == {"Q1", "Q2"}
        assert driver.unanswered_questions(state) == []

    def test_back_rearms_validation(self, driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(driver)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        driver.record_pair_answer(state, "Q2", "answer", "zero")
        assert driver.check(state) == []
        driver.advance(state)
        state = driver.load(state.id)
        driver.reenter_phase(state, "pair-plan", "rethink")
        # Re-validation runs again on next even though the brief is unchanged.
        assert driver.check(state) == []


class TestPairPlanningCli:
    @pytest.fixture()
    def cli_env(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setenv("AWINO_PROJECT", str(project))
        return project

    def _artifact_path(self, output: str, prefix: str = "ARTIFACT") -> Path:
        for line in output.splitlines():
            if line.startswith(prefix):
                return Path(line.split(None, 1)[1].strip())
        raise AssertionError(f"no {prefix} line in output")

    def _run_to_pair_plan(self, cli_env: Path) -> CliRunner:
        runner = CliRunner()
        created = runner.invoke(loop_app, ["run", "rpi", "--task", "migrate auth"])
        assert created.exit_code == 0, created.output
        research = cli_env / self._artifact_path(created.output)
        research.parent.mkdir(parents=True, exist_ok=True)
        research.write_text(RESEARCH_OK, encoding="utf-8")
        # The pairing brief shares the research artifact's stamp and topic.
        brief = cli_env / Path(
            str(self._artifact_path(created.output)).replace("/research/", "/pairing/")
        )
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(BRIEF_OK, encoding="utf-8")
        confirmed = runner.invoke(loop_app, ["confirm-problem", "--confirmed"])
        assert confirmed.exit_code == 0, confirmed.output
        nxt = runner.invoke(loop_app, ["next"])
        assert nxt.exit_code == 0, nxt.output
        assert "ADVANCED  phase=pair-plan" in nxt.output
        return runner

    def test_next_refusal_names_unanswered_ids(self, cli_env: Path) -> None:
        runner = self._run_to_pair_plan(cli_env)
        refused = runner.invoke(loop_app, ["next"])
        assert refused.exit_code == 1
        assert "REFUSED" in refused.output
        assert "Q1" in refused.output
        assert "Q2" in refused.output

    def test_answer_and_default_then_advance(self, cli_env: Path) -> None:
        runner = self._run_to_pair_plan(cli_env)
        answered = runner.invoke(
            loop_app, ["answer", "--question", "Q1", "--answer", "strangler"]
        )
        assert answered.exit_code == 0, answered.output
        assert "ANSWERED  Q1" in answered.output
        assert "REMAINING  Q2" in answered.output

        defaulted = runner.invoke(
            loop_app,
            ["default", "--question", "Q2", "--reason", "zero downtime assumed"],
        )
        assert defaulted.exit_code == 0, defaulted.output
        assert "DEFAULTED  Q2" in defaulted.output

        advanced = runner.invoke(loop_app, ["next"])
        assert advanced.exit_code == 0, advanced.output
        assert "ADVANCED  phase=plan" in advanced.output

    def test_answer_unknown_question_refused(self, cli_env: Path) -> None:
        runner = self._run_to_pair_plan(cli_env)
        refused = runner.invoke(
            loop_app, ["answer", "--question", "Q9", "--answer", "nope"]
        )
        assert refused.exit_code == 1
        assert "REFUSED" in refused.output

    def test_status_lists_pairing_qa(self, cli_env: Path) -> None:
        runner = self._run_to_pair_plan(cli_env)
        runner.invoke(loop_app, ["answer", "--question", "Q1", "--answer", "strangler"])
        status = runner.invoke(loop_app, ["status"])
        assert status.exit_code == 0, status.output
        assert "phase: pair-plan" in status.output
        assert "ANSWERED Q1" in status.output
        assert "OPEN Q2" in status.output
        assert "next:" in status.output
