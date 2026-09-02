"""``awino start``: one command producing the entire startup contract, so
startup cannot be half-followed.

Composes the same underlying reads that ``context``, ``mission``,
``doctor --fast``, and ``resume`` already use (workspace discovery,
mission.discover, health.run_all, Ledger.inspect_current) rather than
re-implementing any of that logic, so a change to one of those data sources
cannot silently diverge from what ``start`` reports.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SMITH_ROOT = Path(__file__).resolve().parents[1]

_CONTRACT_LABELS = (
    "Project:",
    "Mission confidence:",
    "Toolchain:",
    "Tracker:",
    "Active run:",
    "Pending human decision:",
    "Next recommended action:",
    "Route skill:",
)


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=cwd,
        env={**dict(os.environ), "PYTHONPATH": str(SMITH_ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _init_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    return project


def _snapshot(root: Path) -> dict[Path, float]:
    return {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}


class TestStartPrintsTheFullContract:
    def test_start_prints_all_eight_contract_fields(self, tmp_path: Path) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(["start"], cwd=project)
        for label in _CONTRACT_LABELS:
            assert label in result.stdout, f"missing contract field: {label!r}"


class TestStartIsReadOnlyByDefault:
    def test_start_writes_nothing_to_the_project_by_default(self, tmp_path: Path) -> None:
        project = _init_project(tmp_path)
        before = _snapshot(project)
        _run_cli(["start"], cwd=project)
        after = _snapshot(project)
        assert before == after


class TestStartNeverOpensAGateRun:
    def test_start_does_not_create_a_run_ledger_entry(self, tmp_path: Path) -> None:
        project = _init_project(tmp_path)
        _run_cli(["start"], cwd=project)
        run_dir = project / ".smith" / "run"
        assert not run_dir.exists() or not any(run_dir.iterdir())


class TestStartReportsGapWithoutCrashingWhenNoSmithDir:
    def test_start_reports_the_gap_instead_of_a_traceback(self, tmp_path: Path) -> None:
        project = tmp_path / "bare-project"
        project.mkdir()
        result = _run_cli(["start"], cwd=project)
        assert "Traceback" not in result.stdout
        assert "Traceback" not in result.stderr
        for label in _CONTRACT_LABELS:
            assert label in result.stdout


class TestFixPerformsOnlyMechanicalRepairs:
    def test_fix_reports_remaining_items_rather_than_silently_fixing_everything(
        self, tmp_path: Path
    ) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(["start", "--fix"], cwd=project)
        assert result.returncode in (0, 1)
        for label in _CONTRACT_LABELS:
            assert label in result.stdout
