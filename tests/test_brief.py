"""Tests for `awino brief`: the stakeholder narrative from existing state.

Fixtures are synthetic state built in tmp dirs: a mission with measurable
success criteria, decisions with whys and a named losing steel-man case,
outcome verdicts, and unchosen pair-planning approaches with effort
markers. The brief must surface all five sections with the right content,
name the losing case, and say plainly where information is missing instead
of inventing it. No test touches the real workspace.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import cli, loops, working_memory
from awino.cli import brief as brief_mod
from awino.enforce import Ledger, LoopEvent

CRITERIA = [
    "index rebuilds in under 10 minutes -> ./verify_time.sh",
    "zero downtime during cutover -> ./verify_downtime.sh",
    "old index format still readable -> ./verify_compat.sh",
]

LOOP_ID = "rpi-20260911-a1b2c3"

PAIRING_BRIEF = """# Pairing brief: search index rebuild

## Sub-problems
1. Build the new index without touching the live one.
2. Cut over reads atomically.

## Candidate approaches

### Incremental rollout
effort: two days
The default recommendation: ship the new index builder behind a flag.
trade-off: slower wall-clock, but zero downtime.
pro: reversible at any step.

### Full rewrite
effort: two weeks
Replace the indexer wholesale.
con: requires a maintenance window; trade-off: faster once done, but downtime.

## Questions
Q1: Which approach should we take?
"""

PLAN = """# Plan: search index rebuild

## Phases
research, pair-plan, plan, implement

## Scope
src/search/

## Tests
./verify_time.sh

## Rollback
Flip the flag back.

## Acceptance criteria
index rebuilds in under 10 minutes

## Decisions
- Q1 -> Incremental rollout because it ships this week with zero downtime; followed the default recommendation.
"""


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
def briefed_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project with mission, verdicts, decisions, and a pairing brief."""
    monkeypatch.chdir(tmp_path)
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
    _record(ledger, "loop_started", "research", "task: Rebuild the search index")
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
        "verdict: yes; "
        f"loop_id: {LOOP_ID}; loop_kind: rpi; seed: none; goal: rebuild; "
        f"seed_status: n/a; criteria_met: {CRITERIA[0]}; {CRITERIA[1]}; "
        f"criteria_unmet: {CRITERIA[2]}; criteria_unjudgeable: (none)",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "pairing.md").write_text(PAIRING_BRIEF, encoding="utf-8")
    (docs / "plan.md").write_text(PLAN, encoding="utf-8")
    (docs / "research.md").write_text("# research\n", encoding="utf-8")
    state = loops.LoopState(
        id=LOOP_ID,
        task="Rebuild the search index",
        topic="search",
        phase="done",
        pairing_artifact="docs/pairing.md",
        plan_artifact="docs/plan.md",
        pair_answers={"Q1": {"kind": "answer", "text": "Incremental rollout"}},
    )
    (state_root / "loops" / f"{LOOP_ID}.json").write_text(
        json.dumps(state.to_dict()), encoding="utf-8"
    )
    decisions = working_memory.Decisions(state_root)
    decisions.record(
        decision="chose Incremental rollout for the index rebuild",
        why="It ships this week with zero downtime; the flag makes it reversible.",
        source="pair-planning",
        key=f"{LOOP_ID}:Q1",
    )
    decisions.record(
        decision="keep the old index format readable",
        why="Rollback needs the old reader during the cutover window.",
        source="human",
    )
    return tmp_path


@pytest.fixture()
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.delenv("AWINO_HOME", raising=False)
    monkeypatch.delenv("SMITH_HOME", raising=False)
    monkeypatch.delenv("AWINO_PROJECT", raising=False)
    monkeypatch.delenv("SMITH_PROJECT", raising=False)
    return CliRunner()


def _brief_text(cli_runner: CliRunner) -> str:
    result = cli_runner.invoke(cli.app, ["brief"])
    assert result.exit_code == 0, result.output
    return result.output


# ── the five sections ────────────────────────────────────────────────────


def test_brief_renders_all_five_sections(
    briefed_project: Path, cli_runner: CliRunner
) -> None:
    output = _brief_text(cli_runner)
    for header in ("MISSION", "DELIVERABLES", "DECISIONS", "BEYOND THE HONDA", "GAPS"):
        assert header in output, f"missing section: {header}"


def test_brief_mission_judges_each_criterion_from_verdicts(
    briefed_project: Path, cli_runner: CliRunner
) -> None:
    output = _brief_text(cli_runner)
    assert "Objective: Rebuild the search index without downtime." in output
    assert f"[met] {CRITERIA[0]}" in output
    assert f"[met] {CRITERIA[1]}" in output
    assert f"[unmet] {CRITERIA[2]}" in output


def test_brief_deliverables_cite_ledger_proof(
    briefed_project: Path, cli_runner: CliRunner
) -> None:
    output = _brief_text(cli_runner)
    assert f"- Rebuild the search index (loop {LOOP_ID})" in output
    assert "outcome: accomplished" in output
    assert "proof: loops.jsonl: 4 events" in output
    assert "proof: validated artifact: docs/research.md" in output
    assert "proof: outcome verdict 'yes': 2 criteria met, 1 unmet" in output


def test_brief_decisions_carry_whys_and_name_the_losing_case(
    briefed_project: Path, cli_runner: CliRunner
) -> None:
    output = _brief_text(cli_runner)
    assert "chose Incremental rollout for the index rebuild" in output
    assert "why: It ships this week with zero downtime" in output
    # The steel-man: the losing candidate approach, named, with its effort.
    assert "the case against: 'Full rewrite'" in output
    assert "effort: two weeks" in output
    assert "requires a maintenance window" in output


def test_brief_beyond_honda_lists_unchosen_approach_with_effort(
    briefed_project: Path, cli_runner: CliRunner
) -> None:
    output = _brief_text(cli_runner)
    assert f"from pair-planning brief (loop {LOOP_ID}):" in output
    assert "- Full rewrite (effort: two weeks)" in output
    # The chosen approach is not "beyond the Honda".
    assert "- Incremental rollout (effort:" not in output


def test_brief_beyond_honda_labels_buddy_suggestions_by_source(
    briefed_project: Path, cli_runner: CliRunner
) -> None:
    output = _brief_text(cli_runner)
    assert "from buddy audit:" in output


# ── missing-data honesty ─────────────────────────────────────────────────


def test_brief_with_no_verdicts_says_so(
    tmp_path: Path, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".awino"
    state_root.mkdir()
    (state_root / "heilmeier.json").write_text(
        json.dumps(
            {
                "answers": {
                    "objective": "Rebuild the search index.",
                    "exams": CRITERIA[0],
                },
                "source": {},
            }
        ),
        encoding="utf-8",
    )
    output = _brief_text(cli_runner)
    assert "no outcome verdict recorded yet" in output
    assert f"[unjudgeable] {CRITERIA[0]}" in output


def test_brief_with_no_state_says_so_everywhere(
    tmp_path: Path, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    output = _brief_text(cli_runner)
    assert "no mission on file" in output
    assert "no success criteria on file" in output
    assert "nothing completed on file" in output
    assert "no decisions recorded" in output
    assert "nothing else proposed" in output
    # ... and the GAPS section collects every one of them.
    gaps = output.split("GAPS")[1]
    assert "no mission on file" in gaps
    assert "no completed work on file" in gaps


def test_brief_names_unrecorded_opposing_case_in_gaps(
    briefed_project: Path, cli_runner: CliRunner
) -> None:
    # D-0002 has no pairing brief: the brief must say so, not invent one.
    output = _brief_text(cli_runner)
    assert "the opposing case was not recorded" in output


# ── pure helpers ─────────────────────────────────────────────────────────


def test_verdict_criteria_parsing() -> None:
    parsed = brief_mod._verdict_criteria(
        "verdict: partial; criteria_met: a; b; criteria_unmet: c; "
        "criteria_unjudgeable: (none)"
    )
    assert parsed == {"met": ["a", "b"], "unmet": ["c"], "unjudgeable": []}


def test_verdict_criteria_ignores_no_criteria_note() -> None:
    parsed = brief_mod._verdict_criteria(
        "verdict: yes; criteria_unjudgeable: no success criteria on file"
    )
    assert parsed == {"met": [], "unmet": [], "unjudgeable": []}


def test_judge_criteria_latest_verdict_wins() -> None:
    def _event(detail: str, at: str) -> LoopEvent:
        return LoopEvent(
            loop_id="rpi-1",
            loop_kind="rpi",
            phase="",
            kind="outcome_verdict",
            at=at,
            detail=detail,
        )

    events = [
        _event("verdict: no; criteria_unmet: fast enough", "2026-09-10T00:00:00+00:00"),
        _event("verdict: yes; criteria_met: fast enough", "2026-09-11T00:00:00+00:00"),
    ]
    (judgment,) = brief_mod._judge_criteria(["fast enough"], events)
    assert judgment.status == "met"
    assert judgment.loop_id == "rpi-1"


def test_judge_criteria_unmentioned_is_unjudgeable() -> None:
    (judgment,) = brief_mod._judge_criteria(["never judged"], [])
    assert judgment.status == "unjudgeable"
    assert judgment.judged_at is None
