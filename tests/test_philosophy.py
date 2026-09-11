"""Five principles, five mechanisms: the owner's philosophy enforced in code.

Every principle below is enforced by a mechanism with tests -- never by
prose alone:

1. First principles -- research artifacts must show a problem breakdown,
   explicitly named assumptions challenged, and angles considered, before
   any solution (TestResearchFirstPrinciples).
2. Honda first -- pairing briefs mark effort per approach and exactly one
   default recommendation (what was asked, no more); plan decisions say
   whether they followed or overrode it, with a reason (TestPairPlanHonda).
3. Mission-driven and measurable -- the mission requires objective +
   success_criteria (exams with wired verify commands); `mission --set`
   refuses to call a mission complete without both (TestMeasurableMission,
   TestMissionSetCompletion).
4. Revisit the mission -- the session-start playbook renders the live
   mission; loop close prompts instead of judging stale criteria
   (TestSessionStartMissionBrief, TestLoopCloseCriteria).
5. Proof not claims -- artifacts are judged against the criteria at
   validated boundaries with a named event; the verdict records
   per-criterion results (TestCriteriaEvaluation, TestLoopCloseCriteria).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from awino import heilmeier, loops
from awino.cli import app as cli_app
from awino.cli.buddy import buddy_app
from awino.cli.loopctl import loop_app
from awino.enforce import Ledger
from awino.heilmeier import Catechism, save
from awino.playbook import STEP_SKILLS, run_event

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"

runner = CliRunner()


# ── 3. Mission-driven and measurable: the mission schema ─────────────────────


def _cat(**answers: str) -> Catechism:
    return Catechism(answers=dict(answers), source=dict.fromkeys(answers, "human"))


class TestMeasurableMission:
    def test_empty_catechism_missing_both_required_fields(self) -> None:
        assert heilmeier.missing_mission_fields(_cat()) == [
            "objective",
            "success_criteria",
        ]

    def test_prose_only_exams_do_not_count_as_criteria(self) -> None:
        # Exams without a wired verify command are prose, not measurement.
        cat = _cat(objective="ship the thing", exams="it should feel fast")
        assert heilmeier.missing_mission_fields(cat) == ["success_criteria"]

    def test_wired_exam_satisfies_success_criteria(self) -> None:
        cat = _cat(
            objective="ship the thing",
            exams="it feels fast -> pytest tests/test_speed.py -q",
        )
        assert heilmeier.missing_mission_fields(cat) == []
        assert heilmeier.validate_mission(cat) == []

    def test_validate_errors_name_the_field_and_the_fix(self) -> None:
        problems = heilmeier.validate_mission(_cat())
        assert any("objective" in p for p in problems)
        assert any("success_criteria" in p for p in problems)
        assert all("awino mission --set" in p for p in problems)

    def test_success_criteria_returns_one_statement_per_exam_line(self) -> None:
        cat = _cat(
            objective="x",
            exams="fast -> pytest -q\nreliable -> pytest tests/test_r.py -q",
        )
        assert heilmeier.success_criteria(cat) == [
            "fast -> pytest -q",
            "reliable -> pytest tests/test_r.py -q",
        ]

    def test_criteria_hash_stable_and_sensitive(self) -> None:
        cat = _cat(objective="x", exams="fast -> pytest -q")
        assert heilmeier.criteria_hash(cat) == heilmeier.criteria_hash(
            _cat(objective="x", exams="fast -> pytest -q")
        )
        assert heilmeier.criteria_hash(cat) != heilmeier.criteria_hash(
            _cat(objective="x", exams="faster -> pytest -q")
        )
        assert heilmeier.criteria_hash(cat) != heilmeier.criteria_hash(
            _cat(objective="y", exams="fast -> pytest -q")
        )


# ── 3b. `mission --set` refuses an unmeasurable mission ───────────────────────


@pytest.fixture()
def mission_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    return project


def _set(runner: CliRunner, key: str, text: str):
    return runner.invoke(cli_app, ["mission", "--set", f"{key}={text}"])


class TestMissionSetCompletion:
    ANSWERS: ClassVar[dict[str, str]] = {
        "objective": "ship the thing",
        "today": "manual deploys",
        "new_approach": "automate the release",
        "who_cares": "the team",
        "risks": "downtime",
        "cost": "two days",
        "duration": "one week",
    }

    def _answer_all_but_exams(self) -> None:
        for key, text in self.ANSWERS.items():
            result = _set(runner, key, text)
            assert result.exit_code == 0, result.output

    def test_complete_with_prose_exams_is_refused(self, mission_project: Path) -> None:
        self._answer_all_but_exams()
        result = _set(runner, "exams", "it should feel fast")
        assert result.exit_code == 2, result.output
        assert "REFUSED" in result.output
        assert "success_criteria" in result.output
        assert "COMPLETE" not in result.output

    def test_complete_with_wired_exam_is_accepted(self, mission_project: Path) -> None:
        self._answer_all_but_exams()
        result = _set(runner, "exams", "it feels fast -> pytest tests/test_speed.py -q")
        assert result.exit_code == 0, result.output
        assert "COMPLETE" in result.output

    def test_progressive_answering_still_works(self, mission_project: Path) -> None:
        # Setting a non-final field never triggers the completeness check.
        result = _set(runner, "objective", "ship the thing")
        assert result.exit_code == 0, result.output
        assert "QUESTION" in result.output  # next gap is asked, not refused


# ── buddy: criteria completeness diagnostic + draft scaffold ──────────────────


def _buddy_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".awino"
    state_root.mkdir(parents=True, exist_ok=True)
    Ledger(state_root)
    return tmp_path


class TestBuddyCriteria:
    def test_report_flags_missing_criteria(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _buddy_project(tmp_path, monkeypatch)
        save(
            tmp_path / ".awino",
            _cat(objective="ship the thing", exams="it should feel fast"),
        )
        result = runner.invoke(buddy_app, [])
        assert result.exit_code == 0, result.output
        assert "MISSION CRITERIA" in result.output
        assert "MISSING" in result.output
        assert "success_criteria" in result.output

    def test_report_ok_when_measurable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _buddy_project(tmp_path, monkeypatch)
        save(
            tmp_path / ".awino",
            _cat(
                objective="ship the thing",
                exams="fast -> pytest -q",
            ),
        )
        result = runner.invoke(buddy_app, [])
        assert result.exit_code == 0, result.output
        assert "MISSION CRITERIA" in result.output
        assert "1 success criteria on file" in result.output
        assert "MISSING" not in result.output

    def test_fix_scaffolds_draft_from_stated_goals(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = _buddy_project(tmp_path, monkeypatch)
        (project / ".awino" / "project.yaml").write_text(
            "goals:\n  - cut deploy time under five minutes\n  - zero-downtime releases\n",
            encoding="utf-8",
        )
        result = runner.invoke(buddy_app, ["--fix"])
        assert result.exit_code == 0, result.output
        assert "MISSION CRITERIA" in result.output
        assert "scaffolded draft objective" in result.output
        assert "marked for human review" in result.output
        cat = heilmeier.load(project / ".awino")
        assert cat.answers["objective"] == "cut deploy time under five minutes"
        assert "[DRAFT -- human review required]" in cat.answers["exams"]
        # A scaffolded draft has no wired commands: the mission is still
        # not measurable until the human finalizes it.
        assert heilmeier.validate_mission(cat)
        assert "BUDDY-FIX done: 2 correction(s) applied, 1 need a human" in (result.output)

    def test_fix_with_no_stated_goals_scaffolds_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _buddy_project(tmp_path, monkeypatch)
        result = runner.invoke(buddy_app, ["--fix"])
        assert result.exit_code == 0, result.output
        assert "no stated goals to scaffold from" in result.output
        assert "scaffolded draft" not in result.output


# ── 4a. Revisit the mission at session start ─────────────────────────────────


class TestSessionStartMissionBrief:
    def _state(self, tmp_path: Path) -> Path:
        s = tmp_path / ".awino"
        s.mkdir(parents=True, exist_ok=True)
        return s

    def test_mission_brief_step_runs_with_declared_skill(self, tmp_path: Path) -> None:
        s = self._state(tmp_path)
        lines = run_event("session-start", s, tmp_path, ledger=Ledger(s), open_seeds=[])
        joined = "\n".join(lines)
        assert "[mission-brief] skill=direct" in joined
        assert STEP_SKILLS["mission-brief"] == "direct"

    def test_mission_brief_shows_unmeasurable_mission(self, tmp_path: Path) -> None:
        s = self._state(tmp_path)
        lines = run_event("session-start", s, tmp_path, ledger=Ledger(s), open_seeds=[])
        joined = "\n".join(lines)
        assert "MISSION  (revisiting the live mission before work starts)" in joined
        assert "measurable: no (missing: objective, success_criteria)" in joined

    def test_mission_brief_renders_live_objective_and_criteria(self, tmp_path: Path) -> None:
        s = self._state(tmp_path)
        save(
            s,
            _cat(
                objective="ship the thing",
                exams="fast -> pytest -q\nreliable -> pytest tests/test_r.py -q",
            ),
        )
        lines = run_event("session-start", s, tmp_path, ledger=Ledger(s), open_seeds=[])
        joined = "\n".join(lines)
        assert "objective: ship the thing" in joined
        assert "success criteria (2):" in joined
        assert "measurable: yes" in joined


# ── 1. First principles: research shows its work ─────────────────────────────


@pytest.fixture()
def rpi_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "auth.py").write_text("# auth\n", encoding="utf-8")
    # The spine (step 1) refuses all advancement without a mission:
    # objective + at least one exam wired to a verification command.
    heilmeier.save(
        project / ".awino",
        heilmeier.Catechism(
            answers={
                "objective": "exercise the test loop honestly",
                "exams": "the loop advances through its phases -> true",
            }
        ),
    )
    return project


@pytest.fixture()
def rpi_driver(rpi_project: Path, tmp_path: Path) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=rpi_project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
    )


RESEARCH_BASE = """# Research: auth migration

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
2. How the session store behaves during a migration window.

## Assumptions challenged
- "Migration requires downtime": challenged -- src/auth.py:42 shows token
  validation is stateless, so a flag-gated path needs no outage.

## Angles considered
- The inverse: leave auth.py untouched and migrate callers instead.
- Conventions from the existing flag-gated rollout in the session store.

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
User confirmed by tester: solve the stated problem -- "Migrate auth with zero downtime.".

## Open questions
None.
"""


def _write_research(driver: loops.RpiDriver, state: loops.LoopState, text: str) -> None:
    path = driver.project_root / state.research_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestResearchFirstPrinciples:
    def test_missing_sections_each_named(self, rpi_driver: loops.RpiDriver) -> None:
        state = rpi_driver.new("migrate auth")
        bare = RESEARCH_BASE
        for section in (
            "## Problem breakdown",
            "## Assumptions challenged",
            "## Angles considered",
            "## Applicability check",
        ):
            bare = "\n".join(ln for ln in bare.splitlines() if not ln.startswith(section))
        # Drop the bodies too: remove the list lines under the headings.
        _write_research(rpi_driver, state, bare)
        missing = rpi_driver.validate_current(state)
        assert any("'problem breakdown'" in m for m in missing)
        assert any("'assumptions challenged'" in m for m in missing)
        assert any("'angles considered'" in m for m in missing)
        assert any("'applicability check'" in m for m in missing)

    def test_solution_before_sections_is_rejected(self, rpi_driver: loops.RpiDriver) -> None:
        state = rpi_driver.new("migrate auth")
        jumped = RESEARCH_BASE.replace(
            "## Problem breakdown",
            "## Proposed solution\nMigrate everything at once.\n\n## Problem breakdown",
        )
        _write_research(rpi_driver, state, jumped)
        missing = rpi_driver.validate_current(state)
        assert any("before any solution" in m or "proposed solution" in m for m in missing)

    def test_assumptions_section_must_name_assumptions(self, rpi_driver: loops.RpiDriver) -> None:
        state = rpi_driver.new("migrate auth")
        vague = RESEARCH_BASE.replace(
            '- "Migration requires downtime": challenged -- src/auth.py:42 shows token\n'
            "  validation is stateless, so a flag-gated path needs no outage.",
            "We reviewed our starting ideas against the code and they held up.",
        )
        _write_research(rpi_driver, state, vague)
        missing = rpi_driver.validate_current(state)
        assert any("names no assumption explicitly" in m for m in missing)

    def test_complete_first_principles_research_passes(self, rpi_driver: loops.RpiDriver) -> None:
        state = rpi_driver.new("migrate auth")
        _write_research(rpi_driver, state, RESEARCH_BASE)
        assert rpi_driver.validate_current(state) == []


# ── 2. Honda first: effort, default recommendation, follow/override ──────────


BRIEF_BASE = """# Pairing brief: auth migration

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
Default recommendation: migrates incrementally behind a flag -- exactly what
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


def _at_pair_plan(driver: loops.RpiDriver, brief: str = BRIEF_BASE) -> loops.LoopState:
    state = driver.new("migrate auth")
    _write_research(driver, state, RESEARCH_BASE)
    assert driver.check(state) == []
    _confirm_problem(driver, state)
    path = driver.project_root / state.pairing_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(brief, encoding="utf-8")
    assert driver.advance(state) == "pair-plan"
    return driver.load(state.id)


def _write_plan(driver: loops.RpiDriver, state: loops.LoopState, text: str) -> None:
    path = driver.project_root / state.plan_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


PLAN_BASE = """# Plan: auth migration

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
"""


def _thinking_run(driver, state) -> None:
    """Satisfy the plan-approval thinking gate in fixtures."""
    driver.record_thinking_run(state, "premortem", by="tester", memory_id="D-0001")


def _confirm_problem(driver, state) -> None:
    """Satisfy the lawyer-move gate in fixtures: the user confirmed the
    stated problem (the fixture research confirms it stands)."""
    driver.confirm_problem(state, by="tester")


def _comprehend(driver, state) -> None:
    """Satisfy the plan-approval comprehension gate in fixtures. The
    re-examine plan's Decisions section yields one probe per decision;
    every suggestion the plan generates still needs deciding, plus an
    explanation."""
    for suggestion in driver.plan_suggestions(state):
        driver.record_suggestion_decision(
            state,
            suggestion.id,
            "rejected",
            "noted and explicitly set aside for this re-examination",
            by="tester",
        )
    for pid, _ in driver.comprehension_probes(state):
        driver.record_probe_answer(
            state,
            pid,
            "the pairing decision stands: the approach is incremental and "
            "the downtime budget is zero",
            by="tester",
        )
    driver.record_explanation(
        state,
        "We migrate auth behind a flag: read the code, write the plan, "
        "migrate behind a flag, then revert the flag and redeploy. "
        "Key decisions: Q1 -> strangler: followed the default "
        "recommendation because incremental migration keeps downtime at "
        "zero. Q2 -> zero downtime budget answered during pair-planning: "
        "the migration ships in a normal deploy window.",
        by="tester",
    )


class TestPairPlanHonda:
    def test_brief_without_effort_rejected(self, rpi_driver: loops.RpiDriver) -> None:
        no_effort = BRIEF_BASE.replace("effort: one day\n", "").replace("effort: three days\n", "")
        state = rpi_driver.new("migrate auth")
        _write_research(rpi_driver, state, RESEARCH_BASE)
        path = rpi_driver.project_root / state.pairing_artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(no_effort, encoding="utf-8")
        assert rpi_driver.check(state) == []
        _confirm_problem(rpi_driver, state)
        rpi_driver.advance(state)
        state = rpi_driver.load(state.id)
        missing = rpi_driver.check(state)
        assert any("level-of-effort marker" in m for m in missing)

    def test_brief_without_default_recommendation_rejected(
        self, rpi_driver: loops.RpiDriver
    ) -> None:
        no_default = BRIEF_BASE.replace("Default recommendation: ", "")
        state = _at_pair_plan(rpi_driver, no_default)
        missing = rpi_driver.check(state)
        assert any("default recommendation" in m for m in missing)

    def test_brief_with_two_defaults_rejected(self, rpi_driver: loops.RpiDriver) -> None:
        two = BRIEF_BASE.replace(
            "### Big bang\nReplace everything at once.",
            "### Big bang\nDefault recommendation: replace everything at once.",
        )
        state = _at_pair_plan(rpi_driver, two)
        missing = rpi_driver.check(state)
        assert any("multiple approaches" in m for m in missing)

    def test_pairing_approaches_reports_effort_and_roles(self, rpi_driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(rpi_driver)
        approaches = rpi_driver.pairing_approaches(state)
        assert ("Big bang", "one day", "alternate") in approaches
        assert ("Strangler", "three days", "default") in approaches

    def test_describe_pairing_presents_honda_as_default(self, rpi_driver: loops.RpiDriver) -> None:
        state = _at_pair_plan(rpi_driver)
        lines = rpi_driver.describe_pairing(state)
        joined = "\n".join(lines)
        assert "DEFAULT RECOMMENDATION" in joined
        assert "[recommendation]" in joined

    def _at_plan_answered(self, driver: loops.RpiDriver) -> loops.LoopState:
        state = _at_pair_plan(driver)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        driver.record_pair_answer(state, "Q2", "default", "zero downtime assumed")
        assert driver.check(state) == []
        assert driver.advance(state) == "plan"
        return driver.load(state.id)

    def test_decision_choosing_approach_without_follow_or_override_rejected(
        self, rpi_driver: loops.RpiDriver
    ) -> None:
        state = self._at_plan_answered(rpi_driver)
        plan = PLAN_BASE + "- Chose the strangler approach (Q1) because it is safer.\n"
        _write_plan(rpi_driver, state, plan)
        missing = rpi_driver.check(state)
        assert any("followed or overrode the default recommendation" in m for m in missing)

    def test_decision_following_default_with_reason_passes(
        self, rpi_driver: loops.RpiDriver
    ) -> None:
        state = self._at_plan_answered(rpi_driver)
        plan = PLAN_BASE + (
            "- Chose the strangler approach (Q1); followed the default "
            "recommendation because it is exactly what was asked.\n"
        )
        _write_plan(rpi_driver, state, plan)
        assert rpi_driver.check(state) == []

    def test_decision_overriding_default_with_reason_passes(
        self, rpi_driver: loops.RpiDriver
    ) -> None:
        state = self._at_plan_answered(rpi_driver)
        plan = PLAN_BASE + (
            "- Chose the big bang approach (Q1); overrode the default "
            "recommendation because the flag infra does not exist yet.\n"
        )
        _write_plan(rpi_driver, state, plan)
        assert rpi_driver.check(state) == []


# ── 5. Proof not claims: criteria judged at validated boundaries ─────────────


@pytest.fixture()
def crit_driver(tmp_path: Path) -> loops.RpiDriver:
    # Its own mission-less project: the "no criteria on file" test needs
    # no mission at all, and the spine would refuse advancement without
    # one -- check() (artifact validation) is not advancement, so a bare
    # project is the honest fixture for "nothing on file".
    project = tmp_path / "crit-project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "auth.py").write_text("# auth\n", encoding="utf-8")
    ledger = Ledger(tmp_path / ".awino")
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
        ledger=ledger,
    )


def _save_measurable_mission(project: Path) -> None:
    state_root = project / ".awino"
    state_root.mkdir(parents=True, exist_ok=True)
    save(
        state_root,
        _cat(
            objective="migrate auth with zero downtime",
            exams=(
                "the auth migration completes with zero downtime -> pytest tests/test_auth.py -q"
            ),
        ),
    )


class TestCriteriaEvaluation:
    def test_evaluate_met_unmet_unjudgeable(self) -> None:
        judged = dict(
            loops.evaluate_success_criteria(
                [
                    "the auth migration completes",
                    "the billing page loads fast",
                    "???",
                ],
                "Auth migration notes: the migration completes in src/auth.py:42.",
            )
        )
        assert judged["the auth migration completes"] == "met"
        assert judged["the billing page loads fast"] == "unmet"
        assert judged["???"] == "unjudgeable"

    def test_check_judges_artifact_and_emits_named_event(
        self, crit_driver: loops.RpiDriver
    ) -> None:
        _save_measurable_mission(crit_driver.project_root)
        state = crit_driver.new("migrate auth")
        _write_research(crit_driver, state, RESEARCH_BASE)
        assert crit_driver.check(state) == []
        judged = dict(crit_driver.last_criteria or [])
        assert (
            judged[
                "the auth migration completes with zero downtime -> pytest tests/test_auth.py -q"
            ]
            == "met"
        )
        kinds = [
            e.kind
            for e in crit_driver.ledger.loop_events(state.id)  # type: ignore[union-attr]
        ]
        assert "success_criteria_evaluated" in kinds

    def test_no_criteria_on_file_means_no_event_no_noise(
        self, crit_driver: loops.RpiDriver
    ) -> None:
        state = crit_driver.new("migrate auth")
        _write_research(crit_driver, state, RESEARCH_BASE)
        assert crit_driver.check(state) == []
        assert crit_driver.last_criteria is None
        kinds = [
            e.kind
            for e in crit_driver.ledger.loop_events(state.id)  # type: ignore[union-attr]
        ]
        assert "success_criteria_evaluated" not in kinds


# ── 4b. Revisit the mission at loop close ─────────────────────────────────────


@pytest.fixture()
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "demo"
    project.mkdir(parents=True)
    (project / "src").mkdir()
    (project / "src" / "auth.py").write_text("# auth\n", encoding="utf-8")
    monkeypatch.setenv("AWINO_PROJECT", str(project))
    return project


def _run_loop(project: Path) -> str:
    result = runner.invoke(loop_app, ["run", "rpi", "--task", "migrate auth"])
    assert result.exit_code == 0, result.output
    for line in result.output.splitlines():
        if line.startswith("LOOP"):
            return line.split(None, 1)[1].strip()
    raise AssertionError("no LOOP line in output")


def _write_research_cli(driver: loops.RpiDriver, state: loops.LoopState) -> None:
    path = driver.project_root / state.research_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RESEARCH_BASE, encoding="utf-8")


def _cli_driver(project: Path) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=project / ".awino" / "loops",
        skill_md=SKILL_MD,
        ledger=Ledger(project / ".awino"),
    )


def _verdict_event(project: Path, loop_id: str):
    events = [
        e for e in Ledger(project / ".awino").loop_events(loop_id) if e.kind == "outcome_verdict"
    ]
    assert len(events) == 1
    return events[0]


class TestLoopCloseCriteria:
    def test_close_judges_live_criteria_in_verdict(self, cli_env: Path) -> None:
        project = cli_env
        loop_id = _run_loop(project)
        _save_measurable_mission(project)
        driver = _cli_driver(project)
        state = driver.load(loop_id)
        _write_research_cli(driver, state)

        result = runner.invoke(loop_app, ["close", "--id", loop_id, "--verdict", "yes"])
        assert result.exit_code == 0, result.output
        assert "CRITERIA" in result.output
        assert "[met]" in result.output
        event = _verdict_event(project, loop_id)
        assert "criteria_met:" in event.detail
        assert "criteria_unmet:" in event.detail
        assert "criteria_unjudgeable:" in event.detail

    def test_close_refuses_stale_criteria_with_prompt(self, cli_env: Path) -> None:
        project = cli_env
        loop_id = _run_loop(project)
        _save_measurable_mission(project)
        driver = _cli_driver(project)
        state = driver.load(loop_id)
        _write_research_cli(driver, state)
        # Validate the artifact: the loop now stands against criteria A.
        confirmed = runner.invoke(loop_app, ["confirm-problem", "--id", loop_id, "--confirmed"])
        assert confirmed.exit_code == 0, confirmed.output
        result = runner.invoke(loop_app, ["next", "--id", loop_id])
        assert result.exit_code == 0, result.output
        assert "CRITERIA" in result.output

        # The mission moves under the examined work.
        save(
            project / ".awino",
            _cat(
                objective="migrate auth with zero downtime",
                exams="the auth migration is reverted -> pytest -q",
            ),
        )
        result = runner.invoke(loop_app, ["close", "--id", loop_id, "--verdict", "yes"])
        assert result.exit_code == 2, result.output
        assert "PROMPT" in result.output
        assert "stale success criteria" in result.output
        assert 'awino mission --set "exams=<claim> -> <verify command>"' in (result.output)
        assert "awino loop next" in result.output
        # No verdict was recorded against the stale criteria.
        assert not [
            e
            for e in Ledger(project / ".awino").loop_events(loop_id)
            if e.kind == "outcome_verdict"
        ]

    def test_close_after_reexamine_judges_live_criteria(self, cli_env: Path) -> None:
        project = cli_env
        loop_id = _run_loop(project)
        _save_measurable_mission(project)
        driver = _cli_driver(project)
        state = driver.load(loop_id)
        _write_research_cli(driver, state)
        confirmed = runner.invoke(loop_app, ["confirm-problem", "--id", loop_id, "--confirmed"])
        assert confirmed.exit_code == 0, confirmed.output
        assert runner.invoke(loop_app, ["next", "--id", loop_id]).exit_code == 0
        # Pair-planning is mandatory: write the brief, answer the questions,
        # then move on to plan.
        state = driver.load(loop_id)
        assert state.phase == "pair-plan"
        brief_path = project / state.pairing_artifact
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(BRIEF_BASE, encoding="utf-8")
        for qid, answer in (
            ("Q1", "strangler: migrate incrementally behind a flag"),
            ("Q2", "zero downtime budget; a normal deploy window"),
        ):
            answered = runner.invoke(
                loop_app,
                [
                    "answer",
                    "--id",
                    loop_id,
                    "--question",
                    qid,
                    "--answer",
                    answer,
                    "--by",
                    "tester",
                ],
            )
            assert answered.exit_code == 0, answered.output
        assert runner.invoke(loop_app, ["next", "--id", loop_id]).exit_code == 0
        # Mission moves; close refuses (as proven above).
        save(
            project / ".awino",
            _cat(
                objective="migrate auth with zero downtime",
                exams="the auth migration is reverted -> pytest -q",
            ),
        )
        refused = runner.invoke(loop_app, ["close", "--id", loop_id, "--verdict", "yes"])
        assert refused.exit_code == 2
        # Re-examine: write a plan the new criteria can be judged against,
        # then `next` re-validates and re-syncs the recorded hash.
        state = driver.load(loop_id)
        assert state.phase == "plan"
        # Pair-planning happened, so the plan's Decisions section must trace
        # the Q1/Q2 answers (an untraced decision would fail validation).
        # A decision naming a candidate approach must say whether it
        # followed or overrode the default, with a reason.
        plan = (
            PLAN_BASE + "- Q1 -> strangler: followed the default recommendation "
            "because incremental migration behind a flag keeps downtime "
            "at zero.\n"
            "- Q2 -> zero downtime budget: answered during pair-planning; "
            "the migration ships in a normal deploy window.\n"
        )
        path = project / state.plan_artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plan, encoding="utf-8")
        _thinking_run(driver, state)
        # "Execute when comfortable and understanding": comprehension comes
        # BEFORE approval. Once comprehension exists in state, the plan
        # document must record it -- paste the check block into the plan.
        _comprehend(driver, state)
        plan_text = path.read_text(encoding="utf-8")
        path.write_text(
            plan_text.rstrip("\n") + "\n\n" + driver.comprehension_record_block(state),
            encoding="utf-8",
        )
        approved = runner.invoke(
            loop_app,
            ["approve", "--id", loop_id, "--by", "tester", "--reason", "re-examined"],
        )
        assert approved.exit_code == 0, approved.output
        assert runner.invoke(loop_app, ["next", "--id", loop_id]).exit_code == 0
        result = runner.invoke(loop_app, ["close", "--id", loop_id, "--verdict", "partial"])
        assert result.exit_code == 0, result.output
        event = _verdict_event(project, loop_id)
        assert "verdict: partial" in event.detail
        assert "the auth migration is reverted" in event.detail
