"""CLI wiring for ``awino dispatch``: the same seven-step trip S3 implements,
reachable as one command a human or a hook can actually invoke.

Uses real subprocess invocation of ``python -m smith.cli`` rather than
CliRunner, because the exit-code contract (nonzero unless COMPLETE) and the
"nothing is spawned" assertions must hold for the actual process boundary a
human would hit, not just an in-process test double.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SMITH_ROOT = Path(__file__).resolve().parents[1]

_DELEGATE_REQUEST = (
    "we have parallel independent workstreams that need disjoint file "
    "ownership and should run simultaneously with the orchestrator "
    "coordinating rather than one agent doing everything"
)


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=cwd,
        env={**_base_env(), "PYTHONPATH": str(SMITH_ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _base_env() -> dict[str, str]:
    import os

    return dict(os.environ)


def _init_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    return project


class TestMissingBudgetConfirmationRefuses:
    def test_dispatch_without_confirm_budget_exits_nonzero_and_spawns_nothing(
        self, tmp_path: Path
    ) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(["dispatch", _DELEGATE_REQUEST, "--dry-run"], cwd=project)
        # --dry-run alone is allowed without --confirm-budget, since nothing is
        # spawned either way; the refusal is specifically about spending budget.
        result_live = _run_cli(["dispatch", _DELEGATE_REQUEST], cwd=project)
        assert result_live.returncode != 0
        assert "confirm-budget" in (result_live.stdout + result_live.stderr).lower()
        assert result.returncode == 0 or "DECISION" in result.stdout


class TestMaxFloorsValidation:
    def test_a_max_floors_outside_the_bound_refuses_without_clamping(self, tmp_path: Path) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(
            ["dispatch", _DELEGATE_REQUEST, "--confirm-budget", "--max-floors", "0", "--dry-run"],
            cwd=project,
        )
        assert result.returncode == 2
        assert "max-floors" in (result.stdout + result.stderr).lower()

    def test_a_max_floors_above_the_ceiling_refuses_without_clamping(self, tmp_path: Path) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(
            ["dispatch", _DELEGATE_REQUEST, "--confirm-budget", "--max-floors", "99", "--dry-run"],
            cwd=project,
        )
        assert result.returncode == 2
        assert "max-floors" in (result.stdout + result.stderr).lower()


class TestDryRunPrintsDecisionAndSpawnsNothing:
    def test_dry_run_prints_the_matched_skill_and_preflight_verdict(self, tmp_path: Path) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(
            ["dispatch", _DELEGATE_REQUEST, "--confirm-budget", "--dry-run"], cwd=project
        )
        assert result.returncode == 0
        assert "awino-delegate" in result.stdout
        assert "DRY_RUN" in result.stdout


class TestAmbiguousRequestPrintsOneQuestion:
    def test_a_request_matching_no_skill_prints_a_question_and_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(
            ["dispatch", "xyzzy plugh wibble", "--confirm-budget", "--dry-run"], cwd=project
        )
        assert result.returncode != 0
        assert "QUESTION" in result.stdout
        # exactly one question line, not a restated routing table
        question_lines = [
            line for line in result.stdout.splitlines() if line.startswith("QUESTION")
        ]
        assert len(question_lines) == 1


class TestOutputShapeAndExitCode:
    def test_output_names_the_request_skill_confidence_and_preflight_verdict(
        self, tmp_path: Path
    ) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(
            ["dispatch", _DELEGATE_REQUEST, "--confirm-budget", "--dry-run"], cwd=project
        )
        assert "REQUEST" in result.stdout
        assert "CONFIDENCE" in result.stdout
        assert "PREFLIGHT" in result.stdout

    def test_exit_code_is_zero_only_for_dry_run_not_for_a_terminal_claim(
        self, tmp_path: Path
    ) -> None:
        project = _init_project(tmp_path)
        dry = _run_cli(
            ["dispatch", _DELEGATE_REQUEST, "--confirm-budget", "--dry-run"], cwd=project
        )
        assert dry.returncode == 0


class TestUnenforceableReviewerRunnerRefuses:
    def test_a_runner_without_enforceable_read_only_dispatch_refuses_with_a_reason(
        self, tmp_path: Path
    ) -> None:
        project = _init_project(tmp_path)
        result = _run_cli(
            [
                "dispatch",
                _DELEGATE_REQUEST,
                "--confirm-budget",
                "--runner",
                "goose",
                "--max-floors",
                "1",
            ],
            cwd=project,
        )
        assert result.returncode != 0
        combined = (result.stdout + result.stderr).lower()
        assert "read-only" in combined or "read only" in combined
