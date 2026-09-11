"""Tests for the Delegate loop driver (decompose -> assign -> execute ->
controller-verify) and its CLI.

The controller decomposes work with per-worker file ownership, machine-checks
ownership before execution, and re-verifies every done claim against real
evidence. A false done fails loudly and names the worker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import loops
from awino.cli.loopctl import loop_app
from awino.enforce import Ledger
from awino.seeds import Issue, Seeds, SeedsResult, SeedsState

REPO_ROOT = Path(__file__).resolve().parents[1]
DELEGATE_SKILL = REPO_ROOT / "skills" / "awino-delegate" / "SKILL.md"

DECOMPOSE_OK = """# Decompose: split the parser work

## Assignments

### alice
files:
src/a.py
src/b.py

Task: refactor the parser in src/a.py, update helpers in src/b.py.

### bob
files:
src/c.py

Task: update the tests in src/c.py.
"""

EXECUTE_OK = """# Execute: worker results

## Results

### alice
done: refactored the parser
check: true

### bob
done: updated the tests
output: src/c.py
"""


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    for rel in ("src/a.py", "src/b.py", "src/c.py"):
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\nprint('hi')\n", encoding="utf-8")
    return project


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return _project(tmp_path)


@pytest.fixture()
def driver(project: Path, tmp_path: Path) -> loops.DelegateDriver:
    return loops.DelegateDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=DELEGATE_SKILL,
    )


@pytest.fixture()
def event_driver(
    project: Path, tmp_path: Path, loop_ledger: Ledger
) -> loops.DelegateDriver:
    return loops.DelegateDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=DELEGATE_SKILL,
        ledger=loop_ledger,
    )


@pytest.fixture()
def loop_ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / ".awino")


@pytest.fixture()
def seeds_closed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    closed: list[tuple[str, str]] = []
    monkeypatch.setattr(Seeds, "state", lambda self: (SeedsState.READY, "ok"))
    monkeypatch.setattr(
        Seeds,
        "show",
        lambda self, issue_id: Issue(
            id=issue_id, title="Split the work", status="open",
            type="task", priority=2,
        ),
    )

    def fake_close(self, issue_id: str, reason: str) -> SeedsResult:
        closed.append((issue_id, reason))
        return SeedsResult(ok=True, command="sd close", detail="closed", payload=None)

    monkeypatch.setattr(Seeds, "close", fake_close)
    return closed


def _write(driver: loops.DelegateDriver, state: loops.LoopState, kind: str, text: str) -> None:
    rel = state.decompose_artifact if kind == "decompose" else state.execute_artifact
    path = driver.project_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _at_assign(driver: loops.DelegateDriver) -> loops.LoopState:
    state = driver.new("split the parser work")
    _write(driver, state, "decompose", DECOMPOSE_OK)
    assert driver.check(state) == []
    assert driver.advance(state) == "assign"
    return driver.load(state.id)


def _at_verify(driver: loops.DelegateDriver) -> loops.LoopState:
    state = _at_assign(driver)
    assert driver.check(state) == []  # assign machine-check passes
    assert driver.advance(state) == "execute"
    state = driver.load(state.id)
    _write(driver, state, "execute", EXECUTE_OK)
    assert driver.check(state) == []
    assert driver.advance(state) == "controller-verify"
    return driver.load(state.id)


class TestDelegateNew:
    def test_new_starts_at_decompose(self, driver: loops.DelegateDriver) -> None:
        state = driver.new("split the parser work")
        assert state.phase == "decompose"
        assert state.id.startswith("delegate-")
        assert loops.kind_of(state.id) == "delegate"

    def test_phase_prompts_come_from_the_delegate_skill(self) -> None:
        assert "ownership" in loops.skill_section(
            DELEGATE_SKILL, "Step 1 — Decompose and check ownership", "awino-delegate"
        ).lower()
        assert loops.skill_section(
            DELEGATE_SKILL, "Step 6 — Verify and synthesize", "awino-delegate"
        ).strip()

    def test_missing_delegate_skill_section_refuses_to_improvise(
        self, tmp_path: Path
    ) -> None:
        empty = tmp_path / "EMPTY.md"
        empty.write_text("# nothing here\n", encoding="utf-8")
        with pytest.raises(loops.LoopError, match="refusing to invent"):
            loops.skill_section(empty, "Step 1 — Decompose and check ownership", "awino-delegate")


class TestDecomposeValidation:
    def test_valid_decompose_passes(self, driver: loops.DelegateDriver) -> None:
        state = driver.new("split the parser work")
        _write(driver, state, "decompose", DECOMPOSE_OK)
        assert driver.check(state) == []

    def test_missing_decompose_artifact_names_the_path(
        self, driver: loops.DelegateDriver
    ) -> None:
        state = driver.new("split the parser work")
        missing = driver.check(state)
        assert missing
        assert state.decompose_artifact in missing[0]

    def test_decompose_without_assignments_section_rejected(
        self, driver: loops.DelegateDriver
    ) -> None:
        state = driver.new("split the parser work")
        _write(driver, state, "decompose", "# Decompose\n\nNo assignments here.\n")
        missing = driver.check(state)
        assert any("Assignments" in item for item in missing)

    def test_worker_without_files_rejected(self, driver: loops.DelegateDriver) -> None:
        state = driver.new("split the parser work")
        _write(
            driver, state, "decompose",
            "# Decompose\n\n## Assignments\n\n### alice\n\nTask: do things.\n",
        )
        missing = driver.check(state)
        assert any("alice" in item and "no files" in item for item in missing)

    def test_files_list_ignores_bullet_prefixed_prose(
        self, driver: loops.DelegateDriver
    ) -> None:
        """The documented format is one bare path per line under `files:`.
        Bullet lines are prose, not ownership claims: a worker whose only
        `files:` content is bullets claims no files."""
        state = driver.new("split the parser work")
        _write(
            driver, state, "decompose",
            "# Decompose\n\n## Assignments\n\n### alice\nfiles:\n- src/a.py\n- src/b.py\n",
        )
        missing = driver.check(state)
        assert any("alice" in item and "no files" in item for item in missing)

    def test_files_list_accepts_bare_paths(self, driver: loops.DelegateDriver) -> None:
        state = driver.new("split the parser work")
        _write(
            driver, state, "decompose",
            "# Decompose\n\n## Assignments\n\n### alice\nfiles:\nsrc/a.py\n",
        )
        assert driver.check(state) == []


class TestAssignOwnership:
    def test_overlapping_ownership_rejected_naming_workers_and_path(
        self, driver: loops.DelegateDriver
    ) -> None:
        state = driver.new("split the parser work")
        _write(
            driver, state, "decompose",
            "# Decompose\n\n## Assignments\n\n"
            "### alice\nfiles:\nsrc/a.py\nsrc/b.py\n\n"
            "### bob\nfiles:\nsrc/b.py\nsrc/c.py\n",
        )
        assert driver.check(state) == []  # decompose shape is fine
        assert driver.advance(state) == "assign"
        missing = driver.check(state)
        assert len(missing) == 1
        assert "alice" in missing[0] and "bob" in missing[0]
        assert "src/b.py" in missing[0]

    def test_nonexistent_claimed_path_rejected(
        self, driver: loops.DelegateDriver
    ) -> None:
        state = driver.new("split the parser work")
        _write(
            driver, state, "decompose",
            "# Decompose\n\n## Assignments\n\n### alice\nfiles:\nsrc/does-not-exist.py\n",
        )
        driver.advance(state)
        missing = driver.check(state)
        assert missing
        assert "src/does-not-exist.py" in missing[0]
        assert "alice" in missing[0]

    def test_clean_ownership_passes_assign(self, driver: loops.DelegateDriver) -> None:
        state = _at_assign(driver)
        assert driver.check(state) == []
        assert driver.advance(state) == "execute"

    def test_three_failed_assigns_lock_the_loop(
        self, driver: loops.DelegateDriver
    ) -> None:
        state = driver.new("split the parser work")
        _write(
            driver, state, "decompose",
            "# Decompose\n\n## Assignments\n\n"
            "### alice\nfiles:\nsrc/a.py\n\n"
            "### bob\nfiles:\nsrc/a.py\n",
        )
        driver.advance(state)
        for _ in range(loops.MAX_ATTEMPTS):
            assert driver.check(state)
        state = driver.load(state.id)
        assert state.locked


class TestExecuteValidation:
    def test_valid_execute_passes(self, driver: loops.DelegateDriver) -> None:
        state = _at_assign(driver)
        driver.advance(state)
        state = driver.load(state.id)
        _write(driver, state, "execute", EXECUTE_OK)
        assert driver.check(state) == []

    def test_worker_without_done_claim_rejected(
        self, driver: loops.DelegateDriver
    ) -> None:
        state = _at_assign(driver)
        driver.advance(state)
        state = driver.load(state.id)
        _write(
            driver, state, "execute",
            "# Execute\n\n## Results\n\n### alice\ndone: did the thing\ncheck: true\n\n"
            "### bob\n\nNo claim here.\n",
        )
        missing = driver.check(state)
        assert any("bob" in item and "done" in item for item in missing)


class TestControllerVerify:
    def test_all_claims_verified_completes(
        self, event_driver: loops.DelegateDriver
    ) -> None:
        state = _at_verify(event_driver)
        assert event_driver.advance(state) == "done"
        state = event_driver.load(state.id)
        assert state.phase == "done"
        assert not state.locked

    def test_verify_emits_event_trail(
        self, event_driver: loops.DelegateDriver, loop_ledger: Ledger
    ) -> None:
        state = _at_verify(event_driver)
        event_driver.advance(state)
        kinds = [event.kind for event in loop_ledger.loop_events(state.id)]
        assert kinds[-1] == "loop_closed"
        assert kinds.count("phase_started") == 4  # decompose, assign, execute, verify

    def test_false_done_fails_naming_the_worker(
        self, driver: loops.DelegateDriver
    ) -> None:
        state = _at_assign(driver)
        driver.advance(state)
        state = driver.load(state.id)
        _write(
            driver, state, "execute",
            "# Execute\n\n## Results\n\n"
            "### alice\ndone: refactored the parser\ncheck: false\n\n"
            "### bob\ndone: updated the tests\noutput: src/c.py\n",
        )
        assert driver.check(state) == []
        driver.advance(state)
        missing = driver.check(state)
        assert len(missing) == 1
        assert "alice" in missing[0]
        assert "claimed done but verification failed" in missing[0]

    def test_missing_output_file_fails_naming_the_worker(
        self, driver: loops.DelegateDriver
    ) -> None:
        state = _at_assign(driver)
        driver.advance(state)
        state = driver.load(state.id)
        _write(
            driver, state, "execute",
            "# Execute\n\n## Results\n\n"
            "### alice\ndone: did the thing\noutput: src/nope.py\n",
        )
        driver.advance(state)
        missing = driver.check(state)
        assert missing
        assert "alice" in missing[0]
        assert "src/nope.py" in missing[0]

    def test_empty_output_file_fails(self, driver: loops.DelegateDriver) -> None:
        empty = driver.project_root / "src" / "empty.py"
        empty.write_text("", encoding="utf-8")
        state = _at_assign(driver)
        driver.advance(state)
        state = driver.load(state.id)
        _write(
            driver, state, "execute",
            "# Execute\n\n## Results\n\n### alice\ndone: did the thing\noutput: src/empty.py\n",
        )
        driver.advance(state)
        missing = driver.check(state)
        assert missing
        assert "empty" in missing[0]

    def test_claim_with_no_evidence_fails(self, driver: loops.DelegateDriver) -> None:
        state = _at_assign(driver)
        driver.advance(state)
        state = driver.load(state.id)
        _write(
            driver, state, "execute",
            "# Execute\n\n## Results\n\n### alice\ndone: trust me\n",
        )
        driver.advance(state)
        missing = driver.check(state)
        assert missing
        assert "alice" in missing[0]
        assert "nothing to verify" in missing[0]

    def test_success_closes_a_linked_seed(
        self, driver: loops.DelegateDriver, seeds_closed: list[tuple[str, str]]
    ) -> None:
        state = driver.new("split the parser work", seed_id="seed-3")
        _write(driver, state, "decompose", DECOMPOSE_OK)
        driver.advance(state)  # assign
        driver.advance(state)  # execute
        state = driver.load(state.id)
        _write(driver, state, "execute", EXECUTE_OK)
        driver.advance(state)  # controller-verify
        assert driver.advance(state) == "done"
        assert len(seeds_closed) == 1
        assert seeds_closed[0][0] == "seed-3"
        assert "re-verified" in seeds_closed[0][1]

    def test_failed_verification_never_closes_seed(
        self, driver: loops.DelegateDriver, seeds_closed: list[tuple[str, str]]
    ) -> None:
        state = driver.new("split the parser work", seed_id="seed-4")
        _write(driver, state, "decompose", DECOMPOSE_OK)
        driver.advance(state)
        driver.advance(state)
        state = driver.load(state.id)
        _write(
            driver, state, "execute",
            "# Execute\n\n## Results\n\n### alice\ndone: nope\ncheck: false\n",
        )
        driver.advance(state)
        # Three failed controller-verifies lock the loop; the seed stays open.
        for _ in range(loops.MAX_ATTEMPTS):
            assert driver.check(state)
        state = driver.load(state.id)
        assert state.locked
        assert seeds_closed == []


class TestDelegateCli:
    @pytest.fixture()
    def cli_env(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setenv("AWINO_PROJECT", str(project))
        return project

    def _artifact_path(self, output: str) -> Path:
        for line in output.splitlines():
            if line.startswith("ARTIFACT"):
                return Path(line.split(None, 1)[1].strip())
        raise AssertionError("no ARTIFACT line in output")

    def test_run_delegate_prints_decompose_prompt(self, cli_env: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(loop_app, ["run", "delegate", "--task", "split it"])
        assert result.exit_code == 0, result.output
        assert "LOOP  delegate-" in result.output
        assert "phase: decompose" in result.output

    def test_full_delegate_flow_end_to_end(self, cli_env: Path) -> None:
        runner = CliRunner()
        created = runner.invoke(loop_app, ["run", "delegate", "--task", "split it"])
        assert created.exit_code == 0, created.output
        decompose = cli_env / self._artifact_path(created.output)
        decompose.parent.mkdir(parents=True, exist_ok=True)
        decompose.write_text(DECOMPOSE_OK, encoding="utf-8")

        to_assign = runner.invoke(loop_app, ["next"])
        assert to_assign.exit_code == 0, to_assign.output
        assert "ADVANCED  phase=assign" in to_assign.output

        to_execute = runner.invoke(loop_app, ["next"])
        assert to_execute.exit_code == 0, to_execute.output
        assert "ADVANCED  phase=execute" in to_execute.output
        execute = cli_env / self._artifact_path(to_execute.output)
        execute.parent.mkdir(parents=True, exist_ok=True)
        execute.write_text(EXECUTE_OK, encoding="utf-8")

        to_verify = runner.invoke(loop_app, ["next"])
        assert to_verify.exit_code == 0, to_verify.output
        assert "ADVANCED  phase=controller-verify" in to_verify.output

        done = runner.invoke(loop_app, ["next"])
        assert done.exit_code == 0, done.output
        assert "COMPLETE" in done.output
        assert "re-verified" in done.output

    def test_assign_overlap_refused_at_cli(self, cli_env: Path) -> None:
        runner = CliRunner()
        created = runner.invoke(loop_app, ["run", "delegate", "--task", "split it"])
        decompose = cli_env / self._artifact_path(created.output)
        decompose.parent.mkdir(parents=True, exist_ok=True)
        decompose.write_text(
            "# Decompose\n\n## Assignments\n\n"
            "### alice\nfiles:\nsrc/a.py\n\n"
            "### bob\nfiles:\nsrc/a.py\n",
            encoding="utf-8",
        )
        assert runner.invoke(loop_app, ["next"]).exit_code == 0
        refused = runner.invoke(loop_app, ["next"])
        assert refused.exit_code == 1
        assert "VALIDATION_FAILED" in refused.output
        assert "src/a.py" in refused.output

    def test_status_shows_delegate_kind_and_next(self, cli_env: Path) -> None:
        runner = CliRunner()
        created = runner.invoke(loop_app, ["run", "delegate", "--task", "split it"])
        assert created.exit_code == 0, created.output
        status = runner.invoke(loop_app, ["status"])
        assert status.exit_code == 0, status.output
        assert "kind: delegate" in status.output
        assert "phase: decompose" in status.output
        assert "next:" in status.output
