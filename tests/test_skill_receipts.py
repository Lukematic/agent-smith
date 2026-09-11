"""Tests for skill receipts: skill usage as a gated, checkable step.

A receipt (``.awino/receipts/<loop>--<phase>--<skill>.json``) attests that a
phase's artifact was produced under a skill for specific inputs. The loop
driver writes one only when the phase's artifact validates; advancing FROM
the phase requires a valid receipt for every required skill. Buddy audits
completed phases for missing receipts, and `buddy --fix` re-arms the phase
-- it never forges a receipt.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from awino import heilmeier, loops, skill_receipts, working_memory
from awino.cli import buddy
from awino.enforce import Ledger
from awino.paths import project_state_dir

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

## Open questions
- none; the cutover shape is settled.
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
    """A fake project with the files the fixtures claim are in scope."""
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "auth.py").write_text("# auth\n", encoding="utf-8")
    return project


@pytest.fixture()
def state_root(project: Path) -> Path:
    return project_state_dir(project)


@pytest.fixture()
def ledger(state_root: Path) -> Ledger:
    return Ledger(state_root)


@pytest.fixture()
def driver(
    project: Path, ledger: Ledger, state_root: Path
) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=state_root / "loops",
        skill_md=SKILL_MD,
        open_rpi_run=lambda: "run-123",
        ledger=ledger,
        state_root=state_root,
    )


@pytest.fixture()
def workspace(project: Path, state_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project=SimpleNamespace(root=project), state_root=state_root
    )


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


def _research_done(driver: loops.RpiDriver) -> loops.LoopState:
    """Research written and validated: the receipt is on file."""
    state = driver.new("migrate auth")
    _write(driver, state, "research", RESEARCH_OK)
    assert driver.check(state) == []
    return driver.load(state.id)


def _receipt_file(
    state_root: Path, loop_id: str, phase: str, skill: str = "awino-rpi"
) -> Path:
    return skill_receipts.receipt_path(state_root, loop_id, phase, skill)


class TestReceiptGate:
    def test_missing_receipt_blocks_advance_and_names_the_skill(
        self, driver: loops.RpiDriver
    ) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        # No check() ran: no receipt was written.
        with pytest.raises(loops.ReceiptBlocked) as exc_info:
            driver.advance(state)
        assert "awino-rpi" in str(exc_info.value)
        assert exc_info.value.phase == "research"
        assert any(
            "awino-rpi" in problem for problem in exc_info.value.problems
        )

    def test_receipt_with_missing_output_artifact_is_rejected(
        self, driver: loops.RpiDriver, state_root: Path
    ) -> None:
        state = _research_done(driver)
        assert _receipt_file(state_root, state.id, "research").is_file()
        (driver.project_root / state.research_artifact).unlink()
        with pytest.raises(loops.ReceiptBlocked) as exc_info:
            driver.advance(state)
        assert any(
            "missing output artifact" in problem
            and state.research_artifact in problem
            for problem in exc_info.value.problems
        )

    def test_receipt_whose_output_fails_validation_is_rejected(
        self, driver: loops.RpiDriver
    ) -> None:
        state = _research_done(driver)
        _write(driver, state, "research", "too short, no evidence\n")
        with pytest.raises(loops.ReceiptBlocked) as exc_info:
            driver.advance(state)
        assert any(
            "fails its own validation" in problem
            for problem in exc_info.value.problems
        )

    def test_unchanged_artifact_does_not_rerun_validation(
        self, driver: loops.RpiDriver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gate accepts a byte-identical artifact without re-running the
        validator: some validators (ralph retry) are not idempotent, so a
        re-run on an unchanged artifact could wrongly fail."""
        state = _research_done(driver)
        calls: list[str] = []
        original = loops.ResearchPhase.validate

        def counting(
            self_, loops_driver: loops.LoopDriver, loop_state: loops.LoopState
        ) -> list[str]:
            calls.append("research")
            return original(self_, loops_driver, loop_state)

        monkeypatch.setattr(loops.ResearchPhase, "validate", counting)
        driver.advance(state)
        assert calls == []

    def test_changed_artifact_reruns_validation(
        self, driver: loops.RpiDriver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the artifact changed after the receipt was written, the gate
        re-runs the artifact's own validator instead of trusting the hash."""
        state = _research_done(driver)
        calls: list[str] = []
        original = loops.ResearchPhase.validate

        def counting(
            self_, loops_driver: loops.LoopDriver, loop_state: loops.LoopState
        ) -> list[str]:
            calls.append("research")
            return original(self_, loops_driver, loop_state)

        monkeypatch.setattr(loops.ResearchPhase, "validate", counting)
        path = driver.project_root / state.research_artifact
        text = path.read_text(encoding="utf-8")
        path.write_text(text + "Extra sentence, still valid.\n", encoding="utf-8")
        assert driver.advance(state) in ("pair-plan", "plan")
        assert calls == ["research"]

    def test_malformed_receipt_is_invalid_not_missing(
        self, driver: loops.RpiDriver, state_root: Path
    ) -> None:
        """A receipt file that exists but does not parse is invalid: it
        names the problem instead of pretending no receipt was written."""
        state = _research_done(driver)
        _receipt_file(state_root, state.id, "research").write_text(
            "{not json", encoding="utf-8"
        )
        assert driver.skill_statuses(state, "research") == {"awino-rpi": "invalid"}
        with pytest.raises(loops.ReceiptBlocked) as exc_info:
            driver.advance(state)
        assert any(
            "malformed" in problem and "awino-rpi" in problem
            for problem in exc_info.value.problems
        )

    def test_stale_inputs_hash_is_rejected(
        self, driver: loops.RpiDriver, project: Path
    ) -> None:
        state = _research_done(driver)
        # The mission moved after the receipt was written: the attestation
        # no longer describes the phase's actual inputs.
        heilmeier.save(
            project_state_dir(project),
            heilmeier.Catechism(
                {"objective": "a different objective", "exams": "x -> true"}
            ),
        )
        with pytest.raises(loops.ReceiptBlocked) as exc_info:
            driver.advance(state)
        assert any(
            "stale skill receipt" in problem
            for problem in exc_info.value.problems
        )

    def test_receipt_names_phase_skill_and_inputs(
        self, driver: loops.RpiDriver, state_root: Path
    ) -> None:
        state = _research_done(driver)
        receipt = skill_receipts.read_receipt(
            state_root, loop_id=state.id, phase="research", skill="awino-rpi"
        )
        assert receipt is not None
        assert receipt.skill == "awino-rpi"
        assert receipt.phase == "research"
        assert receipt.output_artifact == state.research_artifact
        # version pins the skill text: content hash of SKILL.md.
        assert receipt.version == skill_receipts.skill_version(
            SKILL_MD.parent.parent, "awino-rpi"
        )
        expected = skill_receipts.inputs_hash(
            artifact_path=state.research_artifact,
            criteria_hash=driver._live_criteria_hash(),
            seed_id=state.seed_id,
        )
        assert receipt.inputs_hash == expected
        # artifact_hash pins the exact bytes that validated.
        assert receipt.artifact_hash == skill_receipts.file_sha256(
            driver.project_root / state.research_artifact
        )

    def test_check_refreshes_a_stale_receipt(
        self, driver: loops.RpiDriver, project: Path, state_root: Path
    ) -> None:
        state = _research_done(driver)
        before = _receipt_file(state_root, state.id, "research").read_text(
            encoding="utf-8"
        )
        heilmeier.save(
            project_state_dir(project),
            heilmeier.Catechism(
                {"objective": "a different objective", "exams": "x -> true"}
            ),
        )
        # Re-validation re-attests: the receipt is refreshed, not duplicated.
        assert driver.check(state) == []
        after = _receipt_file(state_root, state.id, "research").read_text(
            encoding="utf-8"
        )
        assert before != after
        assert driver.advance(state) in ("pair-plan", "plan")


class TestRequiredSkillsDeclaration:
    def test_brief_without_required_skills_is_rejected(
        self, driver: loops.RpiDriver
    ) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        brief = BRIEF_OK.split("## Required skills")[0]
        _write(driver, state, "pair-plan", brief)
        assert driver.check(state) == []
        assert driver.advance(state) == "pair-plan"
        missing = driver.check(state)
        assert any(
            "required skills" in item for item in missing
        )

    def test_brief_missing_a_phase_names_it(
        self, driver: loops.RpiDriver
    ) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        brief = BRIEF_OK.replace("- plan: awino-rpi\n", "")
        _write(driver, state, "pair-plan", brief)
        assert driver.check(state) == []
        assert driver.advance(state) == "pair-plan"
        missing = driver.check(state)
        assert any(
            "'plan'" in item and "required-skills" in item for item in missing
        )

    def test_brief_with_unknown_skill_is_rejected(
        self, driver: loops.RpiDriver
    ) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        brief = BRIEF_OK.replace("awino-rpi", "awino-teleport")
        _write(driver, state, "pair-plan", brief)
        assert driver.check(state) == []
        assert driver.advance(state) == "pair-plan"
        missing = driver.check(state)
        assert any(
            "awino-teleport" in item and "no such skill" in item
            for item in missing
        )

    def test_declared_skills_drive_the_gate(
        self, driver: loops.RpiDriver
    ) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _write(driver, state, "pair-plan", BRIEF_OK)
        assert driver.check(state) == []
        assert driver.required_skills(state, "research") == ["awino-rpi"]
        assert driver.required_skills(state, "pair-plan") == ["awino-rpi"]
        # Machine-check phases need no receipts.
        assert driver.required_skills(state, "implement") == []

    def test_project_local_skill_is_known(
        self, driver: loops.RpiDriver, project: Path
    ) -> None:
        """A skill the project added under <project>/skills/ is real: the
        brief validator must not reject it as invented."""
        local = project / "skills" / "awino-local"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text("# awino-local\n", encoding="utf-8")
        assert driver.skill_known("awino-local")
        assert not driver.skill_known("awino-teleport")


class TestFullLoop:
    def test_all_receipts_valid_advancement_succeeds(
        self,
        driver: loops.RpiDriver,
        ledger: Ledger,
        workspace: SimpleNamespace,
        state_root: Path,
    ) -> None:
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _write(driver, state, "pair-plan", BRIEF_OK)
        assert driver.check(state) == []
        assert driver.advance(state) == "pair-plan"
        state = driver.load(state.id)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        driver.record_pair_answer(state, "Q2", "default", "zero downtime assumed")
        assert driver.check(state) == []
        assert driver.advance(state) == "plan"
        state = driver.load(state.id)
        _write(driver, state, "plan", PLAN_OK)
        driver.approve_plan(state, by="Luke", reason="plan is explicit enough")
        assert driver.check(state) == []
        # The checklist exposes received|missing|invalid per phase, and the
        # compact brief carries the current phase's skill line.
        checklist = working_memory.Checklist(state_root)
        assert any(
            line.startswith("SKILLS") and "awino-rpi=received" in line
            for line in checklist.brief_lines()
        )
        # The checklist entry exposes {"<skill>": "received|missing|invalid"}
        # for the current phase; per-phase history accumulates separately.
        assert checklist.skill_status(state.id) == {"awino-rpi": "received"}
        items = {item["loop_id"]: item for item in checklist.items()}
        assert items[state.id]["skills"] == {"awino-rpi": "received"}
        assert driver.advance(state) == "implement"

        # One skill_receipt ledger event per artifact phase.
        kinds = [
            event.kind
            for event in ledger.loop_events(state.id)
            if event.kind == "skill_receipt"
        ]
        assert len(kinds) == 3

        # Buddy's audit is clean: every completed phase carries a receipt.
        assert buddy._receipt_findings(workspace, ledger) == []

        # The new phase has no skill statuses yet; the per-phase history is
        # preserved.
        assert checklist.skill_status(state.id) == {}
        history = checklist.skill_status_by_phase(state.id)
        assert history["research"] == {"awino-rpi": "received"}
        assert history["pair-plan"] == {"awino-rpi": "received"}
        assert history["plan"] == {"awino-rpi": "received"}


class TestBuddyReceiptless:
    def _loop_with_receiptless_research(
        self, driver: loops.RpiDriver, state_root: Path
    ) -> loops.LoopState:
        """A loop whose research phase completed receiptless -- as if the
        phase ran before receipts existed."""
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _write(driver, state, "pair-plan", BRIEF_OK)
        assert driver.check(state) == []
        assert _receipt_file(state_root, state.id, "research").is_file()
        assert driver.advance(state) == "pair-plan"
        # The phase completed, but its receipt is gone -- as if the phase
        # ran before receipts existed.
        _receipt_file(state_root, state.id, "research").unlink()
        return driver.load(state.id)

    def test_completed_receiptless_phase_is_flagged(
        self,
        driver: loops.RpiDriver,
        ledger: Ledger,
        workspace: SimpleNamespace,
        state_root: Path,
    ) -> None:
        state = self._loop_with_receiptless_research(driver, state_root)
        findings = buddy._receipt_findings(workspace, ledger)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.loop_id == state.id
        assert finding.phase == "research"
        assert finding.skill == "awino-rpi"
        assert finding.status == "missing"
        assert "awino-rpi" in finding.problem

    def test_fix_rearms_without_forging(
        self,
        driver: loops.RpiDriver,
        ledger: Ledger,
        workspace: SimpleNamespace,
        state_root: Path,
    ) -> None:
        state = self._loop_with_receiptless_research(driver, state_root)
        rearmed, prompts = buddy._rearm_receiptless_phases(workspace, ledger)
        assert len(rearmed) == 1
        assert prompts == []
        assert state.id in rearmed[0]
        assert "'research'" in rearmed[0]
        # --fix never writes the receipt itself.
        assert not _receipt_file(state_root, state.id, "research").exists()
        # The loop is back at the phase: the skill step runs again and the
        # driver's check() writes the fresh receipt.
        reloaded = driver.load(state.id)
        assert reloaded.phase == "research"
        assert driver.check(reloaded) == []
        assert _receipt_file(state_root, state.id, "research").is_file()
        assert driver.advance(reloaded) == "pair-plan"
        assert buddy._receipt_findings(workspace, ledger) == []

    def _done_loop_with_receiptless_research(
        self, driver: loops.RpiDriver, state_root: Path
    ) -> loops.LoopState:
        """A DONE loop whose research phase completed receiptless -- as if
        the phase ran before receipts existed."""
        state = driver.new("migrate auth")
        _write(driver, state, "research", RESEARCH_OK)
        _write(driver, state, "pair-plan", BRIEF_OK)
        assert driver.check(state) == []
        assert driver.advance(state) == "pair-plan"
        state = driver.load(state.id)
        driver.record_pair_answer(state, "Q1", "answer", "strangler")
        driver.record_pair_answer(state, "Q2", "default", "zero downtime assumed")
        assert driver.check(state) == []
        assert driver.advance(state) == "plan"
        state = driver.load(state.id)
        _write(driver, state, "plan", PLAN_OK)
        driver.approve_plan(state, by="Luke", reason="plan is explicit enough")
        assert driver.check(state) == []
        assert driver.advance(state) == "implement"
        state = driver.load(state.id)
        assert driver.check(state) == []
        assert driver.advance(state) == "done"
        # The loop completed, but research's receipt is gone -- as if the
        # phase ran before receipts existed.
        _receipt_file(state_root, state.id, "research").unlink()
        return driver.load(state.id)

    def test_done_loop_receiptless_phase_is_flagged(
        self,
        driver: loops.RpiDriver,
        ledger: Ledger,
        workspace: SimpleNamespace,
        state_root: Path,
    ) -> None:
        state = self._done_loop_with_receiptless_research(driver, state_root)
        assert state.phase == "done"
        findings = buddy._receipt_findings(workspace, ledger)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.loop_id == state.id
        assert finding.phase == "research"
        assert finding.skill == "awino-rpi"
        assert finding.status == "missing"

    def test_fix_reopens_done_loop_without_forging(
        self,
        driver: loops.RpiDriver,
        ledger: Ledger,
        workspace: SimpleNamespace,
        state_root: Path,
    ) -> None:
        state = self._done_loop_with_receiptless_research(driver, state_root)
        rearmed, prompts = buddy._rearm_receiptless_phases(workspace, ledger)
        assert len(rearmed) == 1
        assert prompts == []
        assert state.id in rearmed[0]
        assert "'research'" in rearmed[0]
        # --fix never writes the receipt itself, even for a done loop.
        assert not _receipt_file(state_root, state.id, "research").exists()
        # The done loop is re-opened at the phase via the dedicated path:
        # the skill step runs again and the driver's check() writes the
        # fresh receipt.
        reloaded = driver.load(state.id)
        assert reloaded.phase == "research"
        assert driver.check(reloaded) == []
        assert _receipt_file(state_root, state.id, "research").is_file()
        assert driver.advance(reloaded) == "pair-plan"
        assert buddy._receipt_findings(workspace, ledger) == []
