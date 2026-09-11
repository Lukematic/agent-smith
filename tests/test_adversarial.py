"""Adversarial tests: hostile inputs against every named validator.

Each test feeds one hostile input -- empty, multi-megabyte noise, null
bytes, Unicode bidi controls, path traversal, hollow headings, sycophancy,
vacuous premortems, tampered receipts, bogus loop ids -- and asserts the
validator refuses it with a precise, actionable error. A test that only
asserts "rejected" is not enough: the failure must name the hostile part.

Companion file: tests/test_chaos.py covers interruption, ledger corruption,
deleted state, and concurrency. This file covers hostile CONTENT.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from awino import heilmeier, loops, skill_receipts, think
from awino import stance_verify
from awino.enforce import Ledger, LoopEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
RPI_SKILL = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"
DELEGATE_SKILL = REPO_ROOT / "skills" / "awino-delegate" / "SKILL.md"

# A research artifact that validates cleanly: every required section
# present and non-hollow, file:line references, challenged assumptions,
# and the full lawyer move (stated problem, reframe-or-stands, evidence,
# user confirmation).
RESEARCH_OK = """# Research: adversarial probe

## Metadata
- date 2026-09-11, branch challenge/tested-fixes, commit abc123

## Problem breakdown
1. How hostile input reaches the validators (src/awino/loops.py:900).
2. What each validator must refuse (src/awino/think.py:575).

## Assumptions challenged
- "Text is text": challenged -- null bytes and bidi controls in artifact
  text are hostile, not content. We assume UTF-8 prose; the guard at
  src/awino/loops.py:1509 enforces it.
- "Paths stay inside the repo": challenged -- ownership claims and scope
  paths are attacker-influenced; confinement is checked, not assumed.

## Angles considered
- Reject vs flag: rejection is correct, because no content check can be
  trusted on text that may not render as read.
- Central vs per-validator guards: per-validator, at the read site, so a
  new reader cannot forget the check.

## Applicability check (the lawyer move)
### Stated problem
Harden the loop validators against hostile input.
### Reframed problem (or: the stated problem stands)
The stated problem stands: hostile input must fail loudly, not slip past.
### Evidence
Probes showed traversal claims passing as merely-missing paths.
### User confirmation
User confirmed: harden the validators, no new features.
"""

# A pairing brief that validates cleanly: sub-problems, two candidate
# approaches with trade-offs and effort (exactly one default), questions.
PAIRING_OK = """# Pairing brief: adversarial probe

## Sub-problems
- Reject hostile characters at every artifact read site
- Confine every path claim to the project

## Candidate approaches

### Per-validator guards
Check at each read site.
trade-off: a new reader could forget the check.
pro: the failure names the exact artifact and phase.
con: nine call sites to keep in sync.
effort: one day

### Central reader
Default recommendation: one reader every validator uses.
trade-off: single choke point, harder to bypass by accident.
pro: one place to audit.
con: every validator must be migrated to it.
effort: two days

## Questions
Q1: Which approach do you prefer, guards or central reader?
Q2: Who owns the guard list long-term?

## Required skills
- research: awino-rpi
- pair-plan: awino-rpi
- plan: awino-rpi
"""

# A plan that validates cleanly against a tmp project containing
# src/a.py and src/b.py. No pairing answers are recorded, so no decisions
# section is required.
PLAN_OK = """# Plan: adversarial probe

## Phases
- [ ] research: validate artifact shape
- [ ] plan: validate sections and scope paths

## Scope
- `src/a.py`
- `src/b.py`

## Tests
pytest tests/test_adversarial.py -q must pass.

## Rollback
Delete the new test file.

## Acceptance criteria
- hostile inputs are refused with the hostile part named
"""


def _write_mission(project: Path) -> None:
    heilmeier.save(
        project / ".awino",
        heilmeier.Catechism(
            answers={
                "objective": "harden the validators honestly",
                "exams": "the validators refuse hostile input -> true",
            }
        ),
    )


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    for rel in ("src/a.py", "src/b.py", "src/c.py"):
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\nprint('hi')\n", encoding="utf-8")
    _write_mission(project)
    return project


@pytest.fixture()
def rpi(project: Path, tmp_path: Path) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=RPI_SKILL,
        open_rpi_run=lambda: "run-123",
    )


@pytest.fixture()
def delegate(project: Path, tmp_path: Path) -> loops.DelegateDriver:
    return loops.DelegateDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=DELEGATE_SKILL,
    )


def _write_artifact(driver: loops.LoopDriver, rel: str, text: str) -> Path:
    """Write raw text to an artifact path (bypasses no validation)."""
    path = driver.project_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rpi_at(rpi: loops.RpiDriver, phase: str) -> loops.LoopState:
    """A fresh RPI loop parked at the given phase for direct validation."""
    state = rpi.new("adversarial probe")
    state.phase = phase
    return state


def _hollow_section(text: str, heading: str, next_heading: str) -> str:
    """Remove a section's body, leaving a bare heading: the hollow-input case.

    A heading with content under it is not hollow, so the removal must span
    the whole body up to the next heading, not just the first line.
    """
    start = text.index(heading)
    end = text.index(next_heading)
    return text[:start] + heading + "\n\n" + text[end:]


def _plan_ready_state(rpi: loops.RpiDriver) -> loops.LoopState:
    """A loop advanced research -> pair-plan -> plan through valid artifacts.

    Research cannot advance on an unconfirmed problem (the lawyer move), so
    the helper records the user's confirmation before advancing.
    """
    state = rpi.new("adversarial probe")
    _write_artifact(rpi, state.research_artifact, RESEARCH_OK)
    assert rpi.check(state) == []
    rpi.confirm_problem(state, by="t")
    assert rpi.advance(state) == "pair-plan"
    state = rpi.load(state.id)
    _write_artifact(rpi, state.pairing_artifact, PAIRING_OK)
    assert rpi.check(state) == []
    for qid, _ in rpi.pairing_questions(state):
        rpi.record_pair_answer(state, qid, "answer", "use the default", by="t")
    assert rpi.advance(state) == "plan"
    return rpi.load(state.id)


class TestResearchAdversarial:
    """Hostile inputs against ResearchPhase.validate."""

    def test_empty_artifact_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "research")
        _write_artifact(rpi, state.research_artifact, "")
        missing = rpi.check(state)
        assert any("too short" in item for item in missing)

    def test_ten_megabyte_noise_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "research")
        _write_artifact(rpi, state.research_artifact, "x" * (10 * 1024 * 1024))
        missing = rpi.check(state)
        # 10MB of noise is not research: no file:line references, no
        # sections. It must be refused, not choke the validator.
        assert missing != []

    def test_file_line_marker_scan_stays_linear_on_large_text(self) -> None:
        """Regression: the file:line marker scan must stay linear on large
        colon-free text. The old ``\\S+:\\d+`` pattern went quadratic --
        minutes at 10 MB -- before failing to match; the marker check is
        existence-only, so the pattern must not carry a greedy run."""
        text = "no colon here " * (10_000_000 // len("no colon here "))
        assert len(text) >= 9_000_000
        start = time.perf_counter()
        try:
            found = loops.FILE_LINE_RE.search(text)
        finally:
            elapsed = time.perf_counter() - start
        assert found is None
        assert elapsed < 5, f"marker scan took {elapsed:.1f}s on 10 MB"

    def test_file_line_marker_still_detected(self) -> None:
        """The existence-only marker still fires on real references, with
        and without backticks."""
        assert loops.FILE_LINE_RE.search("see src/awino/loops.py:42")
        assert loops.FILE_LINE_RE.search("`src/a.py:10`")

    def test_null_bytes_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "research")
        _write_artifact(
            rpi, state.research_artifact, RESEARCH_OK + "\x00\x00binary tail"
        )
        missing = rpi.check(state)
        assert any("null bytes" in item for item in missing)

    def test_bidi_override_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "research")
        # U+202E RIGHT-TO-LEFT OVERRIDE can visually reorder text so the
        # approved rendering differs from the validated bytes.
        _write_artifact(
            rpi, state.research_artifact, RESEARCH_OK + "\u202e hidden"
        )
        missing = rpi.check(state)
        assert any("bidi" in item for item in missing)

    def test_bidi_isolate_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "research")
        _write_artifact(
            rpi, state.research_artifact, RESEARCH_OK + "\u2066 isolated\u2069"
        )
        missing = rpi.check(state)
        assert any("bidi" in item for item in missing)

    def test_legitimate_international_text_passes(
        self, rpi: loops.RpiDriver
    ) -> None:
        """CJK, Arabic, Hebrew, and emoji are not hostile: only the explicit
        bidi formatting controls are refused, never the scripts themselves."""
        state = _rpi_at(rpi, "research")
        text = RESEARCH_OK.replace(
            "Harden the loop validators against hostile input.",
            "Harden the loop validators against hostile input. "
            "Notes: 你好 مرحبا שלום 🎉 -- international reviewers welcome.",
        )
        _write_artifact(rpi, state.research_artifact, text)
        assert rpi.check(state) == []

    def test_traversal_artifact_path_rejected(
        self, rpi: loops.RpiDriver
    ) -> None:
        state = _rpi_at(rpi, "research")
        state.research_artifact = "../../etc/evil.md"
        missing = rpi.check(state)
        assert any("escapes the project" in item for item in missing)

    def test_absolute_artifact_path_rejected(
        self, rpi: loops.RpiDriver
    ) -> None:
        state = _rpi_at(rpi, "research")
        state.research_artifact = "/etc/evil.md"
        missing = rpi.check(state)
        assert any("escapes the project" in item for item in missing)

    def test_hollow_sections_named(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "research")
        text = _hollow_section(
            RESEARCH_OK, "## Assumptions challenged", "## Angles considered"
        )
        _write_artifact(rpi, state.research_artifact, text)
        missing = rpi.check(state)
        assert any(
            "assumptions challenged" in item and "empty" in item
            for item in missing
        )

    def test_hollow_section_not_double_reported(
        self, rpi: loops.RpiDriver
    ) -> None:
        """A hollow section fails once, as empty -- the content checks that
        require section content stay silent for it."""
        state = _rpi_at(rpi, "research")
        text = _hollow_section(
            RESEARCH_OK, "## Assumptions challenged", "## Angles considered"
        )
        _write_artifact(rpi, state.research_artifact, text)
        missing = rpi.check(state)
        assumption_failures = [
            item for item in missing if "assumptions challenged" in item
        ]
        assert len(assumption_failures) == 1
        assert "empty" in assumption_failures[0]


class TestPairPlanAdversarial:
    """Hostile inputs against PairPlanPhase.validate."""

    def test_empty_artifact_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "pair-plan")
        _write_artifact(rpi, state.pairing_artifact, "")
        assert rpi.check(state) != []

    def test_null_bytes_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "pair-plan")
        _write_artifact(rpi, state.pairing_artifact, PAIRING_OK + "\x00")
        missing = rpi.check(state)
        assert any("null bytes" in item for item in missing)

    def test_bidi_override_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "pair-plan")
        _write_artifact(rpi, state.pairing_artifact, PAIRING_OK + "\u202d")
        missing = rpi.check(state)
        assert any("bidi" in item for item in missing)

    def test_hollow_sub_problems_named(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "pair-plan")
        text = _hollow_section(
            PAIRING_OK, "## Sub-problems", "## Candidate approaches"
        )
        _write_artifact(rpi, state.pairing_artifact, text)
        missing = rpi.check(state)
        assert any(
            "sub-problems" in item and "empty" in item for item in missing
        )

    def test_traversal_artifact_path_rejected(
        self, rpi: loops.RpiDriver
    ) -> None:
        state = _rpi_at(rpi, "pair-plan")
        state.pairing_artifact = "thoughts/../../evil.md"
        missing = rpi.check(state)
        assert any("escapes the project" in item for item in missing)

    def test_sycophancy_inside_quote_still_rejected(
        self, rpi: loops.RpiDriver
    ) -> None:
        """A banned validation phrase does not become acceptable inside an
        attributed quote: the critic scans substrings, not intent."""
        failures = stance_verify.verify(
            "advisor",
            'As the user said, "great question, let\'s dig in". '
            "I disagree with the timeline.",
        )
        assert failures == ["no validation phrases"]

    def test_ten_megabyte_noise_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _rpi_at(rpi, "pair-plan")
        _write_artifact(rpi, state.pairing_artifact, "y" * (10 * 1024 * 1024))
        assert rpi.check(state) != []


class TestPlanAdversarial:
    """Hostile inputs against PlanPhase.validate."""

    def test_scope_traversal_rejected_with_escape_named(
        self, rpi: loops.RpiDriver
    ) -> None:
        """A scope path climbing out of the repo is an escape attempt, not
        a missing file: the error must say so instead of laundering the
        traversal through normalization."""
        state = _plan_ready_state(rpi)
        text = PLAN_OK.replace("`src/a.py`", "`../../etc/passwd`")
        _write_artifact(rpi, state.plan_artifact, text)
        missing = rpi.check(state)
        assert any(
            "escapes the project" in item and "../../etc/passwd" in item
            for item in missing
        )

    def test_scope_absolute_path_rejected(
        self, rpi: loops.RpiDriver
    ) -> None:
        state = _plan_ready_state(rpi)
        text = PLAN_OK.replace("`src/a.py`", "`/etc/passwd`")
        _write_artifact(rpi, state.plan_artifact, text)
        missing = rpi.check(state)
        assert any("escapes the project" in item for item in missing)

    def test_scope_internal_dotdot_allowed(
        self, rpi: loops.RpiDriver, project: Path
    ) -> None:
        """'src/../src/a.py' normalizes inside the repo: not an escape."""
        state = _plan_ready_state(rpi)
        text = PLAN_OK.replace("`src/a.py`", "`src/../src/a.py`")
        _write_artifact(rpi, state.plan_artifact, text)
        missing = rpi.check(state)
        assert not any("escapes the project" in item for item in missing)

    def test_scope_missing_file_still_named(
        self, rpi: loops.RpiDriver
    ) -> None:
        state = _plan_ready_state(rpi)
        text = PLAN_OK.replace("`src/a.py`", "`src/does-not-exist.py`")
        _write_artifact(rpi, state.plan_artifact, text)
        missing = rpi.check(state)
        assert any(
            "does not exist in repo" in item and "src/does-not-exist.py" in item
            for item in missing
        )

    def test_hollow_tests_section_named(self, rpi: loops.RpiDriver) -> None:
        state = _plan_ready_state(rpi)
        text = PLAN_OK.replace(
            "## Tests\npytest tests/test_adversarial.py -q must pass.",
            "## Tests",
        )
        _write_artifact(rpi, state.plan_artifact, text)
        missing = rpi.check(state)
        assert any(
            "'tests'" in item and "empty" in item for item in missing
        )

    def test_unknown_question_reference_rejected(
        self, rpi: loops.RpiDriver
    ) -> None:
        """A decision citing Q9 when only Q1/Q2 were asked is untraced."""
        state = _plan_ready_state(rpi)
        text = PLAN_OK + "\n## Decisions\n- Q9 -> ship it because speed.\n"
        _write_artifact(rpi, state.plan_artifact, text)
        missing = rpi.check(state)
        assert any(
            "unknown question" in item and "Q9" in item for item in missing
        )

    def test_null_bytes_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _plan_ready_state(rpi)
        _write_artifact(rpi, state.plan_artifact, PLAN_OK + "\x00")
        missing = rpi.check(state)
        assert any("null bytes" in item for item in missing)

    def test_bidi_override_rejected(self, rpi: loops.RpiDriver) -> None:
        state = _plan_ready_state(rpi)
        _write_artifact(rpi, state.plan_artifact, PLAN_OK + "\u202e")
        missing = rpi.check(state)
        assert any("bidi" in item for item in missing)

    def test_traversal_artifact_path_rejected(
        self, rpi: loops.RpiDriver
    ) -> None:
        state = _plan_ready_state(rpi)
        state.plan_artifact = "../evil.md"
        missing = rpi.check(state)
        assert any("escapes the project" in item for item in missing)


class TestDelegateAdversarial:
    """Hostile inputs against the delegate phases."""

    def _decompose(
        self, delegate: loops.DelegateDriver, body: str
    ) -> tuple[loops.LoopState, list[str]]:
        state = delegate.new("split the work")
        _write_artifact(
            delegate,
            state.decompose_artifact,
            "# Decompose\n\n## Assignments\n\n" + body,
        )
        return state, delegate.check(state)

    def test_decompose_traversal_claim_rejected(
        self, delegate: loops.DelegateDriver
    ) -> None:
        """'../../etc/passwd' is an escape attempt: the decompose phase must
        name it as such, not pass it through to assign as a normalized
        'etc/passwd' that merely fails to exist."""
        _, missing = self._decompose(
            delegate, "### worker-a\nfiles:\n../../etc/passwd\nsrc/a.py\n"
        )
        assert any(
            "escapes the project" in item and "../../etc/passwd" in item
            for item in missing
        )

    def test_decompose_absolute_claim_rejected(
        self, delegate: loops.DelegateDriver
    ) -> None:
        _, missing = self._decompose(
            delegate, "### worker-a\nfiles:\n/etc/passwd\nsrc/a.py\n"
        )
        assert any(
            "escapes the project" in item and "/etc/passwd" in item
            for item in missing
        )

    def test_decompose_internal_dotdot_claim_allowed(
        self, delegate: loops.DelegateDriver
    ) -> None:
        """'src/../src/a.py' normalizes inside the repo: not an escape."""
        _, missing = self._decompose(
            delegate, "### worker-a\nfiles:\nsrc/../src/a.py\n"
        )
        assert not any("escapes the project" in item for item in missing)

    def test_assign_traversal_claim_rejected(
        self, delegate: loops.DelegateDriver
    ) -> None:
        """The assign phase re-checks raw claims: an escape that somehow
        passed decompose still fails here, named as an escape."""
        state = delegate.new("split the work")
        _write_artifact(
            delegate,
            state.decompose_artifact,
            "# Decompose\n\n## Assignments\n\n"
            "### worker-a\nfiles:\n../../etc/passwd\n",
        )
        state.phase = "assign"
        missing = delegate.check(state)
        assert any("escapes the project" in item for item in missing)

    def test_assign_symlink_escape_rejected(
        self, delegate: loops.DelegateDriver, project: Path, tmp_path: Path
    ) -> None:
        """A claimed path that is a symlink pointing outside the repo is an
        escape even though the claim text looks repo-relative."""
        outside = tmp_path / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        link = project / "link-out.py"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(outside)
        state = delegate.new("split the work")
        _write_artifact(
            delegate,
            state.decompose_artifact,
            "# Decompose\n\n## Assignments\n\n"
            "### worker-a\nfiles:\nlink-out.py\n",
        )
        state.phase = "assign"
        missing = delegate.check(state)
        assert any(
            "resolves outside the project" in item and "link-out.py" in item
            for item in missing
        )

    def test_execute_traversal_artifact_path_rejected(
        self, delegate: loops.DelegateDriver
    ) -> None:
        state = delegate.new("split the work")
        state.phase = "execute"
        state.execute_artifact = "../../evil.md"
        missing = delegate.check(state)
        assert any("escapes the project" in item for item in missing)

    def test_execute_empty_results_named(
        self, delegate: loops.DelegateDriver
    ) -> None:
        """A '## Results' heading with no worker blocks is hollow."""
        state = delegate.new("split the work")
        state.phase = "execute"
        _write_artifact(
            delegate, state.execute_artifact, "# Execute\n\n## Results\n"
        )
        missing = delegate.check(state)
        assert any(
            "Results" in item and "empty" in item for item in missing
        )

    def test_verify_output_symlink_escape_rejected(
        self, delegate: loops.DelegateDriver, project: Path, tmp_path: Path
    ) -> None:
        """A worker's 'output:' file that is a symlink out of the repo must
        not verify as done: the re-verification would otherwise read an
        outside file and bless a false claim."""
        outside = tmp_path / "outside.txt"
        outside.write_text("not the deliverable\n", encoding="utf-8")
        link = project / "deliverable.py"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(outside)
        state = delegate.new("split the work")
        state.phase = "controller-verify"
        _write_artifact(
            delegate,
            state.execute_artifact,
            "# Execute\n\n## Results\n\n### worker-a\n"
            "done: wrote the deliverable\noutput: deliverable.py\n",
        )
        missing = delegate.check(state)
        assert any(
            "escapes the project" in item and "deliverable.py" in item
            for item in missing
        )

    def test_execute_null_bytes_rejected(
        self, delegate: loops.DelegateDriver
    ) -> None:
        state = delegate.new("split the work")
        state.phase = "execute"
        _write_artifact(delegate, state.execute_artifact, "# Execute\x00\n")
        missing = delegate.check(state)
        assert any("null bytes" in item for item in missing)


class TestRalphAdversarial:
    """Hostile inputs against the Ralph phases."""

    @pytest.fixture()
    def ralph(self, project: Path, tmp_path: Path) -> loops.RalphDriver:
        return loops.RalphDriver(
            project_root=project,
            loops_dir=tmp_path / "loops",
            skill_md=RPI_SKILL,
        )

    def test_attempt_traversal_artifact_path_rejected(
        self, ralph: loops.RalphDriver
    ) -> None:
        state = ralph.new("hammer it", check="true")
        state.phase = "attempt"
        state.ralph_artifact = "../../evil.md"
        assert any(
            "escapes the project" in item for item in ralph.check(state)
        )

    def test_attempt_null_bytes_rejected(
        self, ralph: loops.RalphDriver
    ) -> None:
        state = ralph.new("hammer it", check="true")
        state.phase = "attempt"
        _write_artifact(ralph, state.ralph_artifact, "x" * 300 + "\x00")
        assert any("null bytes" in item for item in ralph.check(state))

    def test_attempt_bidi_rejected(self, ralph: loops.RalphDriver) -> None:
        state = ralph.new("hammer it", check="true")
        state.phase = "attempt"
        _write_artifact(ralph, state.ralph_artifact, "x" * 300 + "\u202e")
        assert any("bidi" in item for item in ralph.check(state))

    def test_retry_traversal_artifact_path_rejected(
        self, ralph: loops.RalphDriver
    ) -> None:
        state = ralph.new("hammer it", check="true")
        state.phase = "retry"
        state.ralph_artifact = "/etc/evil.md"
        assert any(
            "escapes the project" in item for item in ralph.check(state)
        )


class TestReceiptAdversarial:
    """Tampered skill receipts against LoopDriver.validate_receipt."""

    def _research_with_receipt(
        self, rpi: loops.RpiDriver
    ) -> loops.LoopState:
        state = _rpi_at(rpi, "research")
        _write_artifact(rpi, state.research_artifact, RESEARCH_OK)
        assert rpi.check(state) == []
        return state

    def _receipt_file(
        self, rpi: loops.RpiDriver, state: loops.LoopState
    ) -> Path:
        path = skill_receipts.receipt_path(
            rpi._receipts_root(),
            loop_id=state.id,
            phase="research",
            skill="awino-rpi",
        )
        assert path.is_file(), "check() must have written the receipt"
        return path

    def _tamper(
        self, rpi: loops.RpiDriver, state: loops.LoopState, **fields: object
    ) -> None:
        path = self._receipt_file(rpi, state)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(fields)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_tampered_inner_skill_named(
        self, rpi: loops.RpiDriver
    ) -> None:
        """The receipt file is named for awino-rpi but declares awino-evil
        inside: the inner fields are checked, not just the filename."""
        state = self._research_with_receipt(rpi)
        self._tamper(rpi, state, skill="awino-evil")
        problem = rpi.validate_receipt(state, "research", "awino-rpi")
        assert problem is not None
        assert "tampered" in problem and "awino-evil" in problem

    def test_tampered_inner_phase_named(
        self, rpi: loops.RpiDriver
    ) -> None:
        """A receipt copied from another phase (inner phase 'plan', read as
        'research') is tampering, not a valid receipt."""
        state = self._research_with_receipt(rpi)
        self._tamper(rpi, state, phase="plan")
        problem = rpi.validate_receipt(state, "research", "awino-rpi")
        assert problem is not None
        assert "tampered" in problem

    def test_output_artifact_mismatch_named(
        self, rpi: loops.RpiDriver
    ) -> None:
        """A receipt pointing at a different artifact than the phase's own
        is refused, even when the other artifact exists."""
        state = self._research_with_receipt(rpi)
        other = _write_artifact(rpi, "thoughts/other.md", "# other\n")
        self._tamper(rpi, state, output_artifact="thoughts/other.md")
        problem = rpi.validate_receipt(state, "research", "awino-rpi")
        assert problem is not None
        assert "thoughts/other.md" in problem
        assert "not the phase's output artifact" in problem

    def test_output_artifact_escape_named(
        self, rpi: loops.RpiDriver
    ) -> None:
        """A receipt whose declared output escapes the project fails on
        confinement. The receipt is rebound to the tampered inputs first:
        without that, the tampered artifact path alone makes the receipt
        stale (also a refusal, but it would hide the escape). The escape
        must be named, not hidden behind staleness."""
        state = self._research_with_receipt(rpi)
        state.research_artifact = "../../evil.md"
        self._tamper(
            rpi,
            state,
            output_artifact="../../evil.md",
            inputs_hash=skill_receipts.inputs_hash(
                artifact_path="../../evil.md",
                criteria_hash=state.criteria_hash or "",
                seed_id=state.seed_id,
            ),
        )
        problem = rpi.validate_receipt(state, "research", "awino-rpi")
        assert problem is not None
        assert "escapes the project" in problem

    def test_stale_inputs_hash_named(self, rpi: loops.RpiDriver) -> None:
        """Changing the mission criteria after the receipt was written
        makes the inputs_hash stale: the receipt no longer attests the
        current inputs."""
        state = self._research_with_receipt(rpi)
        heilmeier.save(
            rpi.project_root / ".awino",
            heilmeier.Catechism(
                answers={
                    "objective": "a completely different objective",
                    "exams": "the validators refuse hostile input -> true",
                }
            ),
        )
        problem = rpi.validate_receipt(state, "research", "awino-rpi")
        assert problem is not None
        assert "stale" in problem

    def test_malformed_receipt_named(self, rpi: loops.RpiDriver) -> None:
        """A receipt file that does not parse is reported as malformed,
        not treated as absent."""
        state = self._research_with_receipt(rpi)
        path = self._receipt_file(rpi, state)
        path.write_text("{not json", encoding="utf-8")
        problem = rpi.validate_receipt(state, "research", "awino-rpi")
        assert problem is not None
        assert "malformed" in problem

    def test_modified_artifact_after_receipt_fails(
        self, rpi: loops.RpiDriver
    ) -> None:
        """Editing the artifact after the receipt was written changes its
        bytes: the artifact_hash no longer matches, so the artifact is
        re-validated -- and the hostile edit is refused."""
        state = self._research_with_receipt(rpi)
        _write_artifact(
            rpi, state.research_artifact, RESEARCH_OK + "\x00 tampered"
        )
        problem = rpi.validate_receipt(state, "research", "awino-rpi")
        assert problem is not None
        assert "null bytes" in problem


PREMORTEM_VACUOUS_CASES = [
    "1. Nothing could go wrong. Warning signs: none.",
    "1. Nothing could go wrong. Warning signs: none whatsoever.",
    "1. Nothing could go wrong. Warning signs: n/a.",
    "1. Nothing could go wrong. Warning signs: TBD.",
    "1. Nothing could go wrong. Warning signs: unknown.",
    "1. The deploy fails silently. There are no warning signs.",
]


def _premortem_text(first_reason: str) -> str:
    return (
        "# Premortem\n\n## Failure reasons\n\n"
        f"{first_reason}\n\n"
        "2. The deploy fails. Warning signs: error rate spikes past 5%.\n\n"
        "3. Data loss on failover. Watch for: replication lag climbing.\n"
    )


class TestPremortemAdversarial:
    """Vacuous warning signs against think.validate('premortem', ...)."""

    @pytest.mark.parametrize("reason", PREMORTEM_VACUOUS_CASES)
    def test_vacuous_warning_signs_rejected(self, reason: str) -> None:
        """'None' is not a warning sign, however it is punctuated: the
        marker's presence with a negation as its whole substance fails."""
        failures = think.validate("premortem", _premortem_text(reason))
        assert any(
            "no real warning signs" in item for item in failures
        ), f"passed but should fail: {reason}"

    def test_real_warning_signs_pass(self) -> None:
        failures = think.validate(
            "premortem",
            _premortem_text(
                "1. Nothing could go wrong. Warning signs: none yet, but "
                "watch the deploy dashboard for error spikes."
            ),
        )
        assert failures == []

    def test_null_bytes_rejected(self) -> None:
        failures = think.validate(
            "premortem", _premortem_text("1. Disk fills. Warning signs: alerts.\x00")
        )
        assert any("null bytes" in item for item in failures)

    def test_bidi_override_rejected(self) -> None:
        failures = think.validate(
            "premortem",
            _premortem_text("1. Disk fills. Warning signs: alerts.\u202e"),
        )
        assert any("bidi" in item for item in failures)

    def test_legitimate_international_text_passes(self) -> None:
        text = _premortem_text(
            "1. Disk fills on the Tokyo node. Warning signs: 你好 alerts "
            "firing, مرحبا dashboard red."
        )
        assert think.validate("premortem", text) == []


class TestStanceCriticAdversarial:
    """Hostile inputs against the stance critic (stance_verify)."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "Great question, let's dig in",
            "GREAT QUESTION",
            "you're absolutely right about the design",
            "Youre absolutely right",
            "excellent point, as always",
            "couldn't agree more with this plan",
            "so glad you asked about that",
        ],
    )
    def test_banned_phrases_rejected_case_insensitive(
        self, phrase: str
    ) -> None:
        # A labeled disagreement keeps the advisor's other rule quiet, so
        # this test isolates the banned-phrase rule.
        failures = stance_verify.verify(
            "advisor", f"Some intro. {phrase}. I disagree with the timeline."
        )
        assert failures == ["no validation phrases"]

    def test_banned_phrase_inside_quote_rejected(self) -> None:
        """Attribution does not launder sycophancy: the critic scans
        substrings, and a banned phrase inside quotes is still the phrase."""
        failures = stance_verify.verify(
            "advisor",
            'Quoting the user: "great question" -- now the analysis. '
            "I disagree with the timeline.",
        )
        assert failures == ["no validation phrases"]

    def test_genuine_disagreement_passes(self) -> None:
        """The critic must not nuke honest pushback: disagreement markers
        without validation phrases pass."""
        failures = stance_verify.verify(
            "advisor",
            "I disagree with the proposed timeline. However, the core "
            "approach is sound; instead, ship the smaller scope first. "
            "The risk is integration drift.",
        )
        assert failures == []

    @pytest.mark.parametrize("mode", ["premortem", "devil", "blindspot"])
    def test_thinking_modes_carry_no_validation_phrases(
        self, mode: str
    ) -> None:
        """Mode outputs verified through the critic get the shared
        no-validation-phrases check composed with their structure rules:
        the banned phrase is caught whatever else the mode requires."""
        failures = stance_verify.verify(
            mode, "Great question! " + _premortem_text("1. x. Warning signs: y.")
            if mode == "premortem"
            else "Great question, moving on.",
        )
        assert "no validation phrases" in failures


class TestSpineAdversarial:
    """Hostile inputs against the spine checks (thinking, verdict)."""

    @pytest.fixture()
    def ledger_driver(
        self, project: Path, tmp_path: Path
    ) -> tuple[loops.RpiDriver, Ledger]:
        ledger = Ledger(tmp_path / "state")
        driver = loops.RpiDriver(
            project_root=project,
            loops_dir=tmp_path / "loops",
            skill_md=RPI_SKILL,
            ledger=ledger,
            open_rpi_run=lambda: "run-123",
        )
        return driver, ledger

    def test_bogus_loop_id_verdict_satisfies_nothing(
        self, ledger_driver: tuple[loops.RpiDriver, Ledger]
    ) -> None:
        """An outcome_verdict planted for a loop id that never existed does
        not satisfy the real loop's verdict check: the check is scoped to
        the state's own id."""
        driver, ledger = ledger_driver
        state = driver.new("real work")
        ledger.record_loop_event(
            _trail_event("rpi-00000000-0000", "outcome_verdict", "verdict=yes"),
        )
        check = next(
            step for step in loops.RpiDriver.SPINE if step.name == "verdict"
        )
        assert check.check(driver, state) == "outcome verdict (yes/partial/no)"

    def test_verdict_for_this_loop_satisfies(
        self, ledger_driver: tuple[loops.RpiDriver, Ledger]
    ) -> None:
        driver, ledger = ledger_driver
        state = driver.new("real work")
        ledger.record_loop_event(
            _trail_event(state.id, "outcome_verdict", "verdict=yes"),
        )
        check = next(
            step for step in loops.RpiDriver.SPINE if step.name == "verdict"
        )
        assert check.check(driver, state) is None

    def test_thinking_gate_reads_state_not_ledger_by_design(
        self, ledger_driver: tuple[loops.RpiDriver, Ledger]
    ) -> None:
        """Documented trade-off, pinned: thinking_satisfied reads the loop
        state. The ledger cannot independently witness the run -- both live
        in the same state directory and are written by the same method --
        so a cross-check would false-refuse pruned trails without stopping
        a real forger. The guarantee is against forgetting, not filesystem
        forgery, and record_thinking_run writes both places at once."""
        driver, ledger = ledger_driver
        state = driver.new("real work")
        assert driver.thinking_satisfied(state) is False
        driver.record_thinking_run(
            state, "premortem", by="t", memory_id="D-0001"
        )
        reloaded = driver.load(state.id)
        assert driver.thinking_satisfied(reloaded) is True
        kinds = [
            event.kind
            for event in ledger.loop_events(state.id)
            if event.kind == "thinking_run"
        ]
        assert kinds == ["thinking_run"]

    def test_thinking_waiver_without_reason_refused(
        self, rpi: loops.RpiDriver
    ) -> None:
        state = rpi.new("real work")
        with pytest.raises(loops.LoopError, match="needs a reason"):
            rpi.waive_thinking(state, by="t", reason="   ")


def _trail_event(loop_id: str, kind: str, detail: str = "") -> LoopEvent:
    """A ledger trail event: record_loop_event takes the event object, not
    loose arguments."""
    return LoopEvent(
        loop_id=loop_id,
        loop_kind="rpi",
        phase="research",
        kind=kind,
        at="2026-09-11T00:00:00+00:00",
        detail=detail,
    )


class TestLedgerParsingAdversarial:
    """Corrupt trail lines: skipped for reading, reported for repair."""

    def test_corrupt_line_skipped_but_reported(
        self, tmp_path: Path
    ) -> None:
        ledger = Ledger(tmp_path / "state")
        ledger.record_loop_event(_trail_event("rpi-1", "loop_started", "ok"))
        trail = tmp_path / "state" / "loops.jsonl"
        with trail.open("a", encoding="utf-8") as handle:
            handle.write("{corrupt json line\n")
            handle.write('{"kind": "not-an-event", "nope": true}\n')
        ledger.record_loop_event(_trail_event("rpi-1", "phase_started", "ok2"))
        events = ledger.loop_events()
        kinds = [event.kind for event in events]
        # The corrupt lines never brick the trail: reading skips them.
        assert kinds == ["loop_started", "phase_started"]
        # ...but they are reported precisely, never silently dropped.
        corrupt = ledger.loop_trail_corruption()
        assert [lineno for lineno, _ in corrupt] == [2, 3]
        assert all("loops.jsonl" in str(trail) for _, _ in corrupt)

    def test_clean_trail_reports_nothing(self, tmp_path: Path) -> None:
        ledger = Ledger(tmp_path / "state")
        ledger.record_loop_event(_trail_event("rpi-1", "loop_started", "ok"))
        assert ledger.loop_trail_corruption() == []


class TestStateLoadAdversarial:
    """Corrupt, partial, and traversal loop ids against LoopDriver.load."""

    def _driver(self, tmp_path: Path, project: Path) -> loops.RpiDriver:
        driver = loops.RpiDriver(
            project_root=project,
            loops_dir=tmp_path / "loops",
            skill_md=RPI_SKILL,
            open_rpi_run=lambda: "run-123",
        )
        driver.loops_dir.mkdir(parents=True, exist_ok=True)
        return driver

    def test_truncated_json_names_file_and_recovery(
        self, tmp_path: Path, project: Path
    ) -> None:
        driver = self._driver(tmp_path, project)
        path = driver.loops_dir / "rpi-1-abc.json"
        path.write_text('{"id": "rpi-1-abc", "phas', encoding="utf-8")
        with pytest.raises(loops.LoopError) as excinfo:
            driver.load("rpi-1-abc")
        message = str(excinfo.value)
        assert "corrupt" in message and str(path) in message
        assert "killed mid-write" in message

    def test_partial_state_names_file_and_recovery(
        self, tmp_path: Path, project: Path
    ) -> None:
        driver = self._driver(tmp_path, project)
        path = driver.loops_dir / "rpi-1-abc.json"
        path.write_text(
            json.dumps({"id": "rpi-1-abc", "phase": "research"}),
            encoding="utf-8",
        )
        with pytest.raises(loops.LoopError) as excinfo:
            driver.load("rpi-1-abc")
        message = str(excinfo.value)
        assert "incomplete" in message and str(path) in message

    def test_traversal_loop_id_refused(
        self, tmp_path: Path, project: Path
    ) -> None:
        driver = self._driver(tmp_path, project)
        with pytest.raises(loops.LoopError, match="bad loop id"):
            driver.load("../../etc/passwd")

    def test_absolute_loop_id_refused(
        self, tmp_path: Path, project: Path
    ) -> None:
        driver = self._driver(tmp_path, project)
        with pytest.raises(loops.LoopError, match="bad loop id"):
            driver.load("/etc/passwd")

    def test_missing_loop_still_named(
        self, tmp_path: Path, project: Path
    ) -> None:
        driver = self._driver(tmp_path, project)
        with pytest.raises(loops.LoopError, match="no loop"):
            driver.load("rpi-9-missing")


class TestMissionAdversarial:
    """Mission alignment is advisory by design: drift is flagged, never
    blocking. This pins the documented trade-off."""

    def test_drift_flags_but_never_blocks(
        self, rpi: loops.RpiDriver, project: Path
    ) -> None:
        # Mission goals live in .awino/MISSION.md (goal headings); the
        # catechism JSON alone carries no goal texts for the drift check.
        (project / ".awino").mkdir(parents=True, exist_ok=True)
        (project / ".awino" / "MISSION.md").write_text(
            "# Mission\n\n## Bake the perfect sourdough loaf\n", encoding="utf-8"
        )
        state = _rpi_at(rpi, "research")
        _write_artifact(rpi, state.research_artifact, RESEARCH_OK)
        missing = rpi.check(state)
        assert missing == []
        assert rpi.last_drift, "drift must be flagged"
        assert any(
            "sourdough" in goal for goal in rpi.last_drift
        ), f"flag must name the unaddressed goal: {rpi.last_drift}"
