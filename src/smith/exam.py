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

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Probe:
    name: str
    argv: tuple[str, ...]
    expect: str
    stdin: str = ""


@dataclass(frozen=True)
class ProbeResult:
    name: str
    fired: bool
    evidence: str


def _fixture(root: Path) -> None:
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname='examfixture'\nversion='0.1'\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
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
    Probe("floor.verifies-not-trusts", ("floor", "close"), "REVISE"),
    Probe(
        "hook.routes",
        ("hook", "prompt"),
        "MATCHED awino-debug",
        stdin=json.dumps({"prompt": "pytest is failing with a ValueError"}),
    ),
    Probe("auto.reachable", ("auto", "--max-seeds", "1", "--dry-run"), "READY"),
    Probe("graph.reachable", ("gate", "graph", "--help"), "worker"),
    Probe("loop.reachable", ("gate", "loop", "--help"), "iterations"),
    Probe("skills.installed", ("skills-status",), "CURRENT"),
)


def _run(argv: tuple[str, ...], cwd: Path, stdin: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "smith.cli", *argv],
        cwd=cwd,
        input=stdin or None,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )
    return (completed.stdout or "") + (completed.stderr or "")


def run_exam(keep: bool = False) -> list[ProbeResult]:
    """Build a disposable repo and drive every probe through the real CLI."""
    root = Path(tempfile.mkdtemp(prefix="awino-exam-"))
    _fixture(root)
    results: list[ProbeResult] = []
    try:
        for probe in PROBES:
            output = _run(probe.argv, root, probe.stdin)
            fired = probe.expect in output
            line = next((ln for ln in output.splitlines() if probe.expect in ln), "")
            results.append(
                ProbeResult(probe.name, fired, line.strip()[:140] or output.strip()[-140:])
            )
        # skill-in-prompt: inspect the floor prompt the exam wrote
        prompts = list((root / ".smith" / "assignments").glob("*.md"))
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
                "intent.persisted", (root / ".smith" / "intent.json").is_file(), "state/intent.json"
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
