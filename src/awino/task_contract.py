"""Shared planning and assignment contract (Phase 3 of the recovery).

One contract datatype, one documented template, one spawn/dispatch binding.

The problem this fixes: spawn-time context drift (every spawn path invented
its own assignment shape), lost human edits (the user's wording on the plan
was re-derived instead of preserved), and approval that did not bind to what
was approved (the grant floated free of the revision the human reviewed).

The contract is a versioned, normalized, validated value object:

- ``normalize()`` canonicalizes wording/whitespace, scope ordering, and role
  so two identical contracts hash identically.
- ``validate()`` enforces the same invariants the spawn path enforces
  (read-only roles declare no file scope, writing roles declare scope and a
  verification command), so subagents and CLI commands read one source.
- ``revision_hash()`` binds an approval to the exact content reviewed: a
  grant names (contract_revision, revision_hash), and ``approval_covers()``
  fails when the contract was edited after the grant.
- ``apply_human_edit()`` records who changed what, keeps the user's wording
  verbatim, and invalidates approval only for material changes (scope,
  action, budget). Cosmetic wording fixes keep approval when the caller says
  so explicitly; the default is fail-closed (material).
- ``assert_current()`` / ``rebase_contract()`` implement staleness: a worker
  holding a contract from an older plan revision is refused at spawn time.

Approval itself lives in the Phase 2 controller: contract lifecycle events
(``contract_saved`` / ``contract_approved`` / ``contract_invalidated`` /
``contract_superseded``) are journaled controller events, and the human grant
goes through ``controller.grant_approval`` with provenance. There is no
second approval system.

The one planning brief type is ``task-brief/v1``: ``render_prefilled_draft``
produces the editable draft, ``apply_brief_edits`` parses the returned text
back into provenance-recorded edits.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

# ── schema identity ─────────────────────────────────────────────────────

#: The contract schema version. Bumped only for breaking field changes; the
#: template and the dataclass must agree (a test enforces this).
CONTRACT_SCHEMA_VERSION = "1"

#: The one planning brief type. Spawned workers reference it so a reader can
#: always tell which brief shape the assignment was built from.
BRIEF_TYPE = "task-brief/v1"

# ── states ──────────────────────────────────────────────────────────────

DRAFT = "draft"
APPROVED = "approved"
INVALIDATED = "invalidated"
SUPERSEDED = "superseded"
CLOSED = "closed"

STATES = (DRAFT, APPROVED, INVALIDATED, SUPERSEDED, CLOSED)

#: Legal transitions. Approval is granted from draft/invalidated; a material
#: edit or a plan rebase moves approved back to invalidated (never silently
#: back to draft -- the history must show the grant that was voided).
#: Superseded/closed are terminal.
VALID_TRANSITIONS: dict[str, tuple[str, ...]] = {
    DRAFT: (APPROVED, SUPERSEDED, CLOSED),
    APPROVED: (INVALIDATED, SUPERSEDED, CLOSED),
    INVALIDATED: (APPROVED, SUPERSEDED, CLOSED),
    SUPERSEDED: (),
    CLOSED: (),
}

# ── materiality ─────────────────────────────────────────────────────────
# An edit to one of these fields changes what the worker may do, what it
# must produce, or what it may spend: it invalidates a standing approval.
# Anything else (recorded anyway, with provenance) is cosmetic. The default
# is fail-closed: unknown fields are treated as material.

MATERIAL_FIELDS = frozenset(
    {
        "role",
        "objective",
        "file_scope",
        "context_paths",
        "verification",
        "budgets",
        "verifier",
        "depends_on",
    }
)

ROLES = ("scout", "reviewer", "builder")
READ_ONLY_ROLES = ("scout", "reviewer")

# ── errors ──────────────────────────────────────────────────────────────


class ContractError(RuntimeError):
    """Base for contract failures."""


class InvalidContract(ContractError):
    """The contract failed validation; the problems are listed."""


class StaleContract(ContractError):
    """The contract was built against an older plan revision."""


class ContractNotApproved(ContractError):
    """An operation needed an approved contract and did not have one."""


class ContractNotFound(ContractError):
    """No stored contract with this id in the plan."""


class BriefParseError(ContractError):
    """A returned planning brief could not be parsed into edits."""


__all__ = [
    "APPROVED",
    "BRIEF_TYPE",
    "CLOSED",
    "CONTRACT_SCHEMA_VERSION",
    "DRAFT",
    "INVALIDATED",
    "MATERIAL_FIELDS",
    "STATES",
    "SUPERSEDED",
    "VALID_TRANSITIONS",
    "BriefParseError",
    "ContractError",
    "ContractNotApproved",
    "ContractNotFound",
    "ContractRef",
    "InvalidContract",
    "PlanningBrief",
    "StaleContract",
    "TaskContract",
    "apply_brief_edits",
    "apply_human_edit",
    "check_contract_ref",
    "create_contract",
    "grant_contract_approval",
    "invalidate_contract_approval",
    "load_contract",
    "load_template",
    "rebase_contract",
    "record_contract_edit",
    "render_contract_block",
    "render_prefilled_draft",
    "request_contract_approval",
    "revision_hash",
    "save_contract",
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm_text(value: str) -> str:
    """Collapse whitespace: the user's wording is kept, but 'a  b' and
    'a b' are the same contract content for hashing and comparison."""
    return re.sub(r"\s+", " ", value.strip())


def _norm_paths(values: list[str] | tuple[str, ...] | None) -> list[str]:
    seen: list[str] = []
    for raw in values or []:
        item = str(raw).strip().replace("\\", "/")
        if item and item not in seen:
            seen.append(item)
    return sorted(seen)


# ── the contract ────────────────────────────────────────────────────────


@dataclass
class TaskContract:
    """One approved unit of planned work, bound to a plan revision.

    ``contract_revision`` bumps on every edit; ``plan_revision_seen`` is the
    plan revision the contract was built/approved against (staleness).
    ``approval`` is the grant bound to (contract_revision, revision_hash).
    ``human_edits`` is the provenance log: every edit records who changed
    what, so a revision history survives even when approval is invalidated.
    """

    contract_id: str
    plan_id: str
    role: str
    objective: str
    contract_revision: int = 1
    schema_version: str = CONTRACT_SCHEMA_VERSION
    file_scope: list[str] = field(default_factory=list)
    context_paths: list[str] = field(default_factory=list)
    verification: str = ""
    budgets: dict[str, int] = field(default_factory=dict)
    verifier: str = ""
    depends_on: list[str] = field(default_factory=list)
    state: str = DRAFT
    approval: dict[str, Any] | None = None
    plan_revision_seen: int = 0
    human_edits: list[dict[str, Any]] = field(default_factory=list)
    brief_type: str = BRIEF_TYPE
    created_at: str = ""
    updated_at: str = ""

    # ── normalization / validation ──

    def normalize(self) -> None:
        """Canonicalize in place so identical contracts hash identically."""
        self.contract_id = self.contract_id.strip()
        self.plan_id = self.plan_id.strip()
        self.role = self.role.strip().lower()
        self.objective = _norm_text(self.objective)
        self.file_scope = _norm_paths(self.file_scope)
        self.context_paths = _norm_paths(self.context_paths)
        self.depends_on = _norm_paths(self.depends_on)
        self.verification = self.verification.strip()
        self.verifier = self.verifier.strip()
        self.budgets = {str(k).strip(): int(v) for k, v in (self.budgets or {}).items()}

    def validate(self) -> list[str]:
        """Contract violations. Empty means valid. Mirrors the invariants
        the spawn path enforces, so both read one source."""
        issues: list[str] = []
        if not self.contract_id:
            issues.append("contract_id is empty")
        if not self.plan_id:
            issues.append("plan_id is empty")
        if not self.objective:
            issues.append("objective is empty")
        if self.role not in ROLES:
            issues.append(f"role {self.role!r} is not one of {', '.join(ROLES)}")
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            issues.append(f"schema_version {self.schema_version!r} != {CONTRACT_SCHEMA_VERSION!r}")
        if self.state not in STATES:
            issues.append(f"state {self.state!r} is not one of {', '.join(STATES)}")
        if self.contract_revision < 1:
            issues.append("contract_revision must be >= 1")
        if self.role in READ_ONLY_ROLES and self.file_scope:
            issues.append(f"{self.role} is read-only but declares a file scope")
        if self.role == "builder":
            if not self.file_scope:
                issues.append("a builder must declare its file scope")
            if not self.verification.strip():
                issues.append("a builder must declare a verification command")
            if not self.verifier.strip():
                issues.append("a builder must name a verifier")
        for name, ceiling in self.budgets.items():
            if not name:
                issues.append("budget with an empty name")
            if int(ceiling) <= 0:
                issues.append(f"budget {name!r} ceiling must be positive")
        return issues

    def assert_valid(self) -> None:
        issues = self.validate()
        if issues:
            raise InvalidContract("; ".join(issues))

    # ── binding: approval covers exactly what was reviewed ──

    def revision_hash(self) -> str:
        """Hash of the semantic content the approval binds to. Excludes
        bookkeeping (plan_revision_seen, state, approval, edit log) so a
        rebase or a state transition does not void the content binding --
        only a content edit does."""
        payload = {
            "schema_version": self.schema_version,
            "contract_revision": self.contract_revision,
            "role": self.role,
            "objective": self.objective,
            "file_scope": self.file_scope,
            "context_paths": self.context_paths,
            "verification": self.verification,
            "budgets": self.budgets,
            "verifier": self.verifier,
            "depends_on": self.depends_on,
            "brief_type": self.brief_type,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    # ── spawn/dispatch reference ──

    def to_ref(self) -> ContractRef:
        """The reference a spawn/dispatch call carries for this contract."""
        return ContractRef(
            plan_id=self.plan_id,
            contract_id=self.contract_id,
            revision=self.contract_revision,
        )

    def approval_covers(self) -> bool:
        """True only when a recorded grant names this exact revision and its
        content binding still holds. An edit after approval -- even one byte
        of the objective -- fails this check, EXCEPT for recorded cosmetic
        edits: the spec invalidates approval only for material scope
        changes, so edits explicitly marked non-material (with provenance:
        who, when, before/after) are tolerated. Anything unrecorded, or any
        material edit after the grant, fails the check."""
        grant = self.approval
        if not grant:
            return False
        grant_rev = grant.get("contract_revision")
        if grant_rev is None or grant_rev > self.contract_revision:
            return False
        if grant.get("brief_type", BRIEF_TYPE) != self.brief_type:
            return False
        if (
            grant_rev == self.contract_revision
            and grant.get("revision_hash") == self.revision_hash()
        ):
            return True
        # The revision moved or the hash moved: the only acceptable
        # explanation is recorded cosmetic edits. Reverse-apply every
        # post-grant edit; if all were non-material and the reverted
        # content matches the granted hash, the grant still covers the
        # contract. Anything unrecorded, or any material edit, fails.
        post_grant = [e for e in self.human_edits if e["contract_revision"] > grant_rev]
        if not post_grant or any(e.get("material") for e in post_grant):
            return False
        reverted_fields = {}
        for edit in reversed(post_grant):
            reverted_fields[edit["field"]] = copy.deepcopy(edit["before"])
        candidate = replace(self, contract_revision=grant_rev, **reverted_fields)
        return grant["revision_hash"] == candidate.revision_hash()

    # ── staleness ──

    def is_stale(self, current_plan_revision: int) -> bool:
        """The plan moved on since this contract was built/approved."""
        return self.plan_revision_seen < int(current_plan_revision)

    def assert_current(self, current_plan_revision: int) -> None:
        if self.is_stale(current_plan_revision):
            raise StaleContract(
                f"contract {self.contract_id!r} r{self.contract_revision} was built "
                f"against plan r{self.plan_revision_seen}; plan is at "
                f"r{current_plan_revision}. Rebase and re-approve -- a stale "
                "revision is never executed."
            )


def revision_hash(contract: TaskContract) -> str:
    """Module-level alias; the method is the source of truth."""
    return contract.revision_hash()


def create_contract(
    *,
    contract_id: str,
    plan_id: str,
    role: str,
    objective: str,
    file_scope: list[str] | None = None,
    context_paths: list[str] | None = None,
    verification: str = "",
    budgets: dict[str, int] | None = None,
    verifier: str = "",
    depends_on: list[str] | None = None,
    plan_revision_seen: int = 0,
) -> TaskContract:
    """Build, normalize, and validate a contract. Raises InvalidContract
    listing every problem -- a half-formed contract never exists."""
    contract = TaskContract(
        contract_id=contract_id,
        plan_id=plan_id,
        role=role,
        objective=objective,
        file_scope=list(file_scope or []),
        context_paths=list(context_paths or []),
        verification=verification,
        budgets=dict(budgets or {}),
        verifier=verifier,
        depends_on=list(depends_on or []),
        plan_revision_seen=int(plan_revision_seen),
        created_at=_now(),
        updated_at=_now(),
    )
    contract.normalize()
    contract.assert_valid()
    return contract


# ── human edits: provenance, wording preserved, materiality ──────────────


def _transition(contract: TaskContract, to_state: str) -> None:
    if to_state == contract.state:
        return
    allowed = VALID_TRANSITIONS.get(contract.state, ())
    if to_state not in allowed:
        raise ContractError(
            f"contract {contract.contract_id!r}: {contract.state} -> {to_state} "
            f"is not a legal transition (legal: {', '.join(allowed) or 'none'})"
        )
    contract.state = to_state


def apply_human_edit(
    contract: TaskContract,
    field_name: str,
    new_value: Any,
    *,
    by: str,
    material: bool | None = None,
) -> dict[str, Any]:
    """Record one human edit with provenance. The user's wording is kept
    verbatim (then normalized like any contract content); the edit log
    records who changed what and when, so the revision history survives even
    when approval is invalidated.

    Materiality: edits to scope/action/budget fields are material by
    default (fail-closed). Pass ``material=False`` explicitly for cosmetic
    wording fixes -- the edit is still recorded, but approval stands. A
    material edit on an approved contract moves it to ``invalidated``; the
    caller persists and journals the invalidation via
    ``record_contract_edit`` / ``invalidate_contract_approval``.
    """
    if not by or not str(by).strip():
        raise ContractError("an edit needs a name: who changed it")
    if not hasattr(contract, field_name):
        raise ContractError(f"contract has no field {field_name!r}")
    if field_name in {
        "contract_id",
        "plan_id",
        "contract_revision",
        "schema_version",
        "state",
        "approval",
        "human_edits",
        "brief_type",
        "created_at",
        "updated_at",
        "plan_revision_seen",
    }:
        raise ContractError(
            f"field {field_name!r} is system-managed; use the lifecycle "
            "functions (rebase, grant/invalidate approval) instead"
        )
    if material is None:
        material = field_name in MATERIAL_FIELDS

    before = getattr(contract, field_name)
    if field_name in {"file_scope", "context_paths", "depends_on"}:
        new_norm: Any = _norm_paths(
            new_value if isinstance(new_value, (list, tuple)) else [new_value]
        )
    elif field_name == "budgets":
        new_norm = {str(k).strip(): int(v) for k, v in dict(new_value or {}).items()}
    elif field_name in {"role"}:
        new_norm = str(new_value).strip().lower()
    else:
        new_norm = _norm_text(str(new_value))

    record = {
        "field": field_name,
        "before": copy.deepcopy(before),
        "after": copy.deepcopy(new_norm),
        "by": str(by).strip(),
        "at": _now(),
        "material": bool(material),
        "contract_revision": contract.contract_revision + 1,
    }
    setattr(contract, field_name, new_norm)
    contract.contract_revision += 1
    contract.updated_at = _now()
    contract.human_edits.append(record)

    issues = contract.validate()
    if issues:
        # Roll back: a contract that fails validation never exists, even
        # transiently in memory.
        setattr(contract, field_name, before)
        contract.contract_revision -= 1
        contract.human_edits.pop()
        raise InvalidContract("; ".join(issues))

    if material and contract.state == APPROVED:
        _transition(contract, INVALIDATED)
        record["invalidated_approval"] = True
    return record


def rebase_contract(controller: Any, contract: TaskContract, *, by: str) -> dict[str, Any]:
    """Adopt the current plan revision after the plan moved on. Content is
    untouched; an approved contract becomes ``invalidated`` (the approval
    was bound to the old plan state) and must be re-approved. The edit log
    records the rebase so the history is unbroken. Refuses when the plan
    has not actually moved on."""
    from awino.controller import PlanController

    if not by or not str(by).strip():
        raise ContractError("a rebase needs a name: who rebased it")
    # Fresh read: another process may have moved the plan since this
    # controller instance loaded it.
    fresh = PlanController.load(controller.state_root, contract.plan_id)
    plan_rev_before = fresh.state.plan_revision
    new_plan_revision = plan_rev_before
    if new_plan_revision <= contract.plan_revision_seen:
        raise ContractError(
            f"plan is at r{new_plan_revision}; contract already sees "
            f"r{contract.plan_revision_seen} -- nothing to rebase"
        )
    record = {
        "field": "plan_revision_seen",
        "before": contract.plan_revision_seen,
        "after": new_plan_revision,
        "by": str(by).strip(),
        "at": _now(),
        "material": True,
        "contract_revision": contract.contract_revision + 1,
        "rebase": True,
    }
    contract.plan_revision_seen = new_plan_revision
    contract.contract_revision += 1
    contract.updated_at = _now()
    contract.human_edits.append(record)
    if contract.state == APPROVED:
        _transition(contract, INVALIDATED)
        record["invalidated_approval"] = True
        invalidate_contract_approval(
            controller, contract, reason=f"plan moved to r{new_plan_revision}; rebase by {by}"
        )
    save_contract(controller, contract)
    # The rebase's own journal events moved the plan; account for them so
    # the contract is not stale against itself. External movement after
    # this still reads as stale.
    contract.plan_revision_seen += controller.state.plan_revision - plan_rev_before
    save_contract(controller, contract)
    return record


# ── approval lifecycle (through the Phase 2 controller) ─────────────────
# The controller owns the journal and the human-grant record; this module
# owns the contract file and the revision binding. Neither duplicates the
# other.


def _contract_event_id(contract: TaskContract, verb: str) -> str:
    return f"contract-{verb}-{contract.contract_id}-r{contract.contract_revision}"


def save_contract(controller: Any, contract: TaskContract) -> dict[str, Any]:
    """Persist the contract and journal the save. Write-ahead: the
    controller event lands before the file is written; a crash between the
    two heals on the next save because the event id is idempotent and the
    file is rewritten from the in-memory contract."""
    contract.assert_valid()
    contract.updated_at = _now()
    outcome = controller.submit_event(
        event_id=_contract_event_id(contract, "saved"),
        kind="contract_saved",
        payload={
            "contract_id": contract.contract_id,
            "plan_id": contract.plan_id,
            "contract_revision": contract.contract_revision,
            "state": contract.state,
            "brief_type": contract.brief_type,
            "role": contract.role,
            "plan_revision_seen": contract.plan_revision_seen,
        },
    )
    _write_contract_files(controller.state_root, contract)
    return outcome


def record_contract_edit(
    controller: Any,
    contract: TaskContract,
    field_name: str,
    new_value: Any,
    *,
    by: str,
    material: bool | None = None,
) -> dict[str, Any]:
    """One call for the common path: apply the human edit, persist it, and
    journal the approval invalidation when a material edit voided a grant.
    When the edit was cosmetic and the contract stays approved, the
    ``plan_revision_seen`` advances past only this save's own journal
    events -- external plan movement still reads as stale."""
    plan_rev_before = controller.state.plan_revision
    record = apply_human_edit(contract, field_name, new_value, by=by, material=material)
    if record.get("invalidated_approval"):
        invalidate_contract_approval(
            controller,
            contract,
            reason=f"material edit to {field_name} by {record['by']}",
        )
    save_contract(controller, contract)
    if contract.state == APPROVED:
        advanced = controller.state.plan_revision - plan_rev_before
        contract.plan_revision_seen += advanced
        save_contract(controller, contract)
    return record


def request_contract_approval(
    controller: Any,
    contract: TaskContract,
    *,
    action: str,
    detail: str = "",
    by: str = "human",
) -> str:
    """Record the approval ask at brief-review time -- the fix for the 0.8
    bug where approval was asked at the wrong time (mid-spawn). The human
    reviews the prefilled brief, the ask names the contract revision under
    review, and dispatch later refuses unapproved contracts instead of
    asking again."""
    from awino.controller import request_approval

    action_id = f"contract-{contract.contract_id}-r{contract.contract_revision}"
    return request_approval(
        controller,
        action or f"approve task contract {contract.contract_id} r{contract.contract_revision}",
        detail or f"brief_type={contract.brief_type} role={contract.role}",
        action_id=action_id,
        by=by,
    )


def grant_contract_approval(controller: Any, contract: TaskContract, *, by: str) -> dict[str, Any]:
    """The human's grant, bound to the exact revision reviewed. Records the
    grant on the contract (revision + content hash), the human provenance in
    the controller's approval list, and the state transition in the journal.
    Afterwards the contract's ``plan_revision_seen`` is refreshed to the
    post-grant plan revision so the just-approved contract is not instantly
    stale against its own grant events."""
    from awino.controller import grant_approval

    if not by or not str(by).strip():
        raise ContractError("an approval needs a name: who granted it")
    if contract.state not in (DRAFT, INVALIDATED):
        raise ContractError(
            f"contract {contract.contract_id!r} is {contract.state}; only a "
            "draft or invalidated contract can be approved"
        )
    contract.assert_valid()
    contract.approval = {
        "by": str(by).strip(),
        "at": _now(),
        "contract_revision": contract.contract_revision,
        "revision_hash": contract.revision_hash(),
        "brief_type": contract.brief_type,
    }
    _transition(contract, APPROVED)
    save_contract(controller, contract)
    action_id = f"contract-{contract.contract_id}-r{contract.contract_revision}"
    grant_approval(controller, action_id, by=str(by).strip())
    outcome = controller.submit_event(
        event_id=_contract_event_id(contract, "approved"),
        kind="contract_approved",
        payload={
            "contract_id": contract.contract_id,
            "plan_id": contract.plan_id,
            "contract_revision": contract.contract_revision,
            "revision_hash": contract.approval["revision_hash"],
            "by": contract.approval["by"],
        },
    )
    # The grant events moved the plan revision; refresh so the contract is
    # current against its own approval, then re-persist (same event id, so
    # the journal replay is a no-op and only the file is rewritten).
    contract.plan_revision_seen = controller.state.plan_revision
    save_contract(controller, contract)
    return outcome


def invalidate_contract_approval(
    controller: Any, contract: TaskContract, *, reason: str
) -> dict[str, Any]:
    """Journal that a material change voided the standing grant. The
    contract must already be in ``invalidated`` (``apply_human_edit`` /
    ``rebase_contract`` move it there); this records the reason in the
    controller's own approval journal too."""
    from awino.controller import invalidate_approval

    if contract.state != INVALIDATED:
        raise ContractError(
            f"contract {contract.contract_id!r} is {contract.state}; invalidation "
            "is only journaled from the invalidated state"
        )
    invalidate_approval(controller, reason or "material contract change")
    return controller.submit_event(
        event_id=_contract_event_id(contract, "invalidated"),
        kind="contract_invalidated",
        payload={
            "contract_id": contract.contract_id,
            "plan_id": contract.plan_id,
            "contract_revision": contract.contract_revision,
            "reason": reason,
        },
    )


def supersede_contract(controller: Any, contract: TaskContract, *, by: str) -> dict[str, Any]:
    """Retire a contract without deleting it: the file and its revision
    history stay on disk, the state becomes terminal."""
    _transition(contract, SUPERSEDED)
    contract.updated_at = _now()
    outcome = controller.submit_event(
        event_id=_contract_event_id(contract, "superseded"),
        kind="contract_superseded",
        payload={
            "contract_id": contract.contract_id,
            "plan_id": contract.plan_id,
            "contract_revision": contract.contract_revision,
            "by": by,
        },
    )
    _write_contract_files(controller.state_root, contract)
    return outcome


# ── persistence: one contract, full revision history ────────────────────

_CONTRACTS_DIRNAME = "contracts"


def contract_dir(state_root: Path, plan_id: str, contract_id: str) -> Path:
    return Path(state_root) / "plans" / plan_id / _CONTRACTS_DIRNAME / contract_id


def _contract_dict(contract: TaskContract) -> dict[str, Any]:
    return asdict(contract)


def _contract_from_dict(data: dict[str, Any]) -> TaskContract:
    data = dict(data)
    data.setdefault("schema_version", CONTRACT_SCHEMA_VERSION)
    data.setdefault("brief_type", BRIEF_TYPE)
    return TaskContract(**data)


def _write_contract_files(state_root: Path, contract: TaskContract) -> Path:
    """Write current.json; before overwriting, snapshot the previous
    current.json as rev-<N>.json so every revision is preserved."""
    directory = contract_dir(state_root, contract.plan_id, contract.contract_id)
    directory.mkdir(parents=True, exist_ok=True)
    current = directory / "current.json"
    if current.is_file():
        try:
            previous = json.loads(current.read_text(encoding="utf-8"))
            rev = int(previous.get("contract_revision", 0))
        except (OSError, ValueError, AttributeError):
            rev = 0
        if rev:
            snapshot = directory / f"rev-{rev}.json"
            if not snapshot.is_file():
                snapshot.write_text(
                    json.dumps(previous, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
    current.write_text(
        json.dumps(_contract_dict(contract), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return current


def load_contract(state_root: Path, plan_id: str, contract_id: str) -> TaskContract:
    path = contract_dir(state_root, plan_id, contract_id) / "current.json"
    if not path.is_file():
        raise ContractNotFound(
            f"no contract {contract_id!r} in plan {plan_id!r} under {state_root}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractNotFound(f"contract {contract_id!r} is unreadable: {exc}") from exc
    return _contract_from_dict(data)


def contract_history(state_root: Path, plan_id: str, contract_id: str) -> list[int]:
    """Which revisions are preserved on disk (snapshots plus current)."""
    directory = contract_dir(state_root, plan_id, contract_id)
    revs: set[int] = set()
    for snap in directory.glob("rev-*.json"):
        try:
            revs.add(int(snap.stem.split("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    current = directory / "current.json"
    if current.is_file():
        with suppress(OSError, ValueError, AttributeError):
            revs.add(
                int(json.loads(current.read_text(encoding="utf-8")).get("contract_revision", 0))
            )
    return sorted(revs)


# ── the one planning brief type ─────────────────────────────────────────
# task-brief/v1: a prefilled, human-editable draft. The human edits the
# draft and returns it; the returned text is parsed back into
# provenance-recorded edits. Nothing about the brief is free-form: unknown
# or missing sections fail closed rather than being silently dropped.


@dataclass(frozen=True)
class PlanningBrief:
    """A prefilled draft issued for human review."""

    brief_type: str
    contract_id: str
    plan_id: str
    contract_revision: int
    draft: str
    issued_at: str


_BRIEF_HEADERS = (
    "Objective",
    "Files you may write",
    "Context to read first",
    "Verification",
    "Budgets",
    "Verifier",
)
_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$")


def render_prefilled_draft(contract: TaskContract) -> PlanningBrief:
    """The editable draft: every section prefilled from the contract, with
    instructions that the human's wording is kept verbatim and that
    scope/action/budget edits visibly reset approval."""
    scope = "\n".join(f"- {p}" for p in contract.file_scope) or "(none)"
    context = "\n".join(f"- {p}" for p in contract.context_paths) or "(none)"
    budgets = "\n".join(f"- {k}: {v}" for k, v in sorted(contract.budgets.items())) or "(none)"
    draft = f"""# Task brief ({BRIEF_TYPE}) -- contract {contract.contract_id} r{contract.contract_revision}

Plan: {contract.plan_id} (plan r{contract.plan_revision_seen}) | Role: {contract.role}

Edit any section below, then return the whole brief. Your wording is kept
verbatim. Edits to scope, action, or budget visibly reset approval -- the
reset is shown, never silent. Do not rename or remove the `##` sections.

## Objective

{contract.objective}

## Files you may write

{scope}

## Context to read first

{context}

## Verification

{contract.verification or "(none)"}

## Budgets

{budgets}

## Verifier

{contract.verifier or "(none)"}
"""
    return PlanningBrief(
        brief_type=BRIEF_TYPE,
        contract_id=contract.contract_id,
        plan_id=contract.plan_id,
        contract_revision=contract.contract_revision,
        draft=draft,
        issued_at=_now(),
    )


def _split_brief_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _HEADER_RE.match(line)
        if match:
            current = match.group(1)
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _parse_bullets(body: str) -> list[str]:
    if body.strip() in ("(none)", ""):
        return []
    items: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
        elif stripped:
            raise BriefParseError(
                f"brief list line is not a '- ' bullet: {stripped!r}; "
                "edit the items, not the list format"
            )
    return items


def _parse_budgets(body: str) -> dict[str, int]:
    if body.strip() in ("(none)", ""):
        return {}
    budgets: dict[str, int] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            raise BriefParseError(f"brief budget line is not a '- ' bullet: {stripped!r}")
        item = stripped[2:].strip()
        if ":" not in item:
            raise BriefParseError(f"brief budget line needs 'name: ceiling': {item!r}")
        name, ceiling = item.split(":", 1)
        try:
            budgets[name.strip()] = int(ceiling.strip())
        except ValueError:
            raise BriefParseError(f"brief budget ceiling is not an integer: {item!r}") from None
    return budgets


def _parse_verbatim(body: str) -> str:
    return "" if body.strip() == "(none)" else body.strip()


def apply_brief_edits(
    controller: Any, contract: TaskContract, edited_text: str, *, by: str
) -> list[dict[str, Any]]:
    """Parse a returned brief into provenance-recorded edits. Fails closed:
    missing or unknown sections are an error, never silently dropped text."""
    sections = _split_brief_sections(edited_text)
    missing = [h for h in _BRIEF_HEADERS if h not in sections]
    if missing:
        raise BriefParseError(
            f"brief is missing sections: {', '.join(missing)}; return the "
            "whole brief with every `##` section intact"
        )
    unknown = [h for h in sections if h not in _BRIEF_HEADERS]
    if unknown:
        raise BriefParseError(
            f"brief has unknown sections: {', '.join(unknown)}; edit the "
            "existing sections instead of adding new ones"
        )

    parsed: dict[str, Any] = {
        "objective": _parse_verbatim(sections["Objective"]),
        "file_scope": _parse_bullets(sections["Files you may write"]),
        "context_paths": _parse_bullets(sections["Context to read first"]),
        "verification": _parse_verbatim(sections["Verification"]),
        "budgets": _parse_budgets(sections["Budgets"]),
        "verifier": _parse_verbatim(sections["Verifier"]),
    }
    records: list[dict[str, Any]] = []
    for field_name, new_value in parsed.items():
        current = getattr(contract, field_name)
        norm_new = new_value
        if isinstance(current, list):
            norm_new = _norm_paths(new_value)
        elif isinstance(current, dict):
            norm_new = {str(k).strip(): int(v) for k, v in new_value.items()}
        else:
            norm_new = _norm_text(str(new_value))
        if norm_new != current:
            records.append(record_contract_edit(controller, contract, field_name, new_value, by=by))
    return records


# ── spawn/dispatch binding: one source, stale revisions refused ──────────


@dataclass(frozen=True)
class ContractRef:
    """What a spawned worker carries: plan, contract, and the exact
    revision it was built from. The spawn path resolves this against the
    stored contract and refuses anything stale or unapproved."""

    plan_id: str
    contract_id: str
    revision: int

    @classmethod
    def parse(cls, value: str) -> ContractRef:
        """Parse ``plan/contract`` or ``plan/contract@revision``.

        The bare form means "the currently stored revision" (the caller
        resolves it against disk before dispatch); the pinned form names
        the exact revision the human reviewed.
        """
        text = value.strip()
        revision: int | None = None
        if "@" in text:
            text, _, rev_text = text.partition("@")
            try:
                revision = int(rev_text.strip())
            except ValueError:
                raise ContractError(
                    f"bad contract reference {value!r}: the revision must be "
                    "an integer, as in plan/contract@3"
                ) from None
            if revision < 1:
                raise ContractError(f"bad contract reference {value!r}: the revision must be >= 1")
        plan_id, sep, contract_id = text.partition("/")
        if not sep or not plan_id.strip() or not contract_id.strip():
            raise ContractError(
                f"bad contract reference {value!r}: expected plan/contract "
                "or plan/contract@revision"
            )
        return cls(
            plan_id=plan_id.strip(),
            contract_id=contract_id.strip(),
            revision=revision if revision is not None else -1,
        )

    @property
    def pinned(self) -> bool:
        """Whether this names an exact revision (``@N``) rather than
        "whatever is currently stored"."""
        return self.revision >= 1


def check_contract_ref(state_root: Path, ref: ContractRef) -> list[str]:
    """Problems that refuse a spawn. Empty means the worker may run: the
    contract exists, the revision is current, the plan has not moved on
    since approval, and the recorded grant still covers the content."""
    from awino.controller import PlanController, PlanNotFound

    try:
        contract = load_contract(state_root, ref.plan_id, ref.contract_id)
    except ContractNotFound as exc:
        return [str(exc)]
    problems: list[str] = []
    if contract.contract_revision != ref.revision:
        problems.append(
            f"stale contract revision: worker references "
            f"{ref.contract_id!r} r{ref.revision}, current is "
            f"r{contract.contract_revision}; rebuild the assignment from "
            "the current contract"
        )
    try:
        controller = PlanController.load(state_root, ref.plan_id)
    except PlanNotFound:
        return [*problems, f"plan {ref.plan_id!r} not found under {state_root}"]
    if controller.state.status == "closed":
        problems.append(f"plan {ref.plan_id!r} is closed")
    if contract.state != APPROVED:
        # Staleness and binding only mean something for an approved
        # contract; a draft is refused as unapproved, plainly.
        problems.append(
            f"contract {ref.contract_id!r} r{contract.contract_revision} is "
            f"{contract.state}, not approved; review the prefilled brief "
            "and approve before dispatch"
        )
        return problems
    try:
        contract.assert_current(controller.state.plan_revision)
    except StaleContract as exc:
        problems.append(str(exc))
    if not contract.approval_covers():
        problems.append(
            f"contract {ref.contract_id!r} r{contract.contract_revision} was "
            "edited after its approval was granted; re-approve the current "
            "revision before dispatch"
        )
    return problems


def render_contract_block(contract: TaskContract) -> str:
    """The authoritative header appended to a spawned worker's prompt: the
    worker reads the contract file as the single source, not a copy."""
    if contract.approval:
        approval_line = (
            f"approval: granted by {contract.approval['by']} at {contract.approval['at']} "
            f"for r{contract.approval['contract_revision']} "
            f"(hash {contract.approval['revision_hash']})"
        )
    else:
        approval_line = "approval: none recorded -- do not execute; stop and report"
    return (
        "## Task contract (authoritative)\n\n"
        f"contract: {contract.contract_id}  revision: r{contract.contract_revision}  "
        f"brief: {contract.brief_type}  plan: {contract.plan_id} "
        f"(plan r{contract.plan_revision_seen})\n"
        f"{approval_line}\n\n"
        "This assignment is bound to that contract revision. The contract "
        "file is the single source; if it disagrees with anything below, "
        "the contract wins and you stop and report."
    )


# ── the documented template ─────────────────────────────────────────────


def _template_path() -> Path:
    """Filesystem location of the task-contract template.

    Works in a source checkout and in an installed wheel, where the
    template ships under ``awino/_bundle/templates``."""
    from awino.paths import AwinoPaths

    return AwinoPaths.discover().task_contract_template


def load_template() -> dict[str, Any]:
    """Parse the documented template. The yaml documents; the dataclass
    enforces; a test keeps the two in sync."""
    path = _template_path()
    if not path.is_file():
        raise ContractError(f"contract template missing: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
