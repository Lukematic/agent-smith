"""Real subprocess CLI test for ``awino gate review`` and its interaction with
``gate close`` and ``work-close``.

Exercises the full workflow: open a refactor run (refactor already requires
Gate.REVIEWED per CONTRACTS) -> attempt gate close without gate review, which
must refuse with REVIEW_REQUIRED -> run gate review with a verdict, which
records a ProvenanceRecord and satisfies REVIEWED via toolchain/diff evidence
-> gate close now succeeds. Also proves gate review's tidy --dry-run step
never modifies the project, and that in-scope generated clutter blocks close
while out-of-scope clutter only warns.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from smith.enforce import CONTRACTS, Gate, TaskClass

assert Gate.REVIEWED in CONTRACTS[TaskClass.REFACTOR], "test assumes refactor requires REVIEWED"


def run_cli(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SMITH_PROJECT"] = str(project)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _snapshot(root: Path) -> dict[str, tuple[float, str]]:
    snapshot: dict[str, tuple[float, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[str(path.relative_to(root))] = (path.stat().st_mtime, digest)
    return snapshot


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(project), check=True, capture_output=True)


def _init_repo(project: Path) -> None:
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(
        project,
        "-c",
        "user.email=t@t.com",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "init",
    )


def _toy_project(tmp_path: Path, name: str = "toy-project") -> Path:
    project = tmp_path / name
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "toy"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
        'dependencies = ["pytest"]\n'
        "[tool.pytest.ini_options]\n[tool.ruff]\n",
        encoding="utf-8",
    )
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_toy.py").write_text(
        "def test_toy_passes():\n    assert True\n",
        encoding="utf-8",
    )
    return project


def _open_refactor_run(project: Path, objective: str, scope: str) -> str:
    plan = project / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    opened = run_cli(
        project, "gate", "open", "refactor", objective, "--scope", scope, "--plan", str(plan)
    )
    assert opened.returncode == 0, opened.stdout + opened.stderr
    run_id = opened.stdout.splitlines()[0].split()[1]
    approved = run_cli(project, "gate", "plan", "approve", "--by", "reviewer", "--run", run_id)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    return run_id


def test_gate_close_refuses_without_review_then_succeeds_after_gate_review(
    tmp_path: Path,
) -> None:
    project = _toy_project(tmp_path)
    _init_repo(project)

    run_id = _open_refactor_run(project, "refactor toy module", "tests/test_toy.py")

    closed_early = run_cli(project, "gate", "close", "--run", run_id)
    assert closed_early.returncode == 1
    assert "REVIEW_REQUIRED" in closed_early.stdout
    assert "gate review" in closed_early.stdout

    reviewed = run_cli(
        project,
        "gate",
        "review",
        "--run",
        run_id,
        "--verdict",
        "approved",
        "--diff-base",
        "HEAD",
        "--by",
        "independent-reviewer",
    )
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    assert "REVIEWED  verdict=approved" in reviewed.stdout
    assert "TOOLCHAIN VERIFICATION" in reviewed.stdout
    assert "TESTS_NOT_WEAKENED  ok" in reviewed.stdout
    assert "SCOPE_RESPECTED  ok" in reviewed.stdout

    closed = run_cli(project, "gate", "close", "--run", run_id)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert "COMPLETE" in closed.stdout


def test_gate_review_tidy_dry_run_never_modifies_the_project(tmp_path: Path) -> None:
    project = _toy_project(tmp_path)
    _init_repo(project)
    run_id = _open_refactor_run(project, "refactor toy module", "tests/test_toy.py")

    before = _snapshot(project)

    # --skip-toolchain isolates the guarantee under test: tidy's dry-run scan
    # never writes. Actually running pytest as part of toolchain verification
    # legitimately creates its own __pycache__/.pytest_cache, which is a
    # side effect of running tests, not of gate review's read-only tidy step.
    reviewed = run_cli(
        project,
        "gate",
        "review",
        "--run",
        run_id,
        "--verdict",
        "approved",
        "--diff-base",
        "HEAD",
        "--skip-toolchain",
        "--by",
        "independent-reviewer",
    )
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr

    after = _snapshot(project)
    before_src = {k: v for k, v in before.items() if not k.startswith(".smith")}
    after_src = {k: v for k, v in after.items() if not k.startswith(".smith")}
    assert before_src == after_src, "gate review modified project files it should only read"


def test_in_scope_generated_clutter_blocks_close_while_out_of_scope_only_warns(
    tmp_path: Path,
) -> None:
    """In-scope clutter (declared in file_scope) blocks; out-of-scope clutter warns only."""
    project = _toy_project(tmp_path, name="scoped-project")
    # Disposable clutter declared as part of what this run may ship.
    in_scope_clutter = project / "src" / "__pycache__"
    in_scope_clutter.mkdir(parents=True)
    (in_scope_clutter / "mod.cpython-312.pyc").write_bytes(b"junk")
    # Disposable clutter elsewhere in the repo, never declared as in scope.
    out_of_scope_clutter = project / "other" / "__pycache__"
    out_of_scope_clutter.mkdir(parents=True)
    (out_of_scope_clutter / "mod.cpython-312.pyc").write_bytes(b"junk")

    _init_repo(project)
    run_id = _open_refactor_run(project, "refactor toy module", "src/__pycache__/")

    reviewed = run_cli(
        project,
        "gate",
        "review",
        "--run",
        run_id,
        "--verdict",
        "approved",
        "--diff-base",
        "HEAD",
        "--skip-toolchain",
        "--by",
        "independent-reviewer",
    )
    assert reviewed.returncode == 1, reviewed.stdout + reviewed.stderr
    assert "REVIEW_BLOCKED" in reviewed.stdout
    assert "BLOCKING" in reviewed.stdout
    assert "src" in reviewed.stdout and "__pycache__" in reviewed.stdout


def test_out_of_scope_clutter_alone_only_warns_and_review_proceeds(tmp_path: Path) -> None:
    project = _toy_project(tmp_path, name="warn-only-project")
    out_of_scope_clutter = project / "other" / "__pycache__"
    out_of_scope_clutter.mkdir(parents=True)
    (out_of_scope_clutter / "mod.cpython-312.pyc").write_bytes(b"junk")

    _init_repo(project)
    run_id = _open_refactor_run(project, "refactor toy module", "tests/test_toy.py")

    reviewed = run_cli(
        project,
        "gate",
        "review",
        "--run",
        run_id,
        "--verdict",
        "approved",
        "--diff-base",
        "HEAD",
        "--by",
        "independent-reviewer",
    )
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    assert "WARN" in reviewed.stdout
    assert "REVIEW_BLOCKED" not in reviewed.stdout

    closed = run_cli(project, "gate", "close", "--run", run_id)
    assert closed.returncode == 0, closed.stdout + closed.stderr


def test_gate_review_requires_a_verdict(tmp_path: Path) -> None:
    project = _toy_project(tmp_path, name="no-verdict-project")
    _init_repo(project)
    run_id = _open_refactor_run(project, "refactor toy module", "tests/test_toy.py")

    reviewed = run_cli(project, "gate", "review", "--run", run_id)
    assert reviewed.returncode != 0
    assert "Missing option" in reviewed.stdout + reviewed.stderr or "--verdict" in (
        reviewed.stdout + reviewed.stderr
    )


def test_gate_review_by_the_same_actor_who_opened_the_run_is_refused_end_to_end(
    tmp_path: Path,
) -> None:
    """Real, end-to-end proof of the completion contract's core requirement:
    the same identity cannot both open a run and record its own review as an
    independent one - through the actual CLI, not just the Ledger's own unit
    tests."""
    project = _toy_project(tmp_path, name="self-verification-project")
    _init_repo(project)

    plan = project / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    opened = run_cli(
        project,
        "gate",
        "open",
        "refactor",
        "refactor toy module",
        "--scope",
        "tests/test_toy.py",
        "--plan",
        str(plan),
        "--by",
        "claude-session-a",
    )
    assert opened.returncode == 0, opened.stdout + opened.stderr
    run_id = opened.stdout.splitlines()[0].split()[1]

    approved = run_cli(project, "gate", "plan", "approve", "--by", "reviewer", "--run", run_id)
    assert approved.returncode == 0, approved.stdout + approved.stderr

    self_reviewed = run_cli(
        project,
        "gate",
        "review",
        "--run",
        run_id,
        "--verdict",
        "approved",
        "--diff-base",
        "HEAD",
        "--by",
        "claude-session-a",
    )
    assert self_reviewed.returncode == 1
    assert "SELF_VERIFICATION_REFUSED" in self_reviewed.stdout + self_reviewed.stderr

    independently_reviewed = run_cli(
        project,
        "gate",
        "review",
        "--run",
        run_id,
        "--verdict",
        "approved",
        "--diff-base",
        "HEAD",
        "--by",
        "claude-session-b",
    )
    assert independently_reviewed.returncode == 0, (
        independently_reviewed.stdout + independently_reviewed.stderr
    )
    assert "by=claude-session-b" in independently_reviewed.stdout

    closed = run_cli(project, "gate", "close", "--run", run_id)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert "COMPLETE" in closed.stdout
