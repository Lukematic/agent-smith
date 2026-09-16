"""Phase 1 recovery tests: journaled .smith -> .awino cutover and state conflicts,
installation-owned metadata classification, blocking _locate, baseline capture,
and startup reporting usable tools.

These are the pass criteria of A.W.I.N.O. recovery Phase 1 (Startup and
source/state identity): conflicting state refuses, interrupted migration
resumes, hashes prove byte identity, rollback restores the prior invocation,
`.in_use` is preserved as host-owned metadata, blocking health can never
return "healthy", and `awino start` reports usable tool commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from awino import cutover, fair, health, install_meta, stepper
from awino.enforce import Ledger
from awino.health import Health
from awino.machine import Machine, Node, advance
from awino.paths import AwinoPaths
from awino.skill_catalog import SkillCatalog
from awino.tidy import Finding, Tidier

SMITH_ROOT = Path(__file__).resolve().parents[1]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def legacy_project(tmp_path: Path) -> Path:
    """A pre-cutover project: populated `.smith/`, nothing else."""
    project = tmp_path / "project"
    legacy = project / ".smith"
    (legacy / "run").mkdir(parents=True)
    (legacy / "memory").mkdir(parents=True)
    (legacy / "run" / "ledger.jsonl").write_text(
        '{"run_id": "r1", "event": "opened"}\n{"run_id": "r1", "event": "closed"}\n',
        encoding="utf-8",
    )
    (legacy / "memory" / "lessons.md").write_text(
        "# lessons\n\n- never migrate twice\n", encoding="utf-8"
    )
    (legacy / "MISSION.md").write_text("# mission\n", encoding="utf-8")
    return project


def _plan(project: Path) -> cutover.CutoverPlan:
    return cutover.plan_cutover(project, home=AwinoPaths(root=SMITH_ROOT))


def _snapshot(root: Path) -> dict[str, str]:
    digest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest[path.relative_to(root).as_posix()] = _sha256_bytes(path.read_bytes())
    return digest


# ── cutover planning ─────────────────────────────────────────────────────────


class TestPlanCutover:
    def test_plan_records_source_target_state_and_interpreter(self, legacy_project: Path) -> None:
        plan = _plan(legacy_project)
        assert plan.status == "migrate"
        assert plan.legacy == legacy_project / ".smith"
        assert plan.canonical == legacy_project / ".awino"
        assert plan.identity.runtime_home == SMITH_ROOT
        assert plan.identity.target_project == legacy_project
        assert plan.identity.state_root == legacy_project / ".awino"
        assert plan.identity.interpreter == sys.executable
        assert plan.inventory, "inventory must cover the legacy files"
        assert set(plan.inventory) == {
            "run/ledger.jsonl",
            "memory/lessons.md",
            "MISSION.md",
        }

    def test_plan_with_no_legacy_is_nothing_to_do(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        assert _plan(project).status == "nothing-to-do"

    def test_plan_with_identical_awino_is_redundant(self, legacy_project: Path) -> None:
        import shutil

        shutil.copytree(legacy_project / ".smith", legacy_project / ".awino")
        assert _plan(legacy_project).status == "redundant"

    def test_conflicting_state_refuses_and_preserves_both(self, legacy_project: Path) -> None:
        (legacy_project / ".awino").mkdir()
        (legacy_project / ".awino" / "memory").mkdir()
        (legacy_project / ".awino" / "memory" / "lessons.md").write_text(
            "# different lessons\n", encoding="utf-8"
        )
        with pytest.raises(cutover.CutoverRefused, match=r"memory/lessons\.md"):
            _plan(legacy_project)
        # Both directories survive untouched; nothing merged, nothing deleted.
        assert (legacy_project / ".smith" / "memory" / "lessons.md").is_file()
        assert (
            (legacy_project / ".awino" / "memory" / "lessons.md")
            .read_text(encoding="utf-8")
            .startswith("# different")
        )


# ── execute / verify / resume ────────────────────────────────────────────────


class TestExecute:
    def test_execute_copies_every_file_byte_identical(self, legacy_project: Path) -> None:
        plan = _plan(legacy_project)
        before = _snapshot(legacy_project / ".smith")
        result = cutover.execute(plan)
        assert sorted(result.copied) == sorted(before)
        after = {
            k: v
            for k, v in _snapshot(legacy_project / ".awino").items()
            if k != cutover.JOURNAL_NAME
        }
        assert after == before, "migration changed bytes"
        # Legacy source is untouched: execute copies, never moves.
        assert (legacy_project / ".smith" / "MISSION.md").is_file()
        assert cutover.verify(plan) == []

    def test_execute_is_idempotent(self, legacy_project: Path) -> None:
        plan = _plan(legacy_project)
        cutover.execute(plan)
        second = cutover.execute(plan)
        assert second.copied == []
        assert second.resumed
        assert cutover.verify(plan) == []

    def test_interrupted_migration_resumes(self, legacy_project: Path) -> None:
        """Simulate a crash mid-copy: truncated journal, missing files.

        Re-running execute must re-copy only what is missing and finish with
        byte-identical content, without re-copying what already verified.
        """
        plan = _plan(legacy_project)
        cutover.execute(plan)
        journal = cutover.journal_path(plan)
        records = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        copy_ops = [r for r in records if r.get("op") == "copy"]
        assert len(copy_ops) >= 2
        # Crash: keep only the first copy op journaled, delete a later file.
        kept = [r for r in records if r.get("op") != "copy"] + copy_ops[:1]
        journal.write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in kept) + "\n",
            encoding="utf-8",
        )
        victim = legacy_project / ".awino" / copy_ops[1]["rel"]
        assert victim.is_file()
        victim.unlink()

        result = cutover.execute(plan)
        assert result.resumed
        assert copy_ops[1]["rel"] in result.copied
        assert copy_ops[0]["rel"] in result.verified  # not re-copied
        assert copy_ops[0]["rel"] not in result.copied
        assert cutover.verify(plan) == []

    def test_verify_reports_tampering(self, legacy_project: Path) -> None:
        plan = _plan(legacy_project)
        cutover.execute(plan)
        tampered = legacy_project / ".awino" / "MISSION.md"
        tampered.write_text("# tampered\n", encoding="utf-8")
        assert cutover.verify(plan) == ["MISSION.md"]


# ── checks / finalize / rollback ─────────────────────────────────────────────


class TestCutoverChecks:
    def test_all_three_checks_pass_on_a_clean_migration(self, legacy_project: Path) -> None:
        plan = _plan(legacy_project)
        cutover.execute(plan)
        checks = cutover.run_checks(plan)
        assert checks.passed, checks.notes
        assert checks.launcher_ok and checks.restart_ok and checks.rollback_ok
        # The rollback check rolls back for real, then re-executes: the notes
        # record both halves, and the migration is complete again afterwards.
        assert any("rollback" in note for note in checks.notes)
        assert cutover.verify(plan) == []
        journal_text = cutover.journal_path(plan).read_text(encoding="utf-8")
        assert '"op": "checks"' in journal_text
        assert '"rollback_ok": true' in journal_text


class TestFinalize:
    def test_finalize_archives_legacy_only_after_passing_checks(self, legacy_project: Path) -> None:
        plan = _plan(legacy_project)
        cutover.execute(plan)
        checks = cutover.run_checks(plan)
        assert checks.passed
        before = _snapshot(legacy_project / ".smith")
        archived = cutover.finalize(plan, checks)
        assert not (legacy_project / ".smith").exists()
        assert archived.is_dir()
        assert _snapshot(archived) == before
        # The canonical dir keeps the migrated bytes.
        after = {
            k: v
            for k, v in _snapshot(legacy_project / ".awino").items()
            if k != cutover.JOURNAL_NAME and not k.startswith("archive/")
        }
        assert after == before
        journal_text = cutover.journal_path(plan).read_text(encoding="utf-8")
        assert '"op": "archive"' in journal_text
        assert '"op": "pointers-switched"' in journal_text

    def test_finalize_refuses_without_passing_checks(self, legacy_project: Path) -> None:
        plan = _plan(legacy_project)
        cutover.execute(plan)
        failed = cutover.CutoverChecks(False, False, False, ["nope"])
        with pytest.raises(cutover.CutoverError, match="did not pass"):
            cutover.finalize(plan, failed)
        assert (legacy_project / ".smith").is_dir(), "legacy must survive refusal"

    def test_rollback_after_finalize_restores_the_prior_invocation(
        self, legacy_project: Path
    ) -> None:
        plan = _plan(legacy_project)
        before = _snapshot(legacy_project / ".smith")
        cutover.execute(plan)
        checks = cutover.run_checks(plan)
        cutover.finalize(plan, checks)
        assert not (legacy_project / ".smith").exists()

        cutover.rollback(plan)
        assert _snapshot(legacy_project / ".smith") == before
        for rel in before:
            assert not (legacy_project / ".awino" / rel).exists()
        # The prior invocation is fully restored: .smith present with its
        # original bytes, and the cutover-created .awino gone entirely.
        assert not (legacy_project / ".awino").exists()

    def test_update_global_wrapper_points_to_canonical(self, tmp_path: Path) -> None:
        wrapper = tmp_path / "awino.ps1"
        wrapper.write_text("& 'old/path/bin/awino.ps1' @args\n", encoding="utf-8")
        canonical = tmp_path / "canonical"
        updated = cutover.update_global_wrapper(canonical, wrapper_path=wrapper)
        assert updated is True
        assert str(canonical / "bin" / "awino.ps1") in wrapper.read_text(encoding="utf-8")


class TestRollback:
    def test_rollback_before_finalize_restores_legacy_untouched(self, legacy_project: Path) -> None:
        plan = _plan(legacy_project)
        before = _snapshot(legacy_project / ".smith")
        cutover.execute(plan)
        removed = cutover.rollback(plan)
        assert sorted(removed) == sorted(before)
        assert _snapshot(legacy_project / ".smith") == before
        assert not (legacy_project / ".awino").exists()

    def test_rollback_preserves_unknown_local_edits_to_migrated_copies(
        self, legacy_project: Path
    ) -> None:
        plan = _plan(legacy_project)
        cutover.execute(plan)
        edited = legacy_project / ".awino" / "MISSION.md"
        edited.write_text("# operator's later edit\n", encoding="utf-8")
        removed = cutover.rollback(plan)
        assert "MISSION.md" not in removed
        assert edited.read_text(encoding="utf-8") == "# operator's later edit\n"


# ── installation-owned metadata ──────────────────────────────────────────────


def _minimal_home(tmp_path: Path) -> AwinoPaths:
    (tmp_path / "README.md").write_text("# test\n", encoding="utf-8")
    return AwinoPaths(root=tmp_path)


class TestInstallationMetadataClassification:
    def test_in_use_is_installation_metadata(self) -> None:
        assert install_meta.is_installation_metadata(".in_use")
        assert (
            install_meta.classify_root_entry(".in_use")
            is install_meta.RootEntryKind.INSTALLATION_METADATA
        )

    def test_arbitrary_hidden_files_are_not_installation_metadata(self) -> None:
        for name in (".hidden-junk", ".env", ".DS_Store", ".random"):
            assert not install_meta.is_installation_metadata(name), name


class TestInUseIsPreserved:
    def test_tidy_does_not_flag_in_use_as_stray(self, tmp_path: Path) -> None:
        home = _minimal_home(tmp_path)
        (tmp_path / ".in_use").write_text("gui host marker\n", encoding="utf-8")
        findings = Tidier(home).scan()
        assert [f for f in findings if f.path.name == ".in_use"] == []

    def test_tidy_still_flags_arbitrary_hidden_files(self, tmp_path: Path) -> None:
        home = _minimal_home(tmp_path)
        (tmp_path / ".random-junk").write_text("clutter\n", encoding="utf-8")
        findings = Tidier(home).scan()
        stray = [f for f in findings if f.kind is Finding.STRAY_ROOT_FILE]
        assert [f.path.name for f in stray] == [".random-junk"]

    def test_health_structure_gate_passes_with_in_use_present(self, tmp_path: Path) -> None:
        home = _minimal_home(tmp_path)
        (tmp_path / ".in_use").write_text("gui host marker\n", encoding="utf-8")
        result = health.check_structure(home)
        assert result.health is not Health.FAIL, result.detail

    def test_archive_refuses_installation_metadata_even_when_passed_explicitly(
        self, tmp_path: Path
    ) -> None:
        from awino.tidy import Clutter

        home = _minimal_home(tmp_path)
        marker = tmp_path / ".in_use"
        marker.write_text("gui host marker\n", encoding="utf-8")
        tidier = Tidier(home)
        _, moved = tidier.archive([Clutter(Finding.STRAY_ROOT_FILE, marker, "x")])
        assert moved == []
        assert marker.is_file(), "archive must never move host-owned metadata"

    def test_clean_never_deletes_installation_metadata(self, tmp_path: Path) -> None:
        from awino.tidy import Clutter

        home = _minimal_home(tmp_path)
        marker = tmp_path / ".in_use"
        marker.write_text("gui host marker\n", encoding="utf-8")
        tidier = Tidier(home)
        removed = tidier.clean([Clutter(Finding.DISPOSABLE, marker, "x")])
        assert removed == []
        assert marker.is_file(), "clean must never delete host-owned metadata"

    def test_fair_does_not_demand_docs_for_an_in_use_directory(self, tmp_path: Path) -> None:
        marker_dir = tmp_path / ".in_use"
        marker_dir.mkdir()
        (marker_dir / "lock").write_text("x\n", encoding="utf-8")
        assert fair.is_exempt(marker_dir, tmp_path) is not None


# ── blocking _locate ─────────────────────────────────────────────────────────


def _locate_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> stepper.StepContext:
    state = tmp_path / "state"
    project = tmp_path / "project"
    home = tmp_path / "home"
    for directory in (state, project, home):
        directory.mkdir(parents=True)
    monkeypatch.setattr(stepper.health, "run_all", lambda _p, fast=True: [])
    return stepper.StepContext(
        state_root=state,
        project=project,
        home=home,
        paths=AwinoPaths(root=home),
        ledger=Ledger(state),
        catalog=SkillCatalog(Path("/n"), Path("/n"), SMITH_ROOT / "skills"),
        scope=["tests/test_a.py"],
        verify="pytest -q",
    )


class TestBlockingLocate:
    def test_blocking_health_returns_unhealthy_not_healthy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _locate_context(tmp_path, monkeypatch)
        monkeypatch.setattr(
            stepper.health,
            "run_all",
            lambda _p, fast=True: [health.Result("structure", Health.FAIL, "clutter", "tidy it")],
        )
        m = Machine(node=Node.LOCATE, request="pytest is failing")
        assert stepper._locate(m, ctx) == "unhealthy"
        assert any("HEALTH" in ln and "failing" in ln for ln in ctx.lines or [])

    def test_unhealthy_advances_to_stop_not_route(self) -> None:
        m = Machine(node=Node.LOCATE)
        assert advance(m, "unhealthy").node is Node.STOP

    def test_locate_still_routes_healthy_and_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _locate_context(tmp_path, monkeypatch)
        # A bare project has no auto-provisionable gaps in this fixture.
        monkeypatch.setattr(stepper.provision, "plan", lambda _p, _s=None: [])
        m = Machine(node=Node.LOCATE, request="pytest is failing")
        assert stepper._locate(m, ctx) == "healthy"
        assert advance(Machine(node=Node.LOCATE), "healthy").node is Node.ROUTE
        assert advance(Machine(node=Node.LOCATE), "missing").node is Node.PROVISION


# ── baseline capture ─────────────────────────────────────────────────────────


class TestBaselineCapture:
    def test_capture_records_commit_identity_hashes_and_manifests(self) -> None:
        from awino import baseline

        document = baseline.capture()
        expected_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SMITH_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
        assert document["git"]["commit"] == expected_commit
        assert document["git"]["branch"]
        assert "pyproject.toml" in document["file_hashes"]
        assert set(document["manifest_diffs"]) == {"pyproject.toml", "uv.lock"}
        assert set(document["unique_edits"]) == {
            "status_porcelain",
            "diff_stat",
            "diff",
            "untracked",
        }
        assert document["source_files"] > 0


# ── startup reporting ────────────────────────────────────────────────────────


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "awino.cli", *args],
        cwd=cwd,
        env={**dict(os.environ), "PYTHONPATH": str(SMITH_ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


class TestStartReportsUsableTools:
    def test_toolchain_line_names_usable_commands_not_categories(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        result = _run_cli(["start"], cwd=project)
        assert result.returncode == 0, result.stderr
        toolchain_line = next(
            ln for ln in result.stdout.splitlines() if ln.startswith("Toolchain:")
        )
        # "lint=ruff check ..." names the command; a bare "lint" is a category.
        assert "=" in toolchain_line, toolchain_line

    def test_start_names_the_source_installation(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        result = _run_cli(["start"], cwd=project)
        assert result.returncode == 0, result.stderr
        source_line = next(ln for ln in result.stdout.splitlines() if ln.startswith("Source:"))
        assert str(SMITH_ROOT) in source_line or "awino" in source_line.lower()
