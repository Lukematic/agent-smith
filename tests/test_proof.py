"""Tests for `awino proof export` / `awino proof verify`: FAIR proof packs.

Fixtures are synthetic state built in tmp dirs: a mission with measurable
success criteria, a hash-bound approved plan, a loop trail with verdicts
and test evidence, and decisions. Export must produce a complete pack
(mission, plan hash, ledger trail, test outputs, verdicts, brief, hash
index). Verification must pass from a clean directory with no access to
the original project, and must fail -- naming the file -- when a pack file
is tampered with.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import cli, proof, working_memory
from awino.enforce import Ledger, LoopEvent, TaskClass

CRITERIA = [
    "index rebuilds in under 10 minutes -> ./verify_time.sh",
    "zero downtime during cutover -> ./verify_downtime.sh",
]

LOOP_ID = "rpi-20260911-a1b2c3"


def _record(ledger: Ledger, kind: str, phase: str = "", detail: str = "") -> None:
    ledger.record_loop_event(
        LoopEvent(
            loop_id=LOOP_ID,
            loop_kind="rpi",
            phase=phase,
            kind=kind,
            at=datetime.now(UTC).isoformat(),
            detail=detail,
        )
    )


@pytest.fixture()
def proof_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """A project with mission, approved plan, verdicts, and test evidence."""
    monkeypatch.chdir(tmp_path)
    for var in ("AWINO_HOME", "SMITH_HOME", "AWINO_PROJECT", "SMITH_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    state_root = tmp_path / ".awino"
    (state_root / "loops").mkdir(parents=True)
    (state_root / "heilmeier.json").write_text(
        json.dumps(
            {
                "answers": {
                    "objective": "Rebuild the search index without downtime.",
                    "exams": "\n".join(CRITERIA),
                },
                "source": {},
            }
        ),
        encoding="utf-8",
    )
    ledger = Ledger(state_root)
    docs = tmp_path / "docs"
    docs.mkdir()
    plan_file = docs / "plan.md"
    plan_file.write_text("# Plan: search index rebuild\n", encoding="utf-8")
    run = ledger.open(
        TaskClass.CODE_CHANGE, "Rebuild the search index", plan_path=plan_file
    )
    decision = ledger.approve_plan(run.run_id, "human", "plan looks right")
    _record(ledger, "loop_started", "research", "task: Rebuild the search index")
    _record(
        ledger,
        "verify_passed",
        "implement",
        "verify_time.sh: index rebuilds in under 10 minutes",
    )
    _record(
        ledger,
        "artifact_validated",
        "research",
        "research artifact passed validation: docs/research.md",
    )
    _record(ledger, "loop_closed", "implement", "")
    _record(
        ledger,
        "outcome_verdict",
        "implement",
        f"verdict: yes; criteria_met: {CRITERIA[0]}; {CRITERIA[1]}; "
        "criteria_unmet: (none); criteria_unjudgeable: (none)",
    )
    decisions = working_memory.Decisions(state_root)
    decisions.record(
        decision="chose Incremental rollout for the index rebuild",
        why="It ships this week with zero downtime.",
        source="human",
        key=f"{LOOP_ID}:Q1",
    )
    return tmp_path, decision.plan_sha256


@pytest.fixture()
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    for var in ("AWINO_HOME", "SMITH_HOME", "AWINO_PROJECT", "SMITH_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    return CliRunner()


def _export(
    cli_runner: CliRunner, project: Path, out: Path
) -> None:
    result = cli_runner.invoke(cli.app, ["proof", "export", "--out", str(out)])
    assert result.exit_code == 0, result.output


# ── export ─────────────────────────────────────────────────────────────────


def test_export_creates_complete_pack(
    proof_project: tuple[Path, str], cli_runner: CliRunner, tmp_path: Path
) -> None:
    project, _ = proof_project
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    for name in (
        "README.md",
        "mission.json",
        "plan.json",
        "ledger.jsonl",
        "tests.json",
        "verdicts.json",
        "brief.md",
        "index.json",
    ):
        assert (pack / name).is_file(), f"pack missing {name}"


def test_export_pack_contents_trace_to_state(
    proof_project: tuple[Path, str], cli_runner: CliRunner, tmp_path: Path
) -> None:
    project, plan_sha = proof_project
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    mission = json.loads((pack / "mission.json").read_text(encoding="utf-8"))
    assert mission["objective"] == "Rebuild the search index without downtime."
    assert mission["success_criteria"] == CRITERIA
    plan = json.loads((pack / "plan.json").read_text(encoding="utf-8"))
    assert plan["approved_plan_sha256"] == plan_sha
    assert plan["approved_plan_sha256"] == hashlib.sha256(
        (project / "docs" / "plan.md").read_bytes()
    ).hexdigest()
    trail = [
        json.loads(line)
        for line in (pack / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [event["kind"] for event in trail] == [
        "loop_started",
        "verify_passed",
        "artifact_validated",
        "loop_closed",
        "outcome_verdict",
    ]
    tests = json.loads((pack / "tests.json").read_text(encoding="utf-8"))
    assert {item["kind"] for item in tests["evidence"]} == {
        "verify_passed",
        "artifact_validated",
    }
    assert all(item["passed"] for item in tests["evidence"])
    verdicts = json.loads((pack / "verdicts.json").read_text(encoding="utf-8"))
    assert len(verdicts["verdicts"]) == 1
    verdict = verdicts["verdicts"][0]
    assert verdict["loop_id"] == LOOP_ID
    assert verdict["verdict"] == "yes"
    assert verdict["criteria_met"] == CRITERIA
    index = json.loads((pack / "index.json").read_text(encoding="utf-8"))
    indexed = {item["path"] for item in index["artifacts"]}
    assert indexed == {
        "README.md",
        "mission.json",
        "plan.json",
        "ledger.jsonl",
        "tests.json",
        "verdicts.json",
        "brief.md",
    }
    for item in index["artifacts"]:
        assert hashlib.sha256(
            (pack / item["path"]).read_bytes()
        ).hexdigest() == item["sha256"]
    brief = (pack / "brief.md").read_text(encoding="utf-8")
    assert "Rebuild the search index without downtime." in brief
    assert LOOP_ID in brief


def test_export_without_approved_plan_is_honest(
    proof_project: tuple[Path, str], cli_runner: CliRunner, tmp_path: Path
) -> None:
    project, _ = proof_project
    for child in (project / ".awino" / "run").iterdir():
        if not child.is_dir():
            continue
        run_json = child / "run.json"
        data = json.loads(run_json.read_text(encoding="utf-8"))
        data["plan_decisions"] = []
        run_json.write_text(json.dumps(data), encoding="utf-8")
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    plan = json.loads((pack / "plan.json").read_text(encoding="utf-8"))
    assert plan["approved_plan_sha256"] is None
    assert "no approved plan" in plan["note"]
    # An honestly-empty plan still re-verifies.
    assert proof.verify_pack(pack) == []


# ── verify: the clean-room re-check ──────────────────────────────────────────


def test_verify_passes_in_clean_dir_with_no_project_access(
    proof_project: tuple[Path, str],
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = proof_project
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    # A clean directory: nothing of the project is reachable from here.
    clean = tmp_path / "clean"
    clean.mkdir()
    monkeypatch.chdir(clean)
    for var in ("AWINO_HOME", "SMITH_HOME", "AWINO_PROJECT", "SMITH_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    result = cli_runner.invoke(cli.app, ["proof", "verify", str(pack)])
    assert result.exit_code == 0, result.output
    assert "PROOF OK" in result.output


def test_verify_names_tampered_file(
    proof_project: tuple[Path, str],
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = proof_project
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    brief = pack / "brief.md"
    brief.write_text(brief.read_text(encoding="utf-8") + "\nforged claim\n")
    clean = tmp_path / "clean"
    clean.mkdir()
    monkeypatch.chdir(clean)
    result = cli_runner.invoke(cli.app, ["proof", "verify", str(pack)])
    assert result.exit_code != 0
    assert "brief.md" in result.output
    assert "PROOF INVALID" in result.output


def _resign(pack: Path) -> None:
    """Recompute index.json hashes after a deliberate pack mutation."""
    index_path = pack / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for item in index["artifacts"]:
        item["sha256"] = hashlib.sha256(
            (pack / item["path"]).read_bytes()
        ).hexdigest()
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def test_verify_rejects_verdict_for_unknown_loop(
    proof_project: tuple[Path, str], cli_runner: CliRunner, tmp_path: Path
) -> None:
    project, _ = proof_project
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    verdicts_path = pack / "verdicts.json"
    data = json.loads(verdicts_path.read_text(encoding="utf-8"))
    data["verdicts"][0]["loop_id"] = "rpi-20990101-deadbeef"
    verdicts_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _resign(pack)
    failures = proof.verify_pack(pack)
    assert failures, "verdict for an unknown loop must fail verification"
    assert any(
        "verdicts.json" in str(failure) and "deadbeef" in str(failure)
        for failure in failures
    ), [str(failure) for failure in failures]


def test_verify_rejects_trail_not_starting_with_loop_started(
    proof_project: tuple[Path, str], cli_runner: CliRunner, tmp_path: Path
) -> None:
    project, _ = proof_project
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    ledger_path = pack / "ledger.jsonl"
    lines = [
        line
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    lines.append(
        json.dumps(
            {
                "loop_id": "ghost-1",
                "loop_kind": "rpi",
                "phase": "implement",
                "kind": "loop_closed",
                "at": datetime.now(UTC).isoformat(),
                "detail": "",
            }
        )
    )
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _resign(pack)
    failures = proof.verify_pack(pack)
    assert any(
        "ledger.jsonl" in str(failure) and "ghost-1" in str(failure)
        for failure in failures
    ), [str(failure) for failure in failures]


def test_verify_rejects_brief_that_invents_facts(
    proof_project: tuple[Path, str], cli_runner: CliRunner, tmp_path: Path
) -> None:
    project, _ = proof_project
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    # The brief drops the objective: its claims no longer trace to the pack.
    mission = json.loads((pack / "mission.json").read_text(encoding="utf-8"))
    brief_path = pack / "brief.md"
    brief_path.write_text(
        brief_path.read_text(encoding="utf-8").replace(
            mission["objective"], "A completely different objective."
        ),
        encoding="utf-8",
    )
    _resign(pack)
    failures = proof.verify_pack(pack)
    assert any(
        "brief.md" in str(failure) and "objective" in str(failure)
        for failure in failures
    ), [str(failure) for failure in failures]


def test_verify_rejects_unlisted_extra_file(
    proof_project: tuple[Path, str], cli_runner: CliRunner, tmp_path: Path
) -> None:
    project, _ = proof_project
    pack = tmp_path / "pack"
    _export(cli_runner, project, pack)
    (pack / "extra.md").write_text("smuggled in after export\n", encoding="utf-8")
    failures = proof.verify_pack(pack)
    assert any(
        "extra.md" in str(failure) and "not listed" in str(failure)
        for failure in failures
    ), [str(failure) for failure in failures]
