"""Real subprocess CLI test for ``awino review-doc``.

Matches the subprocess pattern used by tests/test_gate_review_workflow.py and
tests/test_cli_encoding.py (encoding="utf-8" per commit 78d7556).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_cli(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SMITH_PROJECT"] = str(project)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", "review-doc", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def _toy_project(tmp_path: Path) -> Path:
    project = tmp_path / "toy-project"
    project.mkdir()
    return project


def test_review_doc_single_pass_approves_a_clean_spec(tmp_path: Path) -> None:
    project = _toy_project(tmp_path)
    spec = project / "spec.md"
    spec.write_text(
        "# Spec\n\n## Goals\n\nShip the thing.\n\n## Non-Goals\n\nNo scope creep.\n",
        encoding="utf-8",
    )
    result = run_cli(project, str(spec), "--rubric", "spec")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUBRIC  spec" in result.stdout
    assert "APPROVED" in result.stdout


def test_review_doc_single_pass_refuses_on_placeholder_content(tmp_path: Path) -> None:
    project = _toy_project(tmp_path)
    plan = project / "plan.md"
    plan.write_text(
        "# Plan\n\n## Step One\n\nTODO: figure this out.\n",
        encoding="utf-8",
    )
    result = run_cli(project, str(plan), "--rubric", "plan")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Completeness" in result.stdout
    assert "REFUSED" in result.stdout


def test_review_doc_rejects_an_invalid_rubric_flag(tmp_path: Path) -> None:
    project = _toy_project(tmp_path)
    spec = project / "spec.md"
    spec.write_text("# Spec\n", encoding="utf-8")
    result = run_cli(project, str(spec), "--rubric", "bogus")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSED" in result.stdout


def test_review_doc_rejects_a_missing_document(tmp_path: Path) -> None:
    project = _toy_project(tmp_path)
    missing = project / "nope.md"
    result = run_cli(project, str(missing), "--rubric", "spec")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "DOCUMENT_NOT_FOUND" in result.stdout


def test_review_doc_run_loop_caps_on_same_disagreement_against_a_never_fixed_doc(
    tmp_path: Path,
) -> None:
    # The default scorer (score_document) is deterministic: an unfixed
    # placeholder document raises the identical Completeness issue every
    # pass, so --run-loop against a static file on disk hits the
    # same-disagreement cap (3) through the real CLI before ever reaching
    # the separate 5-iteration hard cap.
    project = _toy_project(tmp_path)
    plan = project / "plan.md"
    plan.write_text("# Plan\n\n## Step One\n\nTODO: never resolved.\n", encoding="utf-8")
    result = run_cli(project, str(plan), "--rubric", "plan", "--run-loop")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "THREE_STRIKES after 3" in result.stdout
    assert result.stdout.count("ITERATION ") == 3
