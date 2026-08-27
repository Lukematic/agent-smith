from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from smith.debugging import (
    ArchitectureAssessment,
    DebugPhase,
    DebugSession,
    FailureSignature,
)
from smith.enforce import Ledger, TaskClass


def session(tmp_path: Path) -> tuple[Ledger, DebugSession]:
    ledger = Ledger(tmp_path / ".smith")
    run = ledger.open(TaskClass.BUGFIX, "fix deterministic failure", file_scope=["src/app.py"])
    return ledger, DebugSession.begin(ledger, run.run_id, "pytest fails", "agent")


def test_failure_signatures_normalize_volatile_details() -> None:
    first = FailureSignature.normalize(
        "FAILED tests/test_app.py::test_value - ValueError at C:\\tmp\\app.py:41 id=12345"
    )
    second = FailureSignature.normalize(
        "FAILED tests/test_app.py::test_value - ValueError at C:\\work\\app.py:99 id=98765"
    )

    assert first == second


def test_three_distinct_failed_attempts_make_architecture_questionable(tmp_path: Path) -> None:
    _, debug = session(tmp_path)
    debug.add_evidence("reproduction", "same failure", "agent")
    debug.add_hypothesis("parser state leaks", "agent")
    debug.authorize_fix("reviewer")

    failures = (
        ("reset parser", "ValueError in parser"),
        ("isolate cache", "KeyError in cache"),
        ("replace adapter", "TypeError in adapter"),
    )
    for approach, output in failures:
        debug.record_attempt(approach, output, succeeded=False, actor="agent")

    assert debug.assessment is ArchitectureAssessment.ARCHITECTURE_QUESTIONABLE


def test_two_distinct_attempts_do_not_escalate(tmp_path: Path) -> None:
    _, debug = session(tmp_path)
    debug.add_evidence("reproduction", "same failure", "agent")
    debug.add_hypothesis("parser state leaks", "agent")
    debug.authorize_fix("reviewer")
    debug.record_attempt("reset parser", "ValueError in parser", succeeded=False, actor="agent")
    debug.record_attempt("isolate cache", "KeyError in cache", succeeded=False, actor="agent")

    assert debug.assessment is ArchitectureAssessment.LOCAL_FIX


def test_successful_third_attempt_does_not_escalate(tmp_path: Path) -> None:
    _, debug = session(tmp_path)
    debug.add_evidence("reproduction", "same failure", "agent")
    debug.add_hypothesis("parser state leaks", "agent")
    debug.authorize_fix("reviewer")
    debug.record_attempt("reset parser", "ValueError in parser", succeeded=False, actor="agent")
    debug.record_attempt("isolate cache", "KeyError in cache", succeeded=False, actor="agent")
    debug.record_attempt("replace adapter", "tests pass", succeeded=True, actor="agent")

    assert debug.assessment is ArchitectureAssessment.LOCAL_FIX


def test_same_signature_failures_remain_local_three_strikes_case(tmp_path: Path) -> None:
    _, debug = session(tmp_path)
    debug.add_evidence("reproduction", "same failure", "agent")
    debug.add_hypothesis("parser state leaks", "agent")
    debug.authorize_fix("reviewer")
    for approach in ("reset parser", "isolate cache", "replace adapter"):
        debug.record_attempt(approach, "same failure", succeeded=False, actor="agent")

    assert debug.assessment is ArchitectureAssessment.LOCAL_FIX


def test_exact_four_phase_lifecycle_is_persisted_as_run_artifacts(tmp_path: Path) -> None:
    ledger, debug = session(tmp_path)
    assert list(DebugPhase) == [
        DebugPhase.REPRODUCE,
        DebugPhase.DIAGNOSE,
        DebugPhase.FIX,
        DebugPhase.VERIFY,
    ]
    debug.add_evidence("trace", "ValueError", "agent")
    assert debug.phase is DebugPhase.DIAGNOSE
    debug.add_hypothesis("bad normalization", "agent")
    debug.authorize_fix("reviewer")
    assert debug.phase is DebugPhase.FIX
    debug.record_attempt("normalize paths", "tests pass", succeeded=True, actor="agent")
    assert debug.phase is DebugPhase.VERIFY
    debug.verify("pytest", "all passed", succeeded=True, actor="agent")

    assert debug.phase is DebugPhase.VERIFY
    assert {item.kind for item in ledger.artifacts(debug.run_id)} >= {
        "debug.begin",
        "debug.evidence",
        "debug.hypothesis",
        "debug.authorization",
        "debug.attempt",
        "debug.verification",
    }


def run_cli(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SMITH_PROJECT"] = str(project)
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_debug_subprocess_lifecycle_and_gate_closure(tmp_path: Path) -> None:
    opened = run_cli(tmp_path, "debug", "begin", "failing test", "--scope", "src/app.py")
    assert opened.returncode == 0, opened.stdout + opened.stderr
    assert "DEBUG_BEGIN" in opened.stdout

    assert run_cli(tmp_path, "debug", "evidence", "trace", "ValueError").returncode == 0
    assert run_cli(tmp_path, "debug", "hypothesize", "bad normalization").returncode == 0
    authorized = run_cli(tmp_path, "debug", "authorize-fix", "--by", "reviewer")
    assert authorized.returncode == 0, authorized.stdout + authorized.stderr
    assert (
        run_cli(
            tmp_path, "debug", "attempt", "normalize paths", "same failure", "--failed"
        ).returncode
        == 0
    )
    assert (
        run_cli(
            tmp_path, "debug", "attempt", "preserve node", "tests pass", "--succeeded"
        ).returncode
        == 0
    )
    verified = run_cli(
        tmp_path,
        "debug",
        "verify",
        "--cmd",
        f'"{sys.executable}" -c "print(\'all passed\')"',
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "DEBUG_VERIFIED" in verified.stdout

    passing = f'"{sys.executable}" -c "print(\'gate passed\')"'
    for gate in ("researched", "linted"):
        recorded = run_cli(tmp_path, "gate", "record", gate, "--cmd", passing)
        assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    for gate in ("tests_not_weakened", "lesson_recorded"):
        recorded = run_cli(tmp_path, "gate", "record", gate, "--attest", "reviewed")
        assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    closed = run_cli(tmp_path, "gate", "close")
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert "COMPLETE" in closed.stdout


def test_authorization_requires_evidence_and_hypothesis(tmp_path: Path) -> None:
    _, debug = session(tmp_path)
    with pytest.raises(ValueError, match="evidence"):
        debug.authorize_fix("reviewer")
    debug.add_evidence("trace", "ValueError", "agent")
    with pytest.raises(ValueError, match="hypothesis"):
        debug.authorize_fix("reviewer")
