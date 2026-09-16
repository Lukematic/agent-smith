"""`awino exam`: put A.W.I.N.O. through every capability it claims, live, and
record which ones actually fire.

Every feature this week was verified one at a time as it landed. Nothing
proves they all still work together in a fresh repo - the human's exact
complaint: "we have a lot of functions not working." The exam is the mission's
own final exam applied to the tool: a disposable project, each capability
exercised through the real CLI in a subprocess, one FIRES/SILENT line each,
written to the ledger as an artifact so the claim is evidence, not memory.

Each probe is a (name, argv, expected-substring) triple. Adding a capability
means adding a probe; a probe that goes SILENT is a regression, not an opinion.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from awino.paths import project_state_dir


@dataclass(frozen=True)
class Probe:
    name: str
    argv: tuple[str, ...]
    expect: str
    stdin: str = ""
    expected_codes: tuple[int, ...] = (0,)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    fired: bool
    evidence: str
    returncode: int = 0


def launcher_resolves(executable: str | None = None) -> bool:
    """True when the probe launcher is a real executable that can run awino.cli.

    This is the executable-command guard for the capability exam: a probe only
    counts when its command actually ran. Prose, a missing interpreter, or an
    unimportable CLI module can never produce a pass.
    """
    exe = executable or sys.executable
    p = Path(exe)
    if not p.is_file():
        return False
    if not os.access(p, os.X_OK) and sys.platform != "win32":
        return False
    return importlib.util.find_spec("awino.cli") is not None


def probe_fired(probe: Probe, returncode: int, output: str) -> bool:
    """A probe fires only when its command ran clean AND produced the evidence.

    Expected text from a crashed subprocess (nonzero exit) is not a pass:
    the text may be echoed in an error, a traceback, or a usage message.
    """
    return returncode in probe.expected_codes and probe.expect in output


def _exam_environment(project: Path) -> dict[str, str]:
    """Return a controlled subprocess environment for one disposable exam.

    The process must import the source being examined, but it must not inherit
    the caller's project/state overrides.  Otherwise a command launched from a
    project with ``AWINO_PROJECT`` set can appear to pass by reading that
    project's state instead of the fixture created by :func:`_fixture`.
    """
    preserved = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE")
    env = {key: os.environ[key] for key in preserved if key in os.environ}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["AWINO_PROJECT"] = str(project)
    env.pop("SMITH_" + "PROJECT", None)
    return env


def _fixture(root: Path) -> None:
    (root / ".git").mkdir(parents=True)
    (root / "README.md").write_text("# Exam Fixture\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='examfixture'\nversion='0.1'\ndependencies=['pytest']\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert 1 == 2\n", encoding="utf-8"
    )


PROBES: tuple[Probe, ...] = (
    Probe("startup.report", ("start",), "Route skill:"),
    Probe("provision.reports-missing", ("start",), "MISSING"),
    Probe("best.session-order", ("best",), "[mission-gap] skill=awino-discover"),
    Probe("heilmeier.asks-q1", ("mission", "--heilmeier"), "QUESTION  [objective]"),
    Probe("stance.detects", ("stance", "--for", "I think we should rewrite it"), "steel-man"),
    Probe(
        "elevator.routes",
        ("best", "pytest is failing with a ValueError in the loader"),
        "FLOOR  awino-debug",
    ),
    Probe("elevator.remembers", ("best",), "CARRYING"),
    Probe(
        "recall.lessons",
        (
            "best",
            "pytest is failing because the floor close verify command uses the wrong cwd path",
        ),
        "RECALL",
    ),
    Probe(
        "gate.opens",
        ("gate", "open", "bugfix", "exam fixture failing test", "--scope", "tests/test_a.py"),
        "class=bugfix",
    ),
    Probe(
        "verify.discovered",
        (
            "floor",
            "open",
            "pytest is failing with an assertion error in test_a",
            "--scope",
            "tests/test_a.py",
        ),
        "VERIFY  pytest",
    ),
    Probe(
        "floor.verifies-not-trusts",
        ("floor", "close"),
        "REVISE",
        expected_codes=(1,),
    ),
    Probe(
        "hook.routes",
        ("hook", "prompt"),
        "MATCHED awino-debug",
        stdin=json.dumps({"prompt": "pytest is failing with a ValueError"}),
    ),
    Probe("auto.reachable", ("auto", "--max-seeds", "1", "--dry-run"), "READY"),
    Probe("graph.reachable", ("gate", "graph", "--help"), "worker"),
    Probe("loop.reachable", ("gate", "loop", "--help"), "iterations"),
    Probe("skills.status", ("skills-status",), "DRIFTED"),
)


def _run(argv: tuple[str, ...], cwd: Path, stdin: str) -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "awino.cli", *argv],
        cwd=cwd,
        input=stdin or None,
        env=_exam_environment(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def run_exam(keep: bool = False) -> list[ProbeResult]:
    """Build a disposable repo and drive every probe through the real CLI."""
    root = Path(tempfile.mkdtemp(prefix="awino-exam-"))
    _fixture(root)
    results: list[ProbeResult] = []
    launcher_ok = launcher_resolves()
    try:
        for probe in PROBES:
            code, output = _run(probe.argv, root, probe.stdin)
            fired = launcher_ok and probe_fired(probe, code, output)
            line = next((ln for ln in output.splitlines() if probe.expect in ln), "")
            evidence = line.strip()[:120] or output.strip()[-120:]
            if not launcher_ok:
                evidence = "probe launcher is not an executable awino.cli"
            elif code not in probe.expected_codes:
                evidence = (
                    f"exit={code} (expected {probe.expected_codes}; expected text is not a pass "
                    "on an unexpected failure)"
                )
            results.append(ProbeResult(probe.name, fired, evidence, code))
        # skill-in-prompt: inspect the floor prompt the exam wrote
        prompts = list((project_state_dir(root) / "assignments").glob("*.md"))
        text = prompts[0].read_text(encoding="utf-8") if prompts else ""
        results.append(
            ProbeResult(
                "skill.in-worker-prompt",
                "The skill you were routed to" in text,
                prompts[0].name if prompts else "no prompt written",
            )
        )
        results.append(
            ProbeResult(
                "intent.persisted",
                (project_state_dir(root) / "intent.json").is_file(),
                "state/intent.json",
            )
        )
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
    return results


def render(results: list[ProbeResult]) -> list[str]:
    lines = [f"{'FIRES' if r.fired else 'SILENT':<7} {r.name:<28} {r.evidence}" for r in results]
    fired = sum(1 for r in results if r.fired)
    lines.append(f"EXAM  {fired}/{len(results)} capabilities fire")
    return lines
