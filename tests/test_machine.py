"""S1: the program counter. A.W.I.N.O. has tools but nothing that decides which
runs; this is the fixed edge table plus a persisted node. Every (node,
observation) pair has exactly one successor; `step` reads, acts once, records,
advances, stops."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smith.machine import (
    EDGES,
    Machine,
    Node,
    advance,
    load,
    save,
)


class TestEdgeTableIsTotal:
    def test_every_edge_targets_a_real_node(self) -> None:
        for (src, _obs), dst in EDGES.items():
            assert isinstance(src, Node) and isinstance(dst, Node)

    def test_every_non_terminal_node_has_at_least_one_edge(self) -> None:
        sources = {src for (src, _obs) in EDGES}
        for node in Node:
            if node in (Node.DONE, Node.ANSWER):
                continue
            assert node in sources, f"{node} has no outgoing edge"

    def test_the_diagram_edges_are_present(self) -> None:
        assert EDGES[(Node.LOCATE, "healthy")] is Node.ROUTE
        assert EDGES[(Node.LOCATE, "missing")] is Node.PROVISION
        assert EDGES[(Node.ROUTE, "ambiguous")] is Node.QUESTION
        assert EDGES[(Node.QUESTION, "answered")] is Node.ROUTE
        assert EDGES[(Node.ROUTE, "high")] is Node.LADDER
        assert EDGES[(Node.LADDER, "direct")] is Node.ANSWER
        assert EDGES[(Node.LADDER, "floor")] is Node.BUDGET
        assert EDGES[(Node.BUDGET, "confirmed")] is Node.OPEN
        assert EDGES[(Node.VERIFY, "revise")] is Node.WORK
        assert EDGES[(Node.VERIFY, "max-iterations")] is Node.STOP
        assert EDGES[(Node.VERIFY, "verified-graph")] is Node.REVIEW
        assert EDGES[(Node.REVIEW, "ship")] is Node.GATES
        assert EDGES[(Node.REVIEW, "revise")] is Node.WORK
        assert EDGES[(Node.GATES, "hold")] is Node.CLOSE
        assert EDGES[(Node.CLOSE, "closed")] is Node.DONE
        assert EDGES[(Node.STOP, "continue")] is Node.WORK

    def test_unknown_observation_is_refused_not_guessed(self) -> None:
        m = Machine(node=Node.LOCATE)
        with pytest.raises(ValueError, match="no edge"):
            advance(m, "wat")


class TestPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        m = Machine(node=Node.BUDGET, loop="ralph", floor=2, run_id="r1", request="fix it")
        save(tmp_path, m)
        back = load(tmp_path)
        assert back == m

    def test_missing_file_is_idle(self, tmp_path: Path) -> None:
        assert load(tmp_path).node is Node.IDLE

    def test_advance_records_history(self, tmp_path: Path) -> None:
        m = Machine(node=Node.LOCATE)
        m = advance(m, "healthy")
        m = advance(m, "high")
        assert m.node is Node.LADDER
        assert [h["from"] for h in m.history] == ["locate", "route"]
        save(tmp_path, m)
        data = json.loads((tmp_path / "machine.json").read_text(encoding="utf-8"))
        assert data["node"] == "ladder"


class TestWorkLoopCounter:
    def test_revise_increments_floor(self) -> None:
        m = Machine(node=Node.VERIFY, floor=1)
        m = advance(m, "revise")
        assert m.node is Node.WORK and m.floor == 2

    def test_no_silent_self_loop(self) -> None:
        for (src, _obs), dst in EDGES.items():
            assert src is not dst, f"{src} loops to itself"


class TestLadderChoose:
    def test_six_branches_are_deterministic(self) -> None:
        from smith.ladder import choose

        assert choose("what is a harness", "awino-consult", None, []).loop == "direct"
        assert (
            choose("the agent keeps ignoring instructions", "awino-triage", "pytest", ["a.py"]).loop
            == "graph"
        )
        assert (
            choose(
                "split into modules", "awino-rpi", "pytest -q", ["a/x.py", "b/y.py", "c/z.py"]
            ).loop
            == "delegate"
        )
        assert choose("fix the loader", "awino-debug", None, ["a.py"]).loop == "ralph"
        assert (
            choose(
                "fix the loader", "awino-debug", 'python -c "raise SystemExit(0)"', ["a.py"]
            ).loop
            == "ralph"
        )
        assert choose("fix the loader", "awino-debug", "pytest -q", ["a.py"]).loop == "floor"
        a = choose("fix the loader", "awino-debug", "pytest -q", ["a.py"])
        assert a == choose("fix the loader", "awino-debug", "pytest -q", ["a.py"]) and a.why
