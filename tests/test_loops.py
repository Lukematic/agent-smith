"""Tests for the RPI loop driver (src/awino/loops.py) and its CLI
(src/awino/cli/loopctl.py).

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

from awino import heilmeier, loops
from awino.cli.loopctl import loop_app
from awino.enforce import Ledger, LedgerError

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"

RESEARCH_OK = """# Research: rpi loop driver

## Metadata
- date 2026-09-11, branch challenge/tested-fixes, commit abc123
- scope: src/awino/loops.py and src/awino/cli/loopctl.py examined in full;
  the gate ledger was read, not modified

## Where it lives
| Concern | File | Lines |
|---|---|---|
| driver | src/awino/loops.py | 1-200 |
| cli | src/awino/cli/loopctl.py | 1-150 |

## How it works
The driver sequences phases; validation lives in src/awino/loops.py:100 and
state persists via LoopState at src/awino/loops.py:140. The CLI in
src/awino/cli/loopctl.py:1 wires the driver to typer commands.

## Flow
run rpi -> next -> approve -> next -> handoff to the gate ledger.

## Existing conventions to imitate
_gate.py_ helpers _echo/_workspace are reused by loopctl.

## Problem breakdown
1. How the driver sequences phases (src/awino/loops.py:100).
2. Where validation lives and what shape it checks.
3. How the CLI wires the driver to typer commands (src/awino/cli/loopctl.py:1).

## Assumptions challenged
- "Validation is per-phase prose": challenged — src/awino/loops.py:140 shows
  each phase has a validator returning exactly-what-is-missing lists.
- "State is kept in memory": challenged — LoopState persists via the loop
  ledger on disk, so the CLI is stateless across invocations.

## Angles considered
- The inverse: a single validator for all phases instead of per-phase ones.
- Conventions from src/awino/enforce.py, which already emits named events
  the driver reuses for phase transitions.

## Applicability check (the lawyer move)
### Stated problem
Add an RPI loop driver.
### Reframed problem (or: the stated problem stands)
The stated problem stands: the repo has gate phases but no loop driver
sequencing research -> plan -> implement above them.
### Evidence
src/awino/enforce.py:381 shows the gate ledger with no loop state machine
driving phases; loops.py does not exist yet.
### User confirmation
User confirmed by Luke: solve the stated problem -- "Add an RPI loop driver.".

## Open questions
None; the ledger layout was read from src/awino/enforce.py:381.
"""

PLAN_OK = """# Plan: rpi loop driver

## Source research
thoughts/research/2026-09-11-0800-rpi-loop-driver.md

## Phases
- [ ] research: validate artifact shape
- [ ] plan: validate sections and scope paths
- [ ] implement: verify the gate-ledger handoff

## Scope
- `src/awino/loops.py`
- `src/awino/cli/loopctl.py`
- `tests/test_loops.py`

## Tests
pytest tests/test_loops.py -q must pass.

## Rollback
Delete the three new files and drop the registration lines.

## Acceptance criteria
- `awino loop run rpi --task ...` prints the phase-1 prompt
- `awino loop next` advances only on valid artifacts

## Decisions
- Q1 -> extract a spine module: followed the default recommendation because one ordered list owns the whole chain.
- Q2 -> Luke owns the spine module: answered during pair-planning.
"""

PREMORTEM_OK = """# Premortem: rpi loop driver

## Failure reasons
1. The per-phase validators drift out of sync with the phase list.
   Warning signs: `loop next` advances on an invalid artifact; the CLI
   phase prompt names a phase the driver no longer has.
2. Loop state persists without the ledger trail.
   Warning signs: `buddy` shows a loop with no matching ledger events;
   `loops_dir` holds state files newer than the last ledger write.
3. The handoff to the gate ledger names the wrong run.
   Warning signs: the run ledger has no entry for the loop id; the gate
   receipt names a different task.

## The tripwire
Failure reason 1 is the most likely. The metric: count of phases where
the validator list and the phase list disagree in the weekly
`buddy check` run, threshold zero, checked every Monday. Any nonzero
count blocks the release.
"""


def _thinking_run(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    """Satisfy the plan-approval thinking gate in fixtures: a recorded
    premortem run (memory id only; working memory is not written here)."""
    driver.record_thinking_run(state, "premortem", by="Luke", memory_id="D-0001")


def _confirm_problem(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    """Satisfy the lawyer-move gate in fixtures: the user confirmed the
    stated problem (the fixture research confirms it stands)."""
    driver.confirm_problem(state, by="Luke")


def _comprehend(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    """Satisfy the plan-approval comprehension gate in fixtures. PAIRING_OK
    asks two questions, so the plan's decisions section yields two probes
    (P1, P2); the explanation references the key decisions by name."""
    driver.record_explanation(
        state,
        "We will extract a spine module owning the ordered precondition chain. "
        "Chose to extract a spine module (Q1); followed the default "
        "recommendation because one ordered list owns the whole chain. "
        "Luke owns the spine module long-term (Q2).",
        by="Luke",
    )
    driver.record_probe_answer(
        state,
        "P1",
        "one list owns the order; if the extraction is wrong, gates scatter again",
        by="Luke",
    )
    driver.record_probe_answer(
        state,
        "P2",
        "Luke reviews spine changes; if ownership is wrong, no one tends the order",
        by="Luke",
    )


def _paste_comprehension_block(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    """Paste the comprehension-check record into the plan artifact -- the
    documented workflow: once comprehension work exists in loop state, the
    plan document itself records it (the validator requires the block)."""
    path = driver.project_root / state.plan_artifact
    text = path.read_text(encoding="utf-8")
    block = driver.comprehension_record_block(state)
    path.write_text(text.rstrip("\n") + "\n\n" + block, encoding="utf-8")


def _write_mission(project: Path) -> None:
    """A valid mission: objective + at least one exam wired to a
    verification command. The spine (step 1) refuses all advancement
    without it."""
    heilmeier.save(
        project / ".awino",
        heilmeier.Catechism(
            answers={
                "objective": "exercise the test loop honestly",
                "exams": "the loop advances through its phases -> true",
            }
        ),
    )


def _project(tmp_path: Path) -> Path:
    """A fake project with the files the plan fixture claims are in scope."""
    project = tmp_path / "project"
    (project / "src" / "awino" / "cli").mkdir(parents=True)
    (project / "tests").mkdir(parents=True)
    for rel in (
        "src/awino/loops.py",
        "src/awino/cli/loopctl.py",
        "tests/test_loops.py",
    ):
        (project / rel).write_text("# placeholder\n", encoding="utf-8")
    _write_mission(project)
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


@pytest.fixture()
def loop_ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / ".awino")


@pytest.fixture()
def event_driver(project: Path, tmp_path: Path, loop_ledger: Ledger) -> loops.RpiDriver:
    """A driver wired to a ledger, so every transition lands in the trail."""
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
        ledger=loop_ledger,
    )


def _write_research(driver: loops.RpiDriver, state: loops.LoopState, text: str) -> None:
    path = driver.project_root / state.research_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_plan(driver: loops.RpiDriver, state: loops.LoopState, text: str) -> None:
    path = driver.project_root / state.plan_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


PAIRING_OK = """# Pairing brief: RPI loop driver

## Sub-problems
- Validate the driver shape per phase
- Persist state on disk and wire the CLI to typer commands

## Candidate approaches

### Extend the existing driver
Build the spine on the current driver in place.
trade-off: less code but couples the new gates to the old paths.
pro: smaller diff. con: harder to isolate the spine.
effort: two days

### Extract a spine module
Default recommendation: the precondition chain becomes its own ordered
list -- exactly what was asked, no more.
trade-off: cleaner boundary but touches every advance path.
pro: one place owns the order. con: every driver opts in.
effort: three days

## Questions
Q1: Which approach do you prefer, extend or extract?
Q2: Who owns the spine module long-term?

## Required skills

- research: awino-rpi
- pair-plan: awino-rpi
- plan: awino-rpi
"""


def _write_pairing(driver: loops.RpiDriver, state: loops.LoopState, text: str = PAIRING_OK) -> None:
    path = driver.project_root / state.pairing_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _answer_all(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    """Answer every pairing question, satisfying the pair-plan spine step."""
    for qid, _ in driver.pairing_questions(state):
        driver.record_pair_answer(state, qid, "answer", "use the default recommendation", by="Luke")


def _advance_to_plan(driver: loops.RpiDriver, state: loops.LoopState) -> loops.LoopState:
    """research -> pair-plan -> plan through the mandatory pair-plan step.

    The spine's pair-plan step is not skippable: the brief is written, its
    questions answered, and the loop advances through pair-plan on the way
    to plan. A skip is a decision, never an oversight.
    """
    assert driver.advance(state) == "pair-plan"
    state = driver.load(state.id)
    _write_pairing(driver, state)
    assert driver.check(state) == []
    _answer_all(driver, state)
    assert driver.advance(state) == "plan"
    return driver.load(state.id)


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

    def test_missing_research_artifact_names_the_path(self, driver: loops.RpiDriver) -> None:
        state = driver.new("add an RPI loop driver")
        missing = driver.validate_current(state)
        assert len(missing) == 1
        assert "research artifact missing" in missing[0]
        assert state.research_artifact in missing[0]

    def test_short_research_rejected_with_char_count(self, driver: loops.RpiDriver) -> None:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, "too short, see src/awino/loops.py:1\n")
        missing = driver.validate_current(state)
        assert len(missing) == 1
        assert "too short" in missing[0]
        assert re.search(r"\d+ chars", missing[0])

    def test_research_without_file_line_refs_rejected(self, driver: loops.RpiDriver) -> None:
        text = RESEARCH_OK
        text = re.sub(r"\S+:\d+", "some file", text)
        assert len(text) >= loops.RESEARCH_MIN_CHARS
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, text)
        missing = driver.validate_current(state)
        assert len(missing) == 1
        assert "file:line" in missing[0]


WRONG_PROBLEM_RESEARCH = """# Research: slow dashboard page

## Metadata
- date 2026-09-11, branch challenge/tested-fixes, commit abc123
- scope: the dashboard page and the login funnel, analytics read only

## Where it lives
| Concern | File | Lines |
|---|---|---|
| dashboard | src/web/dashboard.py | 1-200 |
| login | src/web/login.py | 1-80 |

## How it works
The dashboard renders after login; the funnel is tracked in
src/web/analytics.py:10.

## Flow
login -> dashboard render -> user task.

## Existing conventions to imitate
Analytics events are named per src/web/analytics.py:10.

## Problem breakdown
1. The dashboard page loads in 4.2s (src/web/dashboard.py:50).
2. 92% of sessions end at /login before the dashboard ever loads.
3. Support tickets: 41 of 50 mention login failures, 2 mention speed.

## Assumptions challenged
- "The page is slow, so speed is the problem": challenged -- analytics in
  src/web/analytics.py:10 show 92% of users never reach the page; speed
  affects only the 8% who get through.
- "Users complain about speed": challenged -- 41 of 50 tickets mention
  login failures, not speed.

## Angles considered
- The inverse: optimize the dashboard first anyway.
- Fixing login first, then measuring whether speed still matters.

## Applicability check (the lawyer move)
### Stated problem
Make the slow page faster.

### Reframed problem (or: the stated problem stands)
Users never reach the page -- 92% drop off at login before the dashboard
ever loads. The real problem is the login funnel, not page speed.

### Evidence
- Analytics (src/web/analytics.py:10): 92% of sessions end at /login;
  dashboard pageviews are 3% of logins.
- Support tickets: 41 of 50 mention login failures, 2 mention speed.

### User confirmation
<!-- no confirmation yet -->
"""


class TestLawyerMove:
    """The lawyer move: research asks 'is this the actual problem?' and
    planning waits for the user's answer -- stated problem vs. reframed
    problem, with the evidence."""

    def test_reframe_blocks_planning_until_user_confirms(self, driver: loops.RpiDriver) -> None:
        state = driver.new("make the slow page faster")
        _write_research(driver, state, WRONG_PROBLEM_RESEARCH)
        # The research is otherwise valid, but the user hasn't confirmed.
        missing = driver.check(state)
        assert len(missing) == 1
        assert "no user confirmation" in missing[0]
        # The gate puts the stated-vs-reframed question to the user directly:
        # you asked me to solve X, but the evidence says the real problem
        # is Y -- which do we solve?
        with pytest.raises(loops.ProblemUnconfirmed) as exc_info:
            driver.advance(state)
        question = exc_info.value.question
        assert "make the slow page faster" in question.lower()
        assert "users never reach the page" in question.lower()
        assert "which do we solve" in question.lower()
        # The user picks the reframe: the confirmation records the real
        # problem, and the artifact carries the confirmation line.
        confirmation = driver.confirm_problem(
            state, by="Luke", reframed="Users never reach the page."
        )
        assert confirmation["verdict"] == "reframed"
        assert confirmation["solve"] == "Users never reach the page."
        line = driver.problem_confirmation_line(state)
        assert "reframed" in line
        path = driver.project_root / state.research_artifact
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("<!-- no confirmation yet -->", line),
            encoding="utf-8",
        )
        assert driver.check(state) == []
        # The spine's pair-plan step is mandatory: confirmed research leads
        # to pair-plan, never straight to plan.
        assert driver.advance(state) == "pair-plan"
        reloaded = driver.load(state.id)
        assert reloaded.problem_confirmation["solve"] == "Users never reach the page."

    def test_reentering_research_clears_the_confirmation(self, driver: loops.RpiDriver) -> None:
        """The confirmation attested the old research. Re-entry means the
        research is under re-examination, so the lawyer move asks again
        instead of planning on a stale attestation."""
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        assert driver.check(state) == []
        _confirm_problem(driver, state)
        state = _advance_to_plan(driver, state)
        assert state.phase == "plan"
        driver.reenter_phase(state, "research", reason="recheck the sources")
        state = driver.load(state.id)
        assert state.problem_confirmation is None
        with pytest.raises(loops.ProblemUnconfirmed):
            driver.advance(state)


class TestPlanValidation:
    def _research_done(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        assert driver.check(state) == []
        _confirm_problem(driver, state)
        return _advance_to_plan(driver, state)

    def test_valid_plan_passes(self, driver: loops.RpiDriver) -> None:
        state = self._research_done(driver)
        _write_plan(driver, state, PLAN_OK)
        assert driver.validate_current(state) == []

    def test_plan_missing_section_names_it(self, driver: loops.RpiDriver) -> None:
        state = self._research_done(driver)
        text = "\n".join(line for line in PLAN_OK.splitlines() if line.strip() != "## Rollback")
        _write_plan(driver, state, text)
        missing = driver.validate_current(state)
        assert missing == ["plan missing required section: 'rollback'"]

    def test_plan_nonexistent_scope_path_names_it(self, driver: loops.RpiDriver) -> None:
        state = self._research_done(driver)
        text = PLAN_OK.replace("`src/awino/cli/loopctl.py`", "`src/awino/cli/does-not-exist.py`")
        _write_plan(driver, state, text)
        missing = driver.validate_current(state)
        assert missing == ["scope path does not exist in repo: 'src/awino/cli/does-not-exist.py'"]

    def test_plan_accepts_acceptance_synonym(self, driver: loops.RpiDriver) -> None:
        state = self._research_done(driver)
        text = PLAN_OK.replace("## Acceptance criteria", "## Acceptance")
        _write_plan(driver, state, text)
        assert driver.validate_current(state) == []


class TestApprovalGate:
    def _at_plan(self, driver: loops.RpiDriver, *, understood: bool = True) -> loops.LoopState:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        assert driver.check(state) == []
        _confirm_problem(driver, state)
        state = _advance_to_plan(driver, state)
        _write_plan(driver, state, PLAN_OK)
        assert driver.check(state) == []
        # The spine evaluates challenge (thinking) and understand
        # (comprehension) before honda-scope (approval), so the fixture
        # satisfies them to isolate the approval gate.
        _thinking_run(driver, state)
        if understood:
            _comprehend(driver, state)
            _paste_comprehension_block(driver, state)
            assert driver.check(state) == []
        return state

    def test_next_past_plan_without_approval_is_refused(self, driver: loops.RpiDriver) -> None:
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
        assert driver.check(reloaded) == []
        assert driver.advance(reloaded) == "implement"

    def test_approval_without_comprehension_is_refused(self, driver: loops.RpiDriver) -> None:
        """A plan approved without understanding is a rubber stamp: the
        approval gate refuses until the human has explained the plan back."""
        state = self._at_plan(driver, understood=False)
        with pytest.raises(loops.ComprehensionRequired, match="comprehension check incomplete"):
            driver.approve_plan(state, by="Luke", reason="ok")
        assert not driver.plan_approved(driver.load(state.id))


class TestThinkingGate:
    """The plan-approval thinking gate: a plan is approved only after a
    thinking-mode run, or an explicit human waiver that names its reason."""

    def _at_plan(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        assert driver.check(state) == []
        _confirm_problem(driver, state)
        state = _advance_to_plan(driver, state)
        _write_plan(driver, state, PLAN_OK)
        assert driver.check(state) == []
        return state

    def _understood(self, driver: loops.RpiDriver, state: loops.LoopState) -> None:
        _comprehend(driver, state)
        _paste_comprehension_block(driver, state)

    def test_approval_without_thinking_is_refused(self, driver: loops.RpiDriver) -> None:
        state = self._at_plan(driver)
        self._understood(driver, state)
        with pytest.raises(loops.ApprovalRequired, match="critical thinking required"):
            driver.approve_plan(state, by="Luke", reason="looks good")
        assert not driver.thinking_satisfied(driver.load(state.id))

    def test_waiver_records_the_reason(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        state = self._at_plan(event_driver)
        self._understood(event_driver, state)
        waiver = event_driver.waive_thinking(
            state, by="Luke", reason="trivial two-line change; premortem would add nothing"
        )
        assert waiver["reason"].startswith("trivial two-line change")
        assert waiver["by"] == "Luke"
        reloaded = event_driver.load(state.id)
        assert event_driver.thinking_satisfied(reloaded)
        kinds = [event.kind for event in loop_ledger.loop_events(state.id)]
        assert "thinking_waived" in kinds
        # The waiver satisfies the gate: approval proceeds.
        event_driver.approve_plan(reloaded, by="Luke", reason="waived, understood")
        assert event_driver.plan_approved(event_driver.load(state.id))

    def test_inline_waiver_on_approve(self, driver: loops.RpiDriver) -> None:
        state = self._at_plan(driver)
        self._understood(driver, state)
        driver.approve_plan(
            state,
            by="Luke",
            reason="waived, understood",
            waive_reason="spike already premortemed yesterday",
        )
        reloaded = driver.load(state.id)
        assert reloaded.thinking_waiver["reason"] == "spike already premortemed yesterday"
        assert driver.plan_approved(reloaded)

    def test_thinking_run_satisfies_the_gate(self, driver: loops.RpiDriver) -> None:
        state = self._at_plan(driver)
        self._understood(driver, state)
        _thinking_run(driver, state)
        assert driver.thinking_satisfied(state)
        driver.approve_plan(state, by="Luke", reason="premortem run, understood")
        assert driver.plan_approved(driver.load(state.id))


class TestImplementHandoff:
    def _at_implement(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = driver.new("add an RPI loop driver")
        _write_research(driver, state, RESEARCH_OK)
        assert driver.check(state) == []
        _confirm_problem(driver, state)
        state = _advance_to_plan(driver, state)
        _write_plan(driver, state, PLAN_OK)
        _thinking_run(driver, state)
        _comprehend(driver, state)
        _paste_comprehension_block(driver, state)
        driver.approve_plan(state, by="Luke", reason="ok")
        assert driver.check(state) == []
        driver.advance(state)
        return driver.load(state.id)

    def test_implement_validates_against_an_open_rpi_run(self, driver: loops.RpiDriver) -> None:
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
        assert driver.check(state) == []
        _confirm_problem(driver, state)
        state = _advance_to_plan(driver, state)
        _write_plan(driver, state, PLAN_OK)
        _thinking_run(driver, state)
        _comprehend(driver, state)
        _paste_comprehension_block(driver, state)
        driver.approve_plan(state, by="Luke", reason="ok")
        assert driver.check(state) == []
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
    def test_three_failed_validations_lock_the_loop(self, driver: loops.RpiDriver) -> None:
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


def _loop_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("LOOP"):
            return line.split(None, 1)[1].strip()
    raise AssertionError("no LOOP line in output")


class TestLoopCli:
    def test_run_rpi_prints_phase_1_prompt(self, cli_env: Path) -> None:
        result = runner.invoke(loop_app, ["run", "rpi", "--task", "add an RPI loop driver"])
        assert result.exit_code == 0, result.output
        assert "LOOP" in result.output
        assert "Phase 1" in result.output
        assert "RESEARCH_CONTAMINATION" in result.output
        assert "ARTIFACT" in result.output

    def test_next_with_missing_artifact_prints_what_is_missing(self, cli_env: Path) -> None:
        assert runner.invoke(loop_app, ["run", "rpi", "--task", "x"]).exit_code == 0
        result = runner.invoke(loop_app, ["next"])
        assert result.exit_code == 1
        assert "VALIDATION_FAILED" in result.output
        assert "research artifact missing" in result.output

    def test_full_rpi_flow_end_to_end(self, cli_env: Path) -> None:
        project = cli_env
        run_result = runner.invoke(loop_app, ["run", "rpi", "--task", "add an RPI loop driver"])
        assert run_result.exit_code == 0, run_result.output
        research_path = project / _artifact_path(run_result.output)
        research_path.parent.mkdir(parents=True, exist_ok=True)
        research_path.write_text(RESEARCH_OK, encoding="utf-8")

        # The lawyer move: research validates, but the problem is unconfirmed --
        # the gate puts the stated-vs-reframed question to the user directly.
        unconfirmed = runner.invoke(loop_app, ["next"])
        assert unconfirmed.exit_code == 1, unconfirmed.output
        assert "which do we solve" in unconfirmed.output

        status = runner.invoke(loop_app, ["status"])
        assert status.exit_code == 0, status.output
        assert "problem: unconfirmed" in status.output

        confirmed = runner.invoke(loop_app, ["confirm-problem", "--confirmed"])
        assert confirmed.exit_code == 0, confirmed.output
        assert "PROBLEM_CONFIRMED" in confirmed.output
        assert "PASTE" in confirmed.output

        next_result = runner.invoke(loop_app, ["next"])
        assert next_result.exit_code == 0, next_result.output
        # The spine's pair-plan step is mandatory: research leads to
        # pair-plan, never straight to plan.
        assert "ADVANCED  phase=pair-plan" in next_result.output
        assert "Phase 2" in next_result.output
        pairing_path = project / _artifact_path(next_result.output)
        pairing_path.parent.mkdir(parents=True, exist_ok=True)
        pairing_path.write_text(PAIRING_OK, encoding="utf-8")

        # The brief validates, but the pairing questions are unanswered --
        # the spine refuses to leave pair-plan, naming them.
        unanswered = runner.invoke(loop_app, ["next"])
        assert unanswered.exit_code == 1, unanswered.output
        assert "REFUSED" in unanswered.output
        assert "Q1" in unanswered.output

        answered = runner.invoke(
            loop_app,
            ["answer", "--question", "Q1", "--answer", "extract a spine module"],
        )
        assert answered.exit_code == 0, answered.output
        assert "ANSWERED  Q1" in answered.output
        answered = runner.invoke(
            loop_app,
            ["answer", "--question", "Q2", "--answer", "Luke owns it long-term"],
        )
        assert answered.exit_code == 0, answered.output
        assert "All questions answered" in answered.output

        plan_result = runner.invoke(loop_app, ["next"])
        assert plan_result.exit_code == 0, plan_result.output
        assert "ADVANCED  phase=plan" in plan_result.output
        assert "Phase 3" in plan_result.output
        plan_path = project / _artifact_path(plan_result.output)

        # Plan validates but the spine refuses the advance: the challenge
        # step (thinking) comes before the honda-scope step (approval).
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
        assert approved.exit_code == 1, approved.output
        assert "thinking" in approved.output.lower()

        # The minimum bar: one recorded thinking run. --record validates the
        # premortem, writes its insights to working memory, and records the
        # run on the loop.
        thinking_path = project / "thoughts" / "thinking" / "premortem.md"
        thinking_path.parent.mkdir(parents=True, exist_ok=True)
        thinking_path.write_text(PREMORTEM_OK, encoding="utf-8")
        recorded = runner.invoke(
            loop_app, ["think", "--mode", "premortem", "--record", str(thinking_path)]
        )
        assert recorded.exit_code == 0, recorded.output
        assert "THINK_RECORDED" in recorded.output

        approved = runner.invoke(
            loop_app, ["approve", "--by", "Luke", "--reason", "explicit enough"]
        )
        assert approved.exit_code == 1, approved.output
        # "Execute when comfortable and understanding": comprehension comes
        # BEFORE approval -- the driver enters teach-back instead of approving.
        assert "comprehension" in approved.output.lower()
        assert "MISSING" in approved.output

        # Explain the plan back -- the explanation must reference the key
        # decisions by name -- then answer the plan's probes.
        explained = runner.invoke(
            loop_app,
            [
                "explain",
                "--text",
                "We will extract a spine module owning the "
                "ordered precondition chain. Chose to extract a spine module "
                "(Q1); followed the default recommendation because one ordered "
                "list owns the whole chain. Luke owns the spine module "
                "long-term (Q2).",
            ],
        )
        assert explained.exit_code == 0, explained.output
        for qid, answer in (
            ("P1", "one list owns the order; if wrong, gates scatter again"),
            ("P2", "Luke reviews spine changes; if wrong, no one tends it"),
        ):
            probed = runner.invoke(
                loop_app, ["probe-answer", "--question", qid, "--answer", answer]
            )
            assert probed.exit_code == 0, probed.output

        # Paste the comprehension-check record the status command prints
        # into the plan's decisions section, as the documented workflow
        # requires.
        status = runner.invoke(loop_app, ["status"])
        assert status.exit_code == 0, status.output
        assert "COMPREHENSION_RECORD" in status.output
        block_lines = []
        copying = False
        for line in status.output.splitlines():
            if line.startswith("COMPREHENSION_RECORD"):
                copying = True
                continue
            if copying:
                if line.startswith("  "):
                    block_lines.append(line[2:])
                else:
                    break
        assert block_lines, status.output
        with plan_path.open("a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(block_lines))

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
        run_dir = project / ".awino" / "run" / "run-abc"
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


# ── ledger integration: the loop audit trail ─────────────────────────────────


class TestLoopEventPersistence:
    def test_record_and_read_round_trip(self, loop_ledger: Ledger) -> None:
        from awino.enforce import LoopEvent

        loop_ledger.record_loop_event(
            LoopEvent(
                loop_id="rpi-1",
                loop_kind="rpi",
                phase="research",
                kind="loop_started",
                at="2026-09-11T08:00:00+00:00",
                detail="task: test the trail",
            )
        )
        events = loop_ledger.loop_events("rpi-1")
        assert len(events) == 1
        event = events[0]
        assert event.loop_id == "rpi-1"
        assert event.loop_kind == "rpi"
        assert event.phase == "research"
        assert event.kind == "loop_started"
        assert event.at == "2026-09-11T08:00:00+00:00"
        assert event.detail == "task: test the trail"

    def test_events_are_oldest_first_and_filterable_by_loop(self, loop_ledger: Ledger) -> None:
        from awino.enforce import LoopEvent

        for loop_id, kind in (("a", "loop_started"), ("b", "loop_started"), ("a", "loop_closed")):
            loop_ledger.record_loop_event(
                LoopEvent(
                    loop_id=loop_id,
                    loop_kind="rpi",
                    phase="research",
                    kind=kind,
                    at="2026-09-11T08:00:00+00:00",
                )
            )
        assert [e.kind for e in loop_ledger.loop_events()] == [
            "loop_started",
            "loop_started",
            "loop_closed",
        ]
        assert [e.kind for e in loop_ledger.loop_events("a")] == [
            "loop_started",
            "loop_closed",
        ]

    def test_missing_trail_file_reads_as_empty(self, tmp_path: Path) -> None:
        # Loops that ran before the trail existed leave no events; callers
        # fall back to the run-level heuristic.
        assert Ledger(tmp_path / ".awino").loop_events() == []

    def test_unknown_event_kind_is_refused(self, loop_ledger: Ledger) -> None:
        from awino.enforce import LoopEvent

        with pytest.raises(LedgerError, match="unknown loop event kind"):
            loop_ledger.record_loop_event(
                LoopEvent(
                    loop_id="x",
                    loop_kind="rpi",
                    phase="research",
                    kind="made_up",
                    at="2026-09-11T08:00:00+00:00",
                )
            )

    def test_driver_without_a_ledger_emits_nothing(
        self, driver: loops.RpiDriver, tmp_path: Path
    ) -> None:
        state = driver.new("no ledger wired")
        driver.record_failure(state)
        assert not (tmp_path / ".awino" / "loops.jsonl").exists()


class TestLedgerTrail:
    """Acceptance: a full RPI loop leaves the complete trail in order."""

    def test_full_rpi_loop_emits_complete_ordered_trail(self, cli_env: Path) -> None:
        project = cli_env
        run_result = runner.invoke(loop_app, ["run", "rpi", "--task", "add an RPI loop driver"])
        assert run_result.exit_code == 0, run_result.output
        loop_id = _loop_id(run_result.output)

        research_path = project / _artifact_path(run_result.output)
        research_path.parent.mkdir(parents=True, exist_ok=True)
        research_path.write_text(RESEARCH_OK, encoding="utf-8")

        # The lawyer move first: the problem is confirmed before planning.
        confirmed = runner.invoke(loop_app, ["confirm-problem", "--confirmed"])
        assert confirmed.exit_code == 0, confirmed.output
        assert "PROBLEM_CONFIRMED" in confirmed.output

        next_result = runner.invoke(loop_app, ["next"])
        assert next_result.exit_code == 0, next_result.output
        # The spine's pair-plan step is mandatory: research leads to
        # pair-plan, never straight to plan.
        assert "ADVANCED  phase=pair-plan" in next_result.output
        pairing_path = project / _artifact_path(next_result.output)
        pairing_path.parent.mkdir(parents=True, exist_ok=True)
        pairing_path.write_text(PAIRING_OK, encoding="utf-8")

        for qid, answer in (
            ("Q1", "extract a spine module"),
            ("Q2", "Luke owns it long-term"),
        ):
            answered = runner.invoke(loop_app, ["answer", "--question", qid, "--answer", answer])
            assert answered.exit_code == 0, answered.output

        plan_result = runner.invoke(loop_app, ["next"])
        assert plan_result.exit_code == 0, plan_result.output
        assert "ADVANCED  phase=plan" in plan_result.output
        plan_path = project / _artifact_path(plan_result.output)

        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(PLAN_OK, encoding="utf-8")

        # The plan validates, but the spine refuses the advance: the
        # challenge step (thinking) comes before honda-scope (approval).
        refused = runner.invoke(loop_app, ["next"])
        assert refused.exit_code == 1
        assert "REFUSED" in refused.output

        approved = runner.invoke(
            loop_app, ["approve", "--by", "Luke", "--reason", "explicit enough"]
        )
        assert approved.exit_code == 1, approved.output
        assert "thinking" in approved.output.lower()

        thinking_path = project / "thoughts" / "thinking" / "premortem.md"
        thinking_path.parent.mkdir(parents=True, exist_ok=True)
        thinking_path.write_text(PREMORTEM_OK, encoding="utf-8")
        recorded = runner.invoke(
            loop_app, ["think", "--mode", "premortem", "--record", str(thinking_path)]
        )
        assert recorded.exit_code == 0, recorded.output

        # "Execute when comfortable and understanding": comprehension comes
        # BEFORE approval -- the explanation references the key decisions
        # by name, and every probe is answered.
        explained = runner.invoke(
            loop_app,
            [
                "explain",
                "--text",
                "We will extract a spine module owning the "
                "ordered precondition chain. Chose to extract a spine module "
                "(Q1); followed the default recommendation because one ordered "
                "list owns the whole chain. Luke owns the spine module "
                "long-term (Q2).",
            ],
        )
        assert explained.exit_code == 0, explained.output
        for qid, answer in (
            ("P1", "one list owns the order; if wrong, gates scatter again"),
            ("P2", "Luke reviews spine changes; if wrong, no one tends it"),
        ):
            probed = runner.invoke(
                loop_app, ["probe-answer", "--question", qid, "--answer", answer]
            )
            assert probed.exit_code == 0, probed.output

        # The plan document records the comprehension check: paste the block
        # the status command prints.
        status = runner.invoke(loop_app, ["status"])
        assert status.exit_code == 0, status.output
        assert "COMPREHENSION_RECORD" in status.output
        block_lines = []
        copying = False
        for line in status.output.splitlines():
            if line.startswith("COMPREHENSION_RECORD"):
                copying = True
                continue
            if copying:
                if line.startswith("  "):
                    block_lines.append(line[2:])
                else:
                    break
        assert block_lines, status.output
        with plan_path.open("a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(block_lines))

        approved = runner.invoke(
            loop_app, ["approve", "--by", "Luke", "--reason", "explicit enough"]
        )
        assert approved.exit_code == 0, approved.output

        # Re-checking the already-validated plan emits no duplicate event.
        advanced = runner.invoke(loop_app, ["next"])
        assert advanced.exit_code == 0, advanced.output
        assert "ADVANCED  phase=implement" in advanced.output

        # Fake an open ledger run tagged --loop rpi so the handoff verifies.
        run_dir = project / ".awino" / "run" / "run-abc"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_dir.joinpath("run.json").write_text(
            json.dumps({"run_id": "run-abc", "loop": "rpi", "terminal_state": None}),
            encoding="utf-8",
        )
        handoff = runner.invoke(loop_app, ["next"])
        assert handoff.exit_code == 0, handoff.output
        assert "HANDOFF" in handoff.output

        ledger = Ledger(project / ".awino")
        events = ledger.loop_events(loop_id)
        assert [(event.kind, event.phase) for event in events] == [
            ("loop_started", "research"),
            ("phase_started", "research"),
            ("problem_confirmed", "research"),
            ("artifact_validated", "research"),
            ("success_criteria_evaluated", "research"),
            ("skill_receipt", "research"),
            ("phase_started", "pair-plan"),
            ("human_answered", "pair-plan"),
            ("human_answered", "pair-plan"),
            ("artifact_validated", "pair-plan"),
            ("success_criteria_evaluated", "pair-plan"),
            ("skill_receipt", "pair-plan"),
            ("phase_started", "plan"),
            ("artifact_validated", "plan"),
            ("success_criteria_evaluated", "plan"),
            ("skill_receipt", "plan"),
            ("thinking_run", "plan"),
            ("comprehension_recorded", "plan"),
            ("comprehension_recorded", "plan"),
            ("comprehension_recorded", "plan"),
            ("approval_granted", "plan"),
            # The plan artifact changed after the first validation (the
            # comprehension-check block was pasted in), so the receipt was
            # refreshed -- the receipt attests the artifact's live content.
            # No duplicate artifact_validated: the plan was already valid.
            ("skill_receipt", "plan"),
            ("phase_started", "implement"),
            ("loop_closed", "implement"),
        ]
        assert all(event.loop_id == loop_id for event in events)
        assert all(event.loop_kind == "rpi" for event in events)
        assert all(event.at for event in events)
        # The trail is ledger-level, not inside a per-run dir.
        assert (project / ".awino" / "loops.jsonl").is_file()


class TestArtifactRejection:
    def test_rejected_artifact_emits_event_with_reason(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        state = event_driver.new("add an RPI loop driver")
        _write_research(event_driver, state, "too short, see src/awino/loops.py:1\n")
        missing = event_driver.check(state)
        assert missing
        events = loop_ledger.loop_events(state.id)
        assert [event.kind for event in events] == [
            "loop_started",
            "phase_started",
            "artifact_rejected",
        ]
        rejected = events[-1]
        assert rejected.phase == "research"
        assert "too short" in rejected.detail
        assert any("too short" in item for item in missing)
        assert rejected.detail in "; ".join(missing)

    def test_rejected_plan_names_the_missing_section(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        state = event_driver.new("add an RPI loop driver")
        _write_research(event_driver, state, RESEARCH_OK)
        assert event_driver.check(state) == []
        _confirm_problem(event_driver, state)
        state = _advance_to_plan(event_driver, state)
        text = "\n".join(line for line in PLAN_OK.splitlines() if line.strip() != "## Rollback")
        _write_plan(event_driver, state, text)
        missing = event_driver.check(state)
        assert missing == ["plan missing required section: 'rollback'"]
        rejected = loop_ledger.loop_events(state.id)[-1]
        assert rejected.kind == "artifact_rejected"
        assert rejected.phase == "plan"
        assert rejected.detail == "plan missing required section: 'rollback'"


# ── loop back ────────────────────────────────────────────────────────────────


class TestBackDriver:
    def _at_plan(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = driver.new("add an RPI loop driver")
        assert driver.check(state)  # no artifact yet: attempt 1, rejected
        _write_research(driver, state, RESEARCH_OK)
        assert driver.check(state) == []
        _confirm_problem(driver, state)
        return _advance_to_plan(driver, state)

    def test_back_reenters_earlier_phase_with_fresh_attempts(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        state = self._at_plan(event_driver)
        assert state.phase == "plan"
        assert state.attempts["research"] == 1
        event_driver.reenter_phase(state, "research", reason="recheck the sources")
        state = event_driver.load(state.id)
        assert state.phase == "research"
        assert state.attempts["research"] == 0
        events = loop_ledger.loop_events(state.id)
        reentered = [e for e in events if e.kind == "phase_reentered"]
        assert len(reentered) == 1
        assert "recheck the sources" in reentered[0].detail
        assert "'research'" in reentered[0].detail
        # The artifact file is kept and re-validates on next; the mission's
        # success criteria are judged right after each validation.
        assert event_driver.check(state) == []
        tail_kinds = [e.kind for e in loop_ledger.loop_events(state.id)[-2:]]
        assert tail_kinds == ["artifact_validated", "success_criteria_evaluated"]

    def test_back_refuses_unknown_phase(self, event_driver: loops.RpiDriver) -> None:
        state = event_driver.new("add an RPI loop driver")
        with pytest.raises(loops.LoopError, match="unknown phase 'deploy'"):
            event_driver.reenter_phase(state, "deploy")

    def test_back_refuses_phase_ahead_of_current(self, event_driver: loops.RpiDriver) -> None:
        state = event_driver.new("add an RPI loop driver")
        with pytest.raises(loops.LoopError, match="only re-enters earlier phases"):
            event_driver.reenter_phase(state, "plan")
        with pytest.raises(loops.LoopError, match="only re-enters earlier phases"):
            event_driver.reenter_phase(state, "research")

    def test_back_refuses_a_finished_loop(self, event_driver: loops.RpiDriver) -> None:
        state = self._at_plan(event_driver)
        _write_plan(event_driver, state, PLAN_OK)
        _thinking_run(event_driver, state)
        _comprehend(event_driver, state)
        _paste_comprehension_block(event_driver, state)
        event_driver.approve_plan(state, by="Luke", reason="ok")
        assert event_driver.check(state) == []
        event_driver.advance(state)
        event_driver.advance(event_driver.load(state.id))
        done = event_driver.load(state.id)
        assert done.phase == "done"
        with pytest.raises(loops.LoopError, match="loop is done"):
            event_driver.reenter_phase(done, "research")

    def test_back_clears_a_three_strikes_lock(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        # Lock the loop on the plan phase: three failed plan validations.
        state = event_driver.new("add an RPI loop driver")
        _write_research(event_driver, state, RESEARCH_OK)
        assert event_driver.check(state) == []
        _confirm_problem(event_driver, state)
        state = _advance_to_plan(event_driver, state)
        _write_plan(event_driver, state, "too short\n")
        for _ in range(loops.MAX_ATTEMPTS):
            assert event_driver.check(state)
            state = event_driver.load(state.id)
        assert state.phase == "plan"
        assert state.locked
        with pytest.raises(loops.LoopLocked):
            event_driver.advance(state)
        # Re-entering an earlier phase is the human intervention the lock
        # asks for: the lock clears with the fresh attempt count.
        event_driver.reenter_phase(state, "research", reason="human re-check")
        state = event_driver.load(state.id)
        assert state.phase == "research"
        assert not state.locked
        assert state.attempts["research"] == 0
        assert event_driver.check(state) == []


class TestBackCli:
    def _past_research(self, project: Path) -> None:
        """Past research: the loop sits in pair-plan. The spine's pair-plan
        step is mandatory -- research always leads to pair-plan, never
        straight to plan."""
        run_result = runner.invoke(loop_app, ["run", "rpi", "--task", "add an RPI loop driver"])
        assert run_result.exit_code == 0, run_result.output
        research_path = project / _artifact_path(run_result.output)
        research_path.parent.mkdir(parents=True, exist_ok=True)
        research_path.write_text(RESEARCH_OK, encoding="utf-8")
        # The lawyer move: confirm the problem before research may advance.
        confirmed = runner.invoke(loop_app, ["confirm-problem", "--confirmed"])
        assert confirmed.exit_code == 0, confirmed.output
        advanced = runner.invoke(loop_app, ["next"])
        assert advanced.exit_code == 0, advanced.output
        assert "ADVANCED  phase=pair-plan" in advanced.output

    def test_back_reenters_and_reprints_the_prompt(self, cli_env: Path) -> None:
        project = cli_env
        self._past_research(project)
        result = runner.invoke(loop_app, ["back", "research", "--reason", "recheck"])
        assert result.exit_code == 0, result.output
        assert "REENTERED  phase=research" in result.output
        assert "fresh count on re-entry" in result.output
        assert "reason: recheck" in result.output
        assert "Phase 1" in result.output
        status = runner.invoke(loop_app, ["status"])
        assert "phase: research" in status.output
        assert "research=0" in status.output

    def test_back_keeps_the_artifact_but_next_revalidates(self, cli_env: Path) -> None:
        project = cli_env
        self._past_research(project)
        assert runner.invoke(loop_app, ["back", "research"]).exit_code == 0
        # Re-entering research clears the problem confirmation: the lawyer
        # move asks again on re-examined research instead of planning on a
        # stale attestation.
        refused = runner.invoke(loop_app, ["next"])
        assert refused.exit_code == 1
        assert "which do we solve" in refused.output
        confirmed = runner.invoke(loop_app, ["confirm-problem", "--confirmed"])
        assert confirmed.exit_code == 0, confirmed.output
        advanced = runner.invoke(loop_app, ["next"])
        assert advanced.exit_code == 0, advanced.output
        assert "ADVANCED  phase=pair-plan" in advanced.output
        ledger = Ledger(project / ".awino")
        kinds = [event.kind for event in ledger.loop_events()]
        tail = kinds[kinds.index("phase_reentered") :]
        assert tail == [
            "phase_reentered",
            "phase_started",
            # The refused `next` re-validated the kept artifact (and judged
            # it against the mission's success criteria) before the
            # lawyer-move gate stopped the advance. The advancing `next`
            # does not re-validate the unchanged artifact -- it just moves
            # through the mandatory pair-plan step.
            "artifact_validated",
            "success_criteria_evaluated",
            "problem_confirmed",
            "phase_started",
        ]

    def test_back_refuses_unknown_phase(self, cli_env: Path) -> None:
        project = cli_env
        self._past_research(project)
        result = runner.invoke(loop_app, ["back", "deploy"])
        assert result.exit_code == 1
        assert "REFUSED" in result.output
        assert "unknown phase" in result.output

    def test_back_refuses_a_phase_ahead_of_current(self, cli_env: Path) -> None:
        project = cli_env
        self._past_research(project)
        result = runner.invoke(loop_app, ["back", "implement"])
        assert result.exit_code == 1
        assert "REFUSED" in result.output
        assert "earlier phases" in result.output
