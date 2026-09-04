"""The program counter.

A.W.I.N.O. had every capability and nothing that chose between them: the human,
or the persona from memory, picked a CLI command. That is why the ladder was
decorative and the graph had one caller. This module is the fix in its
simplest form - a node persisted on disk and a fixed edge table keyed on
observable outcomes. `awino step` reads the node, performs that node's single
action (an existing command), records the observation, follows the one edge,
and stops. There is no node whose action is "decide". Repeating `step` walks
the graph; a fresh process resumes from disk, so compaction cannot lose it.

Shape borrowed, runtimes not: ADK's `current_step` in state, Conductor's
routing on exit codes and human gates, sequential-thinking's explicit
"next needed". Grounding: chapters/7-patterns/3-orchestrator-pattern.md (phase
gating), chapters/6-harnesses/2-harness-stack.md.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class Node(StrEnum):
    IDLE = "idle"
    LOCATE = "locate"
    PROVISION = "provision"
    ROUTE = "route"
    QUESTION = "question"
    LADDER = "ladder"
    ANSWER = "answer"
    BUDGET = "budget"
    OPEN = "open"
    WORK = "work"
    EXECUTE = "execute"
    VERIFY = "verify"
    REVIEW = "review"
    GATES = "gates"
    CLOSE = "close"
    STOP = "stop"
    DONE = "done"


# (node, observation) -> next node. Observations are strings produced by the
# node's action - exit codes, verdicts, confidence levels - never a judgement.
EDGES: dict[tuple[Node, str], Node] = {
    (Node.IDLE, "start"): Node.LOCATE,
    (Node.LOCATE, "healthy"): Node.ROUTE,
    (Node.LOCATE, "missing"): Node.PROVISION,
    (Node.PROVISION, "provisioned"): Node.LOCATE,
    (Node.PROVISION, "declined"): Node.LOCATE,
    (Node.ROUTE, "high"): Node.LADDER,
    (Node.ROUTE, "ambiguous"): Node.QUESTION,
    (Node.ROUTE, "none"): Node.QUESTION,
    (Node.QUESTION, "answered"): Node.ROUTE,
    (Node.LADDER, "direct"): Node.ANSWER,
    (Node.LADDER, "floor"): Node.BUDGET,
    (Node.LADDER, "ralph"): Node.BUDGET,
    (Node.LADDER, "graph"): Node.BUDGET,
    (Node.LADDER, "delegate"): Node.BUDGET,
    (Node.BUDGET, "confirmed"): Node.OPEN,
    (Node.OPEN, "opened"): Node.WORK,
    (Node.OPEN, "plan-required"): Node.STOP,
    (Node.WORK, "floor-open"): Node.EXECUTE,
    (Node.EXECUTE, "executed"): Node.VERIFY,
    (Node.VERIFY, "verified"): Node.GATES,
    (Node.VERIFY, "verified-graph"): Node.REVIEW,
    (Node.VERIFY, "revise"): Node.WORK,
    (Node.VERIFY, "max-iterations"): Node.STOP,
    (Node.REVIEW, "ship"): Node.GATES,
    (Node.REVIEW, "revise"): Node.WORK,
    (Node.REVIEW, "blocked"): Node.STOP,
    (Node.GATES, "hold"): Node.CLOSE,
    (Node.GATES, "fail"): Node.WORK,
    (Node.GATES, "exhausted"): Node.STOP,
    (Node.CLOSE, "closed"): Node.DONE,
    (Node.STOP, "continue"): Node.WORK,
    (Node.STOP, "close"): Node.GATES,  # human fixed the blocker out of band; re-check gates
    (Node.STOP, "drop"): Node.DONE,
    (Node.DONE, "start"): Node.LOCATE,
    (Node.ANSWER, "start"): Node.LOCATE,
}

_FILE = "machine.json"


@dataclass
class Machine:
    node: Node = Node.IDLE
    loop: str = "direct"
    floor: int = 0
    run_id: str | None = None
    request: str = ""
    skill: str | None = None
    why: str = ""
    stance: str = "advisor"
    updated: str = ""
    history: list[dict[str, str]] = field(default_factory=list)


def advance(machine: Machine, observation: str) -> Machine:
    """Follow the one edge for (node, observation). Refuses rather than guesses."""
    key = (machine.node, observation)
    if key not in EDGES:
        raise ValueError(f"no edge from {machine.node} on {observation!r}")
    nxt = EDGES[key]
    machine.history.append({"from": machine.node.value, "obs": observation, "to": nxt.value})
    if nxt is Node.WORK and machine.node in (Node.VERIFY, Node.REVIEW, Node.GATES, Node.STOP):
        machine.floor += 1
    if nxt is Node.WORK and machine.node is Node.OPEN:
        machine.floor = 1
    machine.node = nxt
    machine.updated = datetime.now(UTC).isoformat()
    return machine


def load(state_root: Path) -> Machine:
    path = state_root / _FILE
    if not path.is_file():
        return Machine()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Machine()
    data["node"] = Node(data.get("node", "idle"))
    return Machine(**data)


def save(state_root: Path, machine: Machine) -> Path:
    state_root.mkdir(parents=True, exist_ok=True)
    path = state_root / _FILE
    payload = asdict(machine)
    payload["node"] = machine.node.value
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def reset(state_root: Path) -> None:
    (state_root / _FILE).unlink(missing_ok=True)
