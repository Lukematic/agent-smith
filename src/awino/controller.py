"""One plan-bound durable controller.

Phase 2 of the recovery: the loop controllers (``awino.loops``) and the
machine controller (``awino.machine`` / ``awino.stepper``, driven by
``awino best`` / ``awino step``) share one durable plan controller instead of
each keeping their own ad-hoc state. The distinct entry points stay as
explicit adapters -- the CLI/API surfaces do not merge, and no parallel
controller framework is introduced: the existing controllers are retained
and evolve through this shared service.

Durability rules (the whole point):

- Every controller event is journaled BEFORE state is mutated (write-ahead).
  A crash between journal and state is repaired on load by re-applying the
  journaled events the plan file does not yet reflect.
- Every event carries an id: submitting the same event twice applies once.
  The recorded outcome is replayed, never recomputed.
- Conflicting concurrent edits carry the revision the caller saw
  (``expected_revision``). A mismatch refuses with ``PlanConflict`` rather
  than silently merging; identical resubmissions converge on the one
  recorded outcome.
- Status is derived from stored facts (plan file, event journal, receipts),
  never from conversational memory: a fresh controller on the same state
  dir reports the same pending work, approval state, and budget.
- Every consequential action asks first: ``require_approval`` records the
  ask and refuses until a human grants it, with provenance (by/at/what).
- An exhausted budget is never success: charging past a ceiling refuses.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from awino.enforce import MAX_ATTEMPTS
from awino.knowledge import (
    DEFAULT_BUDGET as KNOWLEDGE_DEFAULT_BUDGET,
)
from awino.knowledge import (
    KnowledgeReceiptRequired,
    KnowledgeStore,
)
from awino.knowledge import (
    knowledge_receipt as _stored_knowledge_receipt,
)
from awino.knowledge import (
    record_knowledge_receipt as _record_knowledge_receipt,
)
from awino.knowledge import (
    require_knowledge_receipt as _require_knowledge_receipt,
)

PLAN_DIRNAME = "plans"
_JOURNAL_NAME = "events.jsonl"
_PLAN_NAME = "plan.json"
_LOCK_NAME = ".lock"
_LOCK_TIMEOUT_S = 10.0
_STALE_LOCK_S = 60.0
# The plan state's host_activity list is a bounded quick view; the journal
# is the unbounded record.
_HOST_ACTIVITY_CAP = 500


# ── errors ────────────────────────────────────────────────────────────────


class PlanError(RuntimeError):
    """Base for controller failures."""


class PlanNotFound(PlanError):
    """No plan with this id in the state dir."""


class PlanExists(PlanError):
    """A plan with this id already exists; load it instead."""


class PlanConflict(PlanError):
    """A concurrent edit moved the plan: the caller saw a stale revision."""


class ApprovalRequired(PlanError):
    """A consequential action was attempted without a recorded approval."""


class PlanBudgetExhausted(PlanError):
    """Charging this budget would pass its ceiling. Refused, not worked around."""


class PlanNotClosable(PlanError):
    """Closure refused: pending work, pending approvals, or no plan approval."""


# Re-exported so adapters have one import surface.
__all__ = [
    "ApprovalRequired",
    "ControllerEvent",
    "KnowledgeReceiptRequired",
    "PlanAdapter",
    "PlanBudgetExhausted",
    "PlanConflict",
    "PlanController",
    "PlanError",
    "PlanExists",
    "PlanNotClosable",
    "PlanNotFound",
    "PlanState",
    "answer_from_knowledge",
    "apply_action",
    "charge_budget",
    "close_plan",
    "consult_knowledge",
    "default_budgets",
    "for_loop",
    "for_machine",
    "for_plan",
    "grant_approval",
    "preflight",
    "queue_action",
    "record_review",
    "request_approval",
    "require_approval",
]


# ── state ─────────────────────────────────────────────────────────────────


@dataclass
class PlanState:
    plan_id: str
    plan_revision: int = 0
    scope: list[str] = field(default_factory=list)
    approval_state: str = "pending"  # pending | approved | invalidated
    approvals: list[dict[str, Any]] = field(default_factory=list)
    budgets: dict[str, int] = field(default_factory=dict)
    budget_used: dict[str, int] = field(default_factory=dict)
    pending_action_ids: list[str] = field(default_factory=list)
    verifier: str | None = None
    pending_approvals: list[dict[str, Any]] = field(default_factory=list)
    last_review: dict[str, Any] | None = None
    status: str = "open"  # open | closed
    contracts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Append-only trail of host-boundary activations (session start, user
    # turn, tool result) recorded by the per-host adapters in
    # ``awino.hosts``. Capped: the journal is the full record; this is the
    # quick "who touched the plan and when" view.
    host_activity: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ControllerEvent:
    """One normalized controller event.

    ``plan_revision`` is the revision AFTER this event is applied, so the
    journal alone can rebuild the plan (crash recovery) and a reader can
    tell exactly which revision each event produced.
    """

    event_id: str
    kind: str
    at: str
    plan_revision: int
    payload: dict[str, Any]


def default_budgets() -> dict[str, int]:
    """Ceilings for a fresh plan: floors and subprocesses mirror the
    machine's trip budget; knowledge files mirror the knowledge default."""
    return {
        "floors": MAX_ATTEMPTS,
        "subprocesses": MAX_ATTEMPTS * 2,
        "knowledge_files": KNOWLEDGE_DEFAULT_BUDGET,
    }


# ── disk helpers ──────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _plan_dir(state_root: Path, plan_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", plan_id)
    return state_root / PLAN_DIRNAME / safe


@contextlib.contextmanager
def _locked(plan_dir: Path):
    """Cross-platform atomic lock (O_CREAT|O_EXCL works on POSIX and
    Windows). Stale locks (older than _STALE_LOCK_S) are cleared: a crashed
    holder must not wedge the plan forever."""
    lock = plan_dir / _LOCK_NAME
    plan_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0.0
            if age > _STALE_LOCK_S:
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise PlanError(f"could not acquire plan lock at {lock}") from None
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


# ── event application (pure: state + event -> state, outcome) ─────────────


def _apply(state: dict[str, Any], event: ControllerEvent) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one journaled event to a plan-state dict. Pure function: the
    same event applied twice to the same state gives the same result, which
    is what makes crash recovery and replay safe."""
    kind = event.kind
    payload = event.payload
    state = dict(state)
    outcome: dict[str, Any] = {"event_id": event.event_id, "kind": kind}

    if kind == "plan_created":
        pass
    elif kind == "scope_set":
        state["scope"] = list(payload["scope"])
        outcome["scope"] = state["scope"]
    elif kind == "approval_requested":
        pending = [dict(a) for a in state["pending_approvals"]]
        if not any(a["action_id"] == payload["action_id"] for a in pending):
            pending.append(
                {
                    "action_id": payload["action_id"],
                    "action": payload["action"],
                    "detail": payload.get("detail", ""),
                    "requested_at": event.at,
                    "requested_by": payload.get("by", "human"),
                }
            )
        state["pending_approvals"] = pending
        outcome["action_id"] = payload["action_id"]
    elif kind == "approval_granted":
        action_id = payload["action_id"]
        state["pending_approvals"] = [
            a for a in state["pending_approvals"] if a["action_id"] != action_id
        ]
        approvals = [dict(a) for a in state["approvals"]]
        if not any(a["action_id"] == action_id for a in approvals):
            approvals.append(
                {
                    "action_id": action_id,
                    "by": payload["by"],
                    "at": event.at,
                    "plan_level": bool(payload.get("plan_level")),
                }
            )
        state["approvals"] = approvals
        if payload.get("plan_level"):
            state["approval_state"] = "approved"
        outcome.update({"action_id": action_id, "approval_state": state["approval_state"]})
    elif kind == "approval_invalidated":
        state["approval_state"] = "invalidated"
        outcome["approval_state"] = "invalidated"
    elif kind == "action_queued":
        pending = list(state["pending_action_ids"])
        if payload["action_id"] not in pending:
            pending.append(payload["action_id"])
        state["pending_action_ids"] = pending
        outcome["pending"] = pending
    elif kind == "action_applied":
        action_id = payload["action_id"]
        state["pending_action_ids"] = [a for a in state["pending_action_ids"] if a != action_id]
        outcome.update({"action_id": action_id, "result": payload.get("result")})
    elif kind == "budget_charged":
        name, amount = payload["budget"], int(payload["amount"])
        used = {k: int(v) for k, v in state["budget_used"].items()}
        used[name] = used.get(name, 0) + amount
        state["budget_used"] = used
        outcome.update({"budget": name, "used": used[name], "ceiling": state["budgets"].get(name)})
    elif kind == "review_recorded":
        state["last_review"] = {
            "verdict": payload["verdict"],
            "detail": payload.get("detail", ""),
            "by": payload.get("by", "human"),
            "at": event.at,
        }
        outcome["verdict"] = payload["verdict"]
    elif kind == "knowledge_receipt":
        outcome["question_hash"] = payload["question_hash"]
    elif kind == "contract_saved":
        contracts = {k: dict(v) for k, v in state.get("contracts", {}).items()}
        entry = contracts.get(payload["contract_id"], {})
        entry.update(
            {
                "contract_revision": payload["contract_revision"],
                "state": payload["state"],
                "brief_type": payload.get("brief_type"),
                "role": payload.get("role"),
                "plan_revision_seen": payload.get("plan_revision_seen"),
            }
        )
        contracts[payload["contract_id"]] = entry
        state["contracts"] = contracts
        outcome["contract_revision"] = payload["contract_revision"]
    elif kind == "contract_approved":
        contracts = {k: dict(v) for k, v in state.get("contracts", {}).items()}
        entry = contracts.setdefault(payload["contract_id"], {})
        entry.update(
            {
                "contract_revision": payload["contract_revision"],
                "state": "approved",
                "approved_by": payload.get("by"),
                "revision_hash": payload.get("revision_hash"),
            }
        )
        contracts[payload["contract_id"]] = entry
        state["contracts"] = contracts
        outcome["state"] = "approved"
    elif kind == "contract_invalidated":
        contracts = {k: dict(v) for k, v in state.get("contracts", {}).items()}
        entry = contracts.setdefault(payload["contract_id"], {})
        entry.update(
            {
                "contract_revision": payload["contract_revision"],
                "state": "invalidated",
                "invalidation_reason": payload.get("reason"),
            }
        )
        contracts[payload["contract_id"]] = entry
        state["contracts"] = contracts
        outcome["state"] = "invalidated"
    elif kind == "contract_superseded":
        contracts = {k: dict(v) for k, v in state.get("contracts", {}).items()}
        entry = contracts.setdefault(payload["contract_id"], {})
        entry.update(
            {
                "contract_revision": payload["contract_revision"],
                "state": "superseded",
            }
        )
        contracts[payload["contract_id"]] = entry
        state["contracts"] = contracts
        outcome["state"] = "superseded"
    elif kind == "plan_closed":
        state["status"] = "closed"
        outcome["status"] = "closed"
    elif kind in ("host_session_started", "host_user_turn", "host_tool_result"):
        # Host-boundary activation from one of the awino.hosts adapters.
        # Records who touched the plan and when; never mutates plan
        # substance (scope, approvals, budgets). The journal holds the full
        # payload; host_activity is the bounded quick view.
        boundary = {
            "host_session_started": "session_start",
            "host_user_turn": "user_turn",
            "host_tool_result": "tool_result",
        }[kind]
        activity = list(state.get("host_activity", []))
        entry = {
            "at": event.at,
            "host": payload["host"],
            "boundary": boundary,
            "session_id": payload.get("session_id", "unknown"),
            "evidence": payload.get("evidence", "unverified"),
            "detail": payload.get("detail", ""),
        }
        activity.append(entry)
        state["host_activity"] = activity[-_HOST_ACTIVITY_CAP:]
        outcome["recorded"] = entry
    else:
        raise PlanError(f"unknown controller event kind: {kind!r}")

    state["plan_revision"] = event.plan_revision
    state["updated_at"] = event.at
    outcome["plan_revision"] = event.plan_revision
    return state, outcome


# ── the controller ────────────────────────────────────────────────────────


class PlanController:
    """Durable, plan-bound controller. All mutation goes through
    ``submit_event``: journaled first, applied once, revision-checked."""

    def __init__(self, state_root: Path, plan_id: str, state: PlanState) -> None:
        self.state_root = state_root
        self.plan_id = plan_id
        self._dir = _plan_dir(state_root, plan_id)
        self._state = state

    # ── construction ──

    @classmethod
    def create(
        cls,
        state_root: Path,
        plan_id: str,
        *,
        scope: list[str] | None = None,
        budgets: dict[str, int] | None = None,
        verifier: str | None = None,
    ) -> PlanController:
        plan_dir = _plan_dir(state_root, plan_id)
        if (plan_dir / _PLAN_NAME).is_file():
            raise PlanExists(f"plan {plan_id!r} already exists; load it")
        merged = default_budgets()
        merged.update(budgets or {})
        state = PlanState(
            plan_id=plan_id,
            plan_revision=0,
            scope=list(scope or []),
            budgets=merged,
            budget_used=dict.fromkeys(merged, 0),
            verifier=verifier,
            created_at=_now(),
            updated_at=_now(),
        )
        with _locked(plan_dir):
            _atomic_write_json(plan_dir / _PLAN_NAME, asdict(state))
            journal = plan_dir / _JOURNAL_NAME
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text("", encoding="utf-8")
        controller = cls(state_root, plan_id, state)
        controller.submit_event(
            event_id=f"plan-created-{plan_id}",
            kind="plan_created",
            payload={"scope": state.scope, "budgets": state.budgets, "verifier": verifier},
        )
        return controller

    @classmethod
    def load(cls, state_root: Path, plan_id: str) -> PlanController:
        plan_dir = _plan_dir(state_root, plan_id)
        plan_file = plan_dir / _PLAN_NAME
        if not plan_file.is_file():
            raise PlanNotFound(f"no plan {plan_id!r} in {state_root}")
        data = json.loads(plan_file.read_text(encoding="utf-8"))
        state = PlanState(**data)
        controller = cls(state_root, plan_id, state)
        with _locked(controller._dir):
            controller._recover()
        return controller

    # ── journal ──

    def _journal_path(self) -> Path:
        return self._dir / _JOURNAL_NAME

    def _read_journal(self) -> list[tuple[ControllerEvent, dict[str, Any]]]:
        """Every journaled (event, recorded outcome) pair, in order."""
        path = self._journal_path()
        if not path.is_file():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            event = ControllerEvent(
                event_id=raw["event_id"],
                kind=raw["kind"],
                at=raw["at"],
                plan_revision=raw["plan_revision"],
                payload=raw["payload"],
            )
            entries.append((event, raw["outcome"]))
        return entries

    def _recover(self) -> None:
        """Crash recovery: re-apply journaled events the plan file does not
        yet reflect. _apply is pure, so re-applying is safe and never
        double-counts. Assumes the plan lock is held."""
        plan_file = self._dir / _PLAN_NAME
        if plan_file.is_file():
            # The disk is canonical: another process may have moved the plan
            # since this instance loaded it. Sync before doing anything.
            data = json.loads(plan_file.read_text(encoding="utf-8"))
            self._state = PlanState(**data)
        entries = self._read_journal()
        pending = [e for e, _ in entries if e.plan_revision > self._state.plan_revision]
        if not pending:
            return
        state = asdict(self._state)
        for event in pending:
            state, _ = _apply(state, event)
        self._state = PlanState(**state)
        _atomic_write_json(self._dir / _PLAN_NAME, asdict(self._state))

    # ── the one mutation path ──

    def submit_event(
        self,
        *,
        event_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Submit one controller event. Idempotent: a repeated event_id
        replays the recorded outcome without touching state. Write-ahead:
        the journal entry lands before the plan file is mutated. Conflicts:
        an expected_revision that no longer matches refuses."""
        payload = dict(payload or {})
        with _locked(self._dir):
            # Sync with the disk first: the journal may have moved under this
            # instance (another process, a crash recovery). The revision
            # check below is only meaningful against the fresh revision.
            self._recover()
            entries = self._read_journal()
            for event, outcome in entries:
                if event.event_id == event_id:
                    # Exact replay: the same canonical result, no double-apply.
                    return dict(outcome)
            current = self._state.plan_revision
            if expected_revision is not None and expected_revision != current:
                raise PlanConflict(
                    f"plan {self.plan_id!r} is at revision {current}; "
                    f"caller expected {expected_revision} (stale revision). "
                    "Reload and retry -- concurrent edits never silently merge."
                )
            if kind == "budget_charged":
                name = payload["budget"]
                ceiling = self._state.budgets.get(name)
                used = self._state.budget_used.get(name, 0) + int(payload["amount"])
                if ceiling is not None and used > ceiling:
                    raise PlanBudgetExhausted(
                        f"budget {name!r} would reach {used}/{ceiling}: refused. "
                        "An exhausted budget is never success."
                    )
            revision = current + 1
            event = ControllerEvent(
                event_id=event_id,
                kind=kind,
                at=_now(),
                plan_revision=revision,
                payload=payload,
            )
            state, outcome = _apply(asdict(self._state), event)
            # Write-ahead: journal first, plan file second. A crash between
            # the two is repaired by _recover() on the next load.
            with self._journal_path().open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "event_id": event.event_id,
                            "kind": event.kind,
                            "at": event.at,
                            "plan_revision": event.plan_revision,
                            "payload": event.payload,
                            "outcome": outcome,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            self._state = PlanState(**state)
            _atomic_write_json(self._dir / _PLAN_NAME, asdict(self._state))
            return outcome

    # ── reads (stored facts only) ──

    @property
    def state(self) -> PlanState:
        return self._state

    def event_count(self) -> int:
        return len(self._read_journal())

    def status_snapshot(self) -> dict[str, Any]:
        """The whole plan status, derived from stored facts only: the plan
        file, the event journal, and the receipts on disk. Two controller
        instances on the same state dir report the identical snapshot --
        that is the proof status never came from memory."""
        state = asdict(self._state)
        # Sorted for determinism: the snapshot is a pure function of stored
        # facts, so any two instances -- in-memory or freshly loaded -- must
        # report the identical snapshot.
        budgets = {
            name: {"used": state["budget_used"].get(name, 0), "ceiling": state["budgets"].get(name)}
            for name in sorted(state["budgets"])
        }
        return {
            "plan_id": self.plan_id,
            "revision": state["plan_revision"],
            "status": state["status"],
            "approval_state": state["approval_state"],
            "scope": list(state["scope"]),
            "budgets": budgets,
            "pending_actions": list(state["pending_action_ids"]),
            "pending_approvals": [
                {
                    "action_id": a["action_id"],
                    "action": a["action"],
                    "detail": a["detail"],
                }
                for a in state["pending_approvals"]
            ],
            "approvals_granted": len(state["approvals"]),
            "verifier": state["verifier"],
            "contracts": {
                cid: {
                    "revision": entry.get("contract_revision"),
                    "state": entry.get("state"),
                    "brief_type": entry.get("brief_type"),
                }
                for cid, entry in sorted(state.get("contracts", {}).items())
            },
            "last_review": state["last_review"],
            "events": self.event_count(),
            "updated_at": state["updated_at"],
        }

    def status_lines(self) -> list[str]:
        """Human-readable status from stored facts. Interrupting a session
        and restarting shows the same pending work, approval state, and
        budget -- that is the observable contract."""
        snap = self.status_snapshot()
        lines = [f"PLAN  {snap['plan_id']}  rev={snap['revision']}  status={snap['status']}"]
        lines.append(
            f"APPROVAL  {snap['approval_state']} "
            f"({snap['approvals_granted']} granted, {len(snap['pending_approvals'])} pending)"
        )
        budget_bits = "  ".join(
            f"{name} {info['used']}/{info['ceiling']}" for name, info in snap["budgets"].items()
        )
        lines.append(f"BUDGET  {budget_bits}")
        if snap["pending_actions"]:
            lines.append(
                f"PENDING  {len(snap['pending_actions'])} action(s): {', '.join(snap['pending_actions'])}"
            )
        else:
            lines.append("PENDING  no pending actions")
        for approval in snap["pending_approvals"]:
            lines.append(f"AWAITING_APPROVAL  {approval['action_id']}: {approval['action']}")
        if snap["scope"]:
            lines.append(f"SCOPE  {', '.join(snap['scope'])}")
        if snap["verifier"]:
            lines.append(f"VERIFIER  {snap['verifier']}")
        if snap["last_review"]:
            review = snap["last_review"]
            lines.append(f"REVIEW  {review['verdict']} by {review['by']} at {review['at']}")
        return lines


# ── shared services ─────────────────────────────────────────────────────
# One implementation used by the loop controllers and the machine
# controller alike. Entry points keep their own APIs; these are the
# explicit adapters' shared vocabulary.


def preflight(controller: PlanController) -> list[str]:
    """Blocking problems before any work starts. Pure reads over stored
    state; empty means clear to proceed."""
    state = controller.state
    problems: list[str] = []
    if state.status == "closed":
        problems.append(f"plan {state.plan_id!r} is closed")
    if state.approval_state == "invalidated":
        problems.append("plan approval was invalidated; a human must re-approve before work")
    for name, ceiling in state.budgets.items():
        used = state.budget_used.get(name, 0)
        if used >= ceiling:
            problems.append(f"budget exhausted: {name} {used}/{ceiling}")
    return problems


def request_approval(
    controller: PlanController,
    action: str,
    detail: str = "",
    *,
    action_id: str | None = None,
    by: str = "human",
    plan_level: bool = False,
) -> str:
    """Record that a consequential action was asked about. Returns the
    action id the human's grant must name."""
    aid = action_id or f"approval-{controller.state.plan_revision + 1}-{abs(hash(action)) % 10_000}"
    controller.submit_event(
        event_id=f"approval-requested-{aid}",
        kind="approval_requested",
        payload={
            "action_id": aid,
            "action": action,
            "detail": detail,
            "by": by,
            "plan_level": plan_level,
        },
    )
    return aid


def require_approval(controller: PlanController, action_id: str) -> dict[str, Any]:
    """Every consequential action asks first. Returns the recorded grant;
    otherwise records the ask (if not already pending) and refuses."""
    for grant in controller.state.approvals:
        if grant["action_id"] == action_id:
            return dict(grant)
    pending = next(
        (a for a in controller.state.pending_approvals if a["action_id"] == action_id), None
    )
    if pending is None:
        request_approval(controller, action_id, action_id=action_id)
        pending_action = action_id
    else:
        pending_action = pending["action"]
    raise ApprovalRequired(
        f"consequential action {pending_action!r} needs human approval first "
        f"(action_id={action_id}). The ask is recorded; grant it, then retry."
    )


def grant_approval(
    controller: PlanController, action_id: str, *, by: str, plan_level: bool = False
) -> dict[str, Any]:
    """The human's grant, with provenance. plan_level=True approves the
    plan itself (approval_state -> approved)."""
    if not by or not by.strip():
        raise PlanError("an approval needs a name: who granted it")
    return controller.submit_event(
        event_id=f"approval-granted-{action_id}",
        kind="approval_granted",
        payload={"action_id": action_id, "by": by.strip(), "plan_level": plan_level},
    )


def invalidate_approval(controller: PlanController, reason: str) -> dict[str, Any]:
    """A material change invalidates the plan approval; work stops until a
    human re-approves. The reason is journaled."""
    return controller.submit_event(
        event_id=f"approval-invalidated-r{controller.state.plan_revision + 1}",
        kind="approval_invalidated",
        payload={"reason": reason},
    )


def queue_action(controller: PlanController, action_id: str) -> dict[str, Any]:
    return controller.submit_event(
        event_id=f"action-queued-{action_id}",
        kind="action_queued",
        payload={"action_id": action_id},
    )


def apply_action(controller: PlanController, action_id: str, result: str = "") -> dict[str, Any]:
    """Mark one pending action applied. Idempotent on the event id: a retry
    of the same application replays the recorded outcome."""
    return controller.submit_event(
        event_id=f"action-applied-{action_id}",
        kind="action_applied",
        payload={"action_id": action_id, "result": result},
    )


def charge_budget(controller: PlanController, budget: str, amount: int = 1) -> dict[str, Any]:
    """Charge a budget. Refuses past the ceiling: an exhausted budget is
    never success, and the refusal leaves no journal trace of work done."""
    if amount <= 0:
        raise PlanError("budget charges must be positive")
    return controller.submit_event(
        event_id=f"budget-charged-{budget}-r{controller.state.plan_revision + 1}-{amount}",
        kind="budget_charged",
        payload={"budget": budget, "amount": amount},
    )


def record_review(
    controller: PlanController, *, verdict: str, detail: str = "", by: str = "human"
) -> dict[str, Any]:
    """The shared review service. Verdicts are mechanical: ship, revise, or
    blocked -- never a judgement smuggled in as prose."""
    if verdict not in ("ship", "revise", "blocked"):
        raise PlanError(f"review verdict must be ship|revise|blocked, got {verdict!r}")
    return controller.submit_event(
        event_id=f"review-r{controller.state.plan_revision + 1}",
        kind="review_recorded",
        payload={"verdict": verdict, "detail": detail, "by": by},
    )


def close_plan(controller: PlanController, *, by: str) -> dict[str, Any]:
    """The shared closure service. Refuses while work is pending, while
    approvals are pending, or while the plan itself was never approved:
    closure is a verdict over stored facts, not a button."""
    state = controller.state
    if state.status == "closed":
        raise PlanNotClosable(f"plan {state.plan_id!r} is already closed")
    if state.pending_action_ids:
        raise PlanNotClosable(
            f"{len(state.pending_action_ids)} pending action(s): "
            f"{', '.join(state.pending_action_ids)}"
        )
    if state.pending_approvals:
        raise PlanNotClosable(
            f"{len(state.pending_approvals)} pending approval(s): "
            f"{', '.join(a['action_id'] for a in state.pending_approvals)}"
        )
    if state.approval_state != "approved":
        raise PlanNotClosable(
            f"plan approval is {state.approval_state!r}; a human must approve the plan before closure"
        )
    return controller.submit_event(
        event_id=f"plan-closed-r{state.plan_revision + 1}",
        kind="plan_closed",
        payload={"by": by},
    )


# ── knowledge through the controller ────────────────────────────────────
# The unified service for the best / battery / claude / exam flows: every
# knowledge consultation is budget-charged against the plan, accounting
# persists across fresh stores, and an answer always cites a recorded
# receipt. Answers without a receipt are refused.


def consult_knowledge(
    controller: PlanController,
    paths,
    question: str,
    source_id: str,
    path: str,
    *,
    by: str = "human",
    force: bool = False,
    client=None,
) -> dict[str, Any]:
    """Fetch one knowledge file under the plan's knowledge budget and record
    the receipt the answer must cite. Idempotent per question: asking again
    replays the recorded receipt without re-charging the budget."""
    existing = _stored_knowledge_receipt(controller.state_root, question)
    if existing is not None:
        return {
            "question": question,
            "source_id": existing.source_id,
            "path": existing.path,
            "sha": existing.sha,
            "replay": True,
        }
    charge_budget(controller, "knowledge_files", 1)
    store = KnowledgeStore(
        paths,
        budget=controller.state.budgets.get("knowledge_files", KNOWLEDGE_DEFAULT_BUDGET),
        accounting_key=f"plan-{controller.plan_id}",
        client=client,
    )
    target, status = store.fetch(path, source_id, force=force)
    entry = store.manifest.get(source_id, path)
    sha = entry.sha if entry else "unknown"
    receipt = _record_knowledge_receipt(
        controller.state_root,
        question=question,
        source_id=source_id,
        path=path,
        sha=sha,
        by=by,
    )
    controller.submit_event(
        event_id=f"knowledge-receipt-{receipt.question_hash}",
        kind="knowledge_receipt",
        payload={
            "question_hash": receipt.question_hash,
            "source_id": source_id,
            "path": path,
            "sha": sha,
            "cache_file": target.name,
            "fetch_status": status,
        },
    )
    return {
        "question": question,
        "source_id": source_id,
        "path": path,
        "sha": sha,
        "cache_file": str(target),
        "replay": False,
    }


def answer_from_knowledge(controller: PlanController, question: str) -> dict[str, Any]:
    """Cite a recorded knowledge receipt for a question. Refuses when no
    receipt exists: a knowledge answer without a recorded receipt is a
    claim without provenance."""
    receipt = _require_knowledge_receipt(controller.state_root, question)
    return {
        "question": question,
        "source_id": receipt.source_id,
        "path": receipt.path,
        "sha": receipt.sha,
        "recorded_at": receipt.at,
        "recorded_by": receipt.by,
    }


# ── explicit adapters ───────────────────────────────────────────────────
# The entry points keep their own APIs. Each adapter binds one entry point
# (a loop, a machine run, or a bare plan) to the shared controller: same
# preflight, same approval, same review, same closure, same status.


@dataclass
class PlanAdapter:
    """One entry point's view of the shared controller."""

    entry_point: str  # "loop" | "machine" | "plan"
    controller: PlanController

    def preflight(self) -> list[str]:
        return preflight(self.controller)

    def status_lines(self) -> list[str]:
        header = f"VIA  {self.entry_point} adapter"
        return [header, *self.controller.status_lines()]

    def request_approval(self, action: str, detail: str = "", **kwargs) -> str:
        return request_approval(self.controller, action, detail, **kwargs)

    def require_approval(self, action_id: str) -> dict[str, Any]:
        return require_approval(self.controller, action_id)

    def close(self, *, by: str = "human") -> dict[str, Any]:
        return close_plan(self.controller, by=by)


def _get_or_create(
    state_root: Path,
    plan_id: str,
    *,
    budgets: dict[str, int] | None,
    verifier: str | None,
) -> PlanController:
    try:
        return PlanController.load(state_root, plan_id)
    except PlanNotFound:
        return PlanController.create(state_root, plan_id, budgets=budgets, verifier=verifier)


def for_plan(
    state_root: Path,
    plan_id: str,
    *,
    scope: list[str] | None = None,
    budgets: dict[str, int] | None = None,
    verifier: str | None = None,
) -> PlanAdapter:
    """Bind a bare plan id. Creates the plan on first use, loads it after."""
    try:
        controller = PlanController.load(state_root, plan_id)
    except PlanNotFound:
        controller = PlanController.create(
            state_root, plan_id, scope=scope, budgets=budgets, verifier=verifier
        )
    return PlanAdapter(entry_point="plan", controller=controller)


def for_loop(
    state_root: Path,
    loop_id: str,
    *,
    budgets: dict[str, int] | None = None,
    verifier: str | None = None,
) -> PlanAdapter:
    """Bind a loop controller's run: the loop keeps its driver and state;
    the plan carries the durable approval/budget/event trail."""
    controller = _get_or_create(state_root, f"loop-{loop_id}", budgets=budgets, verifier=verifier)
    return PlanAdapter(entry_point="loop", controller=controller)


def for_machine(
    state_root: Path,
    run_id: str,
    *,
    budgets: dict[str, int] | None = None,
    verifier: str | None = None,
) -> PlanAdapter:
    """Bind a machine run (`awino best` / `awino step`): the machine keeps
    its node table; the plan carries the durable trail."""
    controller = _get_or_create(state_root, f"machine-{run_id}", budgets=budgets, verifier=verifier)
    return PlanAdapter(entry_point="machine", controller=controller)
