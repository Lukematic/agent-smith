"""Tests for the A.W.I.N.O. spine: the owner's 10-step precondition chain.

The spine (loops.RpiDriver.SPINE) is one ordered, enforced state machine:

    1. mission        6. honda-scope
    2. pair-plan      7. beyond-honda
    3. challenge      8. capture
    4. understand     9. work
    5. real-problem  10. outcome verdict

Every artifact is the next step's precondition: advance() evaluates the
steps in owner order and refuses at the first missing one, naming the
missing artifact. Waivers are explicit, human-only, reasoned, and
ledger-recorded (the thinking waiver). Precedent (working_memory
case law) surfaces where new decisions are recorded -- pair-planning
answers, thinking waivers, plan approvals -- never blocking, never
invented: no match means silence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import heilmeier, loops, working_memory
from awino.cli.loopctl import loop_app
from awino.enforce import Ledger, LoopEvent
from awino.paths import project_state_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"
runner = CliRunner()

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
- none; the shape is settled.
"""

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
"""


def _write_mission(project: Path) -> None:
    """A valid mission: objective + an exam wired to a verification command.
    The spine's step 1 refuses all advancement without it."""
    heilmeier.save(
        project / ".awino",
        heilmeier.Catechism(
            answers={
                "objective": "exercise the test loop honestly",
                "exams": "the loop advances through its phases -> true",
            }
        ),
    )


@pytest.fixture()
def project(tmp_path: Path) -> Path:
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
def state_root(project: Path) -> Path:
    return project_state_dir(project)


@pytest.fixture()
def loop_ledger(state_root: Path) -> Ledger:
    return Ledger(state_root)


@pytest.fixture()
def driver(
    project: Path, loop_ledger: Ledger, state_root: Path
) -> loops.RpiDriver:
    """A driver wired to ledger + working memory, so the capture step's
    boundary checks (ledger trail, checklist) are live. The loops dir
    matches the CLI's (state_root / "loops") so CLI commands resolve the
    same loop."""
    return loops.RpiDriver(
        project_root=project,
        loops_dir=state_root / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
        ledger=loop_ledger,
        state_root=state_root,
    )


@pytest.fixture()
def cli_env(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AWINO_PROJECT", str(project))
    return project


def _write(
    driver: loops.RpiDriver, state: loops.LoopState, phase: str, text: str
) -> None:
    rel = {
        "research": state.research_artifact,
        "pair-plan": state.pairing_artifact,
        "plan": state.plan_artifact,
    }[phase]
    path = driver.project_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _at_research(driver: loops.RpiDriver) -> loops.LoopState:
    """A fresh loop with valid research written (problem not confirmed)."""
    state = driver.new("add an RPI loop driver")
    _write(driver, state, "research", RESEARCH_OK)
    assert driver.check(state) == []
    return driver.load(state.id)


def _at_pair_plan(driver: loops.RpiDriver) -> loops.LoopState:
    """Research confirmed; now at pair-plan (brief not yet written)."""
    state = _at_research(driver)
    driver.confirm_problem(state, by="Luke")
    assert driver.check(state) == []
    assert driver.advance(state) == "pair-plan"
    return driver.load(state.id)


def _at_plan(driver: loops.RpiDriver) -> loops.LoopState:
    """Pair-plan answered; now at plan (nothing else done)."""
    state = _at_pair_plan(driver)
    _write(driver, state, "pair-plan", PAIRING_OK)
    assert driver.check(state) == []
    for qid, _ in driver.pairing_questions(state):
        driver.record_pair_answer(
            state, qid, "answer", "use the default recommendation", by="Luke"
        )
    assert driver.check(state) == []
    assert driver.advance(state) == "plan"
    state = driver.load(state.id)
    _write(driver, state, "plan", PLAN_OK)
    assert driver.check(state) == []
    return state


def _comprehend(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    """Satisfy the understand step: explanation naming the key decisions,
    every probe answered."""
    driver.record_explanation(
        state,
        "We will extract a spine module owning the ordered precondition "
        "chain. Chose to extract a spine module (Q1); followed the default "
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


def _thinking_run(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    driver.record_thinking_run(state, "premortem", by="Luke", memory_id="D-0001")


def _paste_comprehension_block(
    driver: loops.RpiDriver, state: loops.LoopState
) -> None:
    """Paste the comprehension-check record into the plan artifact -- the
    documented workflow: once comprehension work exists in loop state, the
    plan document itself records it (the validator requires the block)."""
    path = driver.project_root / state.plan_artifact
    block = driver.comprehension_record_block(state)
    path.write_text(
        path.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + block,
        encoding="utf-8",
    )


def _at_plan_ready(driver: loops.RpiDriver) -> loops.LoopState:
    """At plan with challenge + understand satisfied and the plan approved:
    every spine step but work/verdict is green."""
    state = _at_plan(driver)
    _thinking_run(driver, state)
    _comprehend(driver, state)
    _paste_comprehension_block(driver, state)
    assert driver.check(state) == []
    driver.approve_plan(state, by="Luke", reason="explicit enough")
    return driver.load(state.id)


class _Ctx:
    """What a spine case's builder/voider may touch."""

    def __init__(self, driver, project, tmp_path, loop_ledger, state_root):
        self.driver = driver
        self.project = project
        self.tmp_path = tmp_path
        self.loop_ledger = loop_ledger
        self.state_root = state_root


def _build_research(ctx: _Ctx) -> tuple[loops.RpiDriver, loops.LoopState]:
    return ctx.driver, _at_research(ctx.driver)


def _build_pair_plan(ctx: _Ctx) -> tuple[loops.RpiDriver, loops.LoopState]:
    return ctx.driver, _at_pair_plan(ctx.driver)


def _build_plan(ctx: _Ctx) -> tuple[loops.RpiDriver, loops.LoopState]:
    return ctx.driver, _at_plan(ctx.driver)


def _build_plan_ready(ctx: _Ctx) -> tuple[loops.RpiDriver, loops.LoopState]:
    return ctx.driver, _at_plan_ready(ctx.driver)


def _void_nothing(
    ctx: _Ctx, driver: loops.RpiDriver, state: loops.LoopState
) -> tuple[loops.RpiDriver, loops.LoopState]:
    return driver, state


def _void_mission(
    ctx: _Ctx, driver: loops.RpiDriver, state: loops.LoopState
) -> tuple[loops.RpiDriver, loops.LoopState]:
    """Step 1 voided: the mission no longer validates (no objective)."""
    heilmeier.save(
        ctx.project / ".awino", heilmeier.Catechism(answers={})
    )
    return driver, driver.load(state.id)


def _void_thinking_undone(
    ctx: _Ctx, driver: loops.RpiDriver, state: loops.LoopState
) -> tuple[loops.RpiDriver, loops.LoopState]:
    # _at_plan does no thinking run: the void is the absence.
    return driver, state


def _add_thinking(
    ctx: _Ctx, driver: loops.RpiDriver, state: loops.LoopState
) -> tuple[loops.RpiDriver, loops.LoopState]:
    _thinking_run(driver, state)
    return driver, driver.load(state.id)


def _add_thinking_and_comprehension(
    ctx: _Ctx, driver: loops.RpiDriver, state: loops.LoopState
) -> tuple[loops.RpiDriver, loops.LoopState]:
    _thinking_run(driver, state)
    _comprehend(driver, state)
    return driver, driver.load(state.id)


def _void_beyond_honda(
    ctx: _Ctx, driver: loops.RpiDriver, state: loops.LoopState
) -> tuple[loops.RpiDriver, loops.LoopState]:
    """Step 7 voided: the non-default approach loses its effort label in
    the live brief (no re-check -- the spine reads the brief, not the
    receipt)."""
    path = driver.project_root / state.pairing_artifact
    text = path.read_text(encoding="utf-8")
    assert "effort: two days" in text  # the alternate's label
    path.write_text(text.replace("effort: two days\n", ""), encoding="utf-8")
    return driver, driver.load(state.id)


def _void_capture(
    ctx: _Ctx, driver: loops.RpiDriver, state: loops.LoopState
) -> tuple[loops.RpiDriver, loops.LoopState]:
    """Step 8 voided: the same loop state, but a driver whose ledger has
    no trail for it -- no phase_started boundary, no capture."""
    fresh_root = ctx.tmp_path / "fresh-state"
    fresh_root.mkdir(exist_ok=True)
    fresh_driver = loops.RpiDriver(
        project_root=ctx.project,
        loops_dir=ctx.state_root / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
        ledger=Ledger(fresh_root),
        state_root=ctx.state_root,
    )
    return fresh_driver, fresh_driver.load(state.id)


class TestSpineOrder:
    def test_spine_lists_the_owner_order(self) -> None:
        names = [step.name for step in loops.RpiDriver.SPINE]
        assert names == [
            "mission",
            "pair-plan",
            "challenge",
            "understand",
            "real-problem",
            "honda-scope",
            "beyond-honda",
            "capture",
            "work",
            "verdict",
        ]

    def test_every_step_refuse_names_the_step(self) -> None:
        """Refusals name the missing step: the human always knows which
        artifact to produce."""
        for step in loops.RpiDriver.SPINE:
            assert step.name, "every spine step has a name"
            assert step.artifact, f"step {step.name} names its artifact"

    def test_spine_status_renders_in_owner_order(
        self, driver: loops.RpiDriver
    ) -> None:
        state = _at_plan(driver)
        names = [name for name, _, _ in driver.spine_status(state)]
        assert names == [step.name for step in loops.RpiDriver.SPINE]


_MISSING_CASES = [
    # (step, builder, void, expected exception, message fragment)
    ("mission", _build_research, _void_mission, loops.SpineBlocked, "mission"),
    (
        "pair-plan",
        _build_pair_plan,
        _void_nothing,
        loops.SpineBlocked,
        "pair-plan",
    ),
    (
        "challenge",
        _build_plan,
        _void_thinking_undone,
        loops.ApprovalRequired,
        "critical thinking",
    ),
    (
        "understand",
        _build_plan,
        _add_thinking,
        loops.ComprehensionRequired,
        "comprehension",
    ),
    (
        "real-problem",
        _build_research,
        _void_nothing,
        loops.ProblemUnconfirmed,
        "which do we solve",
    ),
    (
        "honda-scope",
        _build_plan,
        _add_thinking_and_comprehension,
        loops.ApprovalRequired,
        "not approved",
    ),
    (
        "beyond-honda",
        _build_plan_ready,
        _void_beyond_honda,
        loops.SpineBlocked,
        "beyond-honda",
    ),
    (
        "capture",
        _build_plan_ready,
        _void_capture,
        loops.SpineBlocked,
        "capture",
    ),
]


class TestMissingPreconditions:
    """Every spine artifact is the next step's precondition: advance()
    refuses at the first missing one, naming the missing artifact."""

    @pytest.mark.parametrize(
        "step,build,void,exc,match",
        _MISSING_CASES,
        ids=[case[0] for case in _MISSING_CASES],
    )
    def test_missing_artifact_refuses_advance(
        self,
        driver: loops.RpiDriver,
        project: Path,
        tmp_path: Path,
        loop_ledger: Ledger,
        state_root: Path,
        step: str,
        build,
        void,
        exc: type,
        match: str,
    ) -> None:
        ctx = _Ctx(driver, project, tmp_path, loop_ledger, state_root)
        use_driver, state = build(ctx)
        use_driver, state = void(ctx, use_driver, state)
        with pytest.raises(exc) as exc_info:
            use_driver.advance(state)
        assert match.lower() in str(exc_info.value).lower()

    def test_satisfied_spine_advances(
        self, driver: loops.RpiDriver
    ) -> None:
        """The control: with every artifact present, the same advances
        the missing-cases refuse all succeed."""
        state = _at_research(driver)
        driver.confirm_problem(state, by="Luke")
        assert driver.check(driver.load(state.id)) == []
        assert driver.advance(driver.load(state.id)) == "pair-plan"


class TestThinkingWaiver:
    def test_waiver_with_reason_is_a_conscious_ledger_decision(
        self, driver: loops.RpiDriver, loop_ledger: Ledger, state_root: Path
    ) -> None:
        state = _at_plan(driver)
        _comprehend(driver, state)
        assert not driver.thinking_satisfied(state)
        reason = (
            "the failure modes are enumerated in the plan's rollback "
            "section; a premortem would repeat them"
        )
        waiver = driver.waive_thinking(state, by="Luke", reason=reason)
        assert waiver["by"] == "Luke"
        assert waiver["reason"] == reason
        # Ledger-recorded: the trail names the reason.
        waived = [
            e
            for e in loop_ledger.loop_events(state.id)
            if e.kind == "thinking_waived"
        ]
        assert len(waived) == 1
        assert reason in (waived[0].detail or "")
        # decisions.md records the why, keyed as a thinking-waiver.
        decisions = working_memory.Decisions(state_root)
        keyed = [e for e in decisions.entries() if e.key == f"{state.id}:thinking-waiver"]
        assert len(keyed) == 1
        assert keyed[0].why == reason

    def test_waiver_without_reason_does_not_waive(
        self, driver: loops.RpiDriver
    ) -> None:
        """A waiver with no reason is not a conscious decision: approval
        still demands thinking."""
        state = _at_plan(driver)
        with pytest.raises(loops.ApprovalRequired):
            driver.approve_plan(
                state, by="Luke", reason="explicit enough", waive_reason="  "
            )
        assert not driver.thinking_satisfied(driver.load(state.id))

    def test_direct_waiver_rejects_a_blank_reason(
        self, driver: loops.RpiDriver
    ) -> None:
        """The direct call is the same boundary as the CLI: a blank reason
        refuses instead of recording a why-less waiver."""
        state = _at_plan(driver)
        with pytest.raises(loops.LoopError, match="needs a reason"):
            driver.waive_thinking(state, by="Luke", reason="   ")
        assert not driver.thinking_satisfied(driver.load(state.id))

    def test_waiver_lets_advancement_proceed(
        self, driver: loops.RpiDriver
    ) -> None:
        """Waiver + comprehension + approval: the challenge step is
        satisfied and the loop advances past plan."""
        state = _at_plan(driver)
        _comprehend(driver, state)
        _paste_comprehension_block(driver, state)
        driver.waive_thinking(state, by="Luke", reason="risks are in the plan")
        driver.approve_plan(state, by="Luke", reason="explicit enough")
        assert driver.check(driver.load(state.id)) == []
        assert driver.advance(driver.load(state.id)) == "implement"


class TestFullSpineWalk:
    def test_walk_through_work_and_verdict(
        self,
        driver: loops.RpiDriver,
        loop_ledger: Ledger,
        cli_env: Path,
    ) -> None:
        """The whole chain, end to end: mission -> pair-plan -> challenge
        -> understand -> real-problem -> honda-scope -> beyond-honda ->
        capture -> work (implement handoff) -> outcome verdict."""
        state = _at_plan_ready(driver)
        kinds = [e.kind for e in loop_ledger.loop_events(state.id)]
        # Steps 1-8 left their trail on the way here.
        for expected in (
            "phase_started",
            "problem_confirmed",
            "human_answered",
            "thinking_run",
            "comprehension_recorded",
            "approval_granted",
        ):
            assert expected in kinds, kinds

        # Step 9 (work): the implement phase hands off to the gate ledger.
        assert driver.advance(state) == "implement"
        assert driver.advance(driver.load(state.id)) == "done"
        state = driver.load(state.id)
        assert state.phase == "done"
        kinds = [e.kind for e in loop_ledger.loop_events(state.id)]
        assert "loop_closed" in kinds

        # Step 10 (verdict): a done loop with no verdict still owes it --
        # work is ok, verdict is missing.
        status = {
            name: st for name, st, _ in driver.spine_status(state)
        }
        assert status["work"] == "ok"
        assert status["verdict"] == "missing"

        # `loop close` is the verdict boundary: it refuses without --verdict.
        closed = runner.invoke(loop_app, ["close", "--id", state.id])
        assert closed.exit_code != 0
        assert "--verdict" in closed.output

        # Record the verdict the way `loop close --verdict` does.
        loop_ledger.record_loop_event(
            LoopEvent(
                loop_id=state.id,
                loop_kind="rpi",
                phase="done",
                kind="outcome_verdict",
                at=datetime.now(UTC).isoformat(),
                detail=f"verdict: partial; loop_id: {state.id}",
            )
        )
        status = {
            name: st for name, st, _ in driver.spine_status(state)
        }
        assert status["verdict"] == "ok"
        assert all(
            st == "ok" for st in status.values()
        ), status


class TestPrecedent:
    def _decisions(self, state_root: Path) -> working_memory.Decisions:
        return working_memory.Decisions(state_root)

    def test_related_precedent_surfaces_with_past_outcome(
        self, driver: loops.RpiDriver, state_root: Path, loop_ledger: Ledger
    ) -> None:
        """Case law: a past similar pairing decision whose loop closed
        `partial` surfaces as 'last time you chose X because Z; outcome
        was partial.'"""
        old_loop = "rpi-2026-09-01-0000-deadbeef"
        self._decisions(state_root).record(
            decision=(
                "Q1: Which approach do you prefer, extend the driver or "
                "extract a spine module? -> ANSWER: extract a spine module"
            ),
            why=(
                "one ordered list owns the whole precondition chain, so "
                "the gates cannot scatter again"
            ),
            source="pair-planning Q1 in loop rpi-2026-09-01-0000-deadbeef",
            key=f"{old_loop}:Q1",
        )
        loop_ledger.record_loop_event(
            LoopEvent(
                loop_id=old_loop,
                loop_kind="rpi",
                phase="done",
                kind="outcome_verdict",
                at=datetime.now(UTC).isoformat(),
                detail=f"verdict: partial; loop_id: {old_loop}",
            )
        )
        state = _at_pair_plan(driver)
        _write(driver, state, "pair-plan", PAIRING_OK)
        assert driver.check(state) == []
        driver.record_pair_answer(
            state,
            "Q1",
            "answer",
            "extract a spine module, since one ordered list should own "
            "the whole precondition chain",
            by="Luke",
        )
        assert len(driver.last_precedents) == 1
        line = driver.last_precedents[0]
        assert line.startswith("last time you chose ")
        assert "extract a spine module" in line
        assert "outcome was partial" in line

    def test_unrelated_precedent_stays_silent(
        self, driver: loops.RpiDriver, state_root: Path
    ) -> None:
        """No shared keywords, no invention: an unrelated past decision
        produces no precedent line."""
        self._decisions(state_root).record(
            decision="Q1: Which database index strategy? -> ANSWER: gin index",
            why="full-text search over the audit log needs trigram support",
            source="pair-planning Q1 in loop rpi-2026-09-01-0000-deadbeef",
            key="rpi-2026-09-01-0000-deadbeef:Q1",
        )
        state = _at_pair_plan(driver)
        _write(driver, state, "pair-plan", PAIRING_OK)
        assert driver.check(state) == []
        driver.record_pair_answer(
            state, "Q1", "answer", "extract a spine module", by="Luke"
        )
        assert driver.last_precedents == []

    def test_precedent_ranking_is_deterministic(
        self, state_root: Path
    ) -> None:
        """More shared keywords first, then newer first, then lower ids --
        the same inputs always give the same order.

        The new decision's keywords: extract, spine, module, precondition,
        chain, gates, ordered, list, owns (9).
        """
        (state_root / working_memory.DECISIONS_FILENAME).write_text(
            "# Decisions\n"
            "\n"
            "## D-0001 — 2026-09-01\n"
            "decision: extract a spine module\n"
            "why: one list owns\n"
            "source: s1\n"
            "key: rpi-2026-09-01-0000-deadbeef:Q1\n"
            "at: 2026-09-01T00:00:00+00:00\n"
            "\n"
            "## D-0002 — 2026-09-05\n"
            "decision: extract a spine module for the precondition chain\n"
            "why: one ordered list owns the gates\n"
            "source: s2\n"
            "key: rpi-2026-09-01-0000-deadbeef:Q2\n"
            "at: 2026-09-05T00:00:00+00:00\n"
            "\n"
            "## D-0003 — 2026-09-08\n"
            "decision: extract a spine module for the precondition chain\n"
            "why: one ordered list owns the gates\n"
            "source: s3\n"
            "key: rpi-2026-09-01-0000-deadbeef:Q3\n"
            "at: 2026-09-08T00:00:00+00:00\n"
            "\n"
            "## D-0004 — 2026-09-08\n"
            "decision: extract a spine module for the precondition chain\n"
            "why: one ordered list owns the gates\n"
            "source: s4\n"
            "key: rpi-2026-09-01-0000-deadbeef:Q4\n"
            "at: 2026-09-08T00:00:00+00:00\n",
            encoding="utf-8",
        )
        decisions = self._decisions(state_root)
        # D-0002/D-0003/D-0004 share 9 keywords, D-0001 shares 5; the
        # 9-keyword ties break newer-first, then lower-id-first.
        assert [
            p.id
            for p in working_memory.find_precedents(
                decisions,
                "extract a spine module for the precondition chain with gates",
                "one ordered list owns the gates",
                area="pairing",
                loop_kind="rpi",
                limit=10,
            )
        ] == ["D-0003", "D-0004", "D-0002", "D-0001"]

    def test_precedent_never_blocks_and_never_invents(
        self, driver: loops.RpiDriver, state_root: Path
    ) -> None:
        """Advisory only: even a corrupt decisions file cannot break
        recording, and a past loop with no verdict gets no outcome
        clause."""
        path = state_root / working_memory.DECISIONS_FILENAME
        path.write_text("## D-0001 — 2026-09-01\nnot a real entry\n", encoding="utf-8")
        state = _at_pair_plan(driver)
        _write(driver, state, "pair-plan", PAIRING_OK)
        assert driver.check(state) == []
        # Does not raise; stays silent on garbage.
        driver.record_pair_answer(
            state, "Q1", "answer", "extract a spine module", by="Luke"
        )
        assert driver.last_precedents == []
