"""42d4: the loop declaration stops being decorative. It is a validated Run
field written at gate open and read back by status; the rung verdict is
recorded as an artifact so a misaligned rung is auditable, never a block."""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.enforce import LOOPS, Ledger, LedgerError, TaskClass


def test_loop_is_recorded_on_the_run(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    run = ledger.open(TaskClass.CODE_CHANGE, "x", loop="rpi")
    assert ledger.load(run.run_id).loop == "rpi"


def test_loop_defaults_to_direct(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    run = ledger.open(TaskClass.QUESTION, "x")
    assert run.loop == "direct"


def test_unknown_loop_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    with pytest.raises(LedgerError, match="loop"):
        ledger.open(TaskClass.QUESTION, "x", loop="yolo")


def test_the_five_loops_are_the_ladders_ones() -> None:
    assert LOOPS == ("direct", "floor", "ralph", "graph", "rpi", "delegate")


def test_rung_verdict_is_recorded_as_artifact_not_a_block(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    # execution-flavoured objective that the naive misaligned check would flag
    run = ledger.open(TaskClass.BUGFIX, "fix a failing test in the core module", loop="direct")
    art = ledger.latest_artifact(run.run_id, "rung-verdict")
    assert art is not None
    assert "actual" in art.payload and "stated" in art.payload
    # and the run opened regardless: advisory, never blocking
    assert ledger.load(run.run_id).run_id == run.run_id


def test_legacy_runs_without_loop_field_still_load(tmp_path: Path) -> None:
    import json

    ledger = Ledger(tmp_path)
    run = ledger.open(TaskClass.QUESTION, "legacy")
    meta = ledger.run_dir(run.run_id) / "run.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data.pop("loop", None)
    meta.write_text(json.dumps(data), encoding="utf-8")
    assert ledger.load(run.run_id).loop == "direct"
