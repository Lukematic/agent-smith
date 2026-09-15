"""Journaled ``.smith`` -> ``.awino`` cutover.

The lazy ``migrate_legacy_dir`` rename in ``awino.paths`` is a convenience for
code paths that must keep working on legacy projects. This module is the
explicit, auditable cutover used to retire a ``.smith`` source installation:

- every decision is journaled (JSONL), so an interrupted run resumes instead
  of restarting;
- every byte is hashed (sha256) before and after the copy;
- conflicting state refuses: if ``.awino`` already exists with different
  content, nothing is merged and nothing is deleted;
- the legacy directory is archived with a dated name after the checks pass,
  never unlinked;
- launcher/integration pointers switch last, after launcher, restart, and
  rollback checks pass;
- rollback restores the prior invocation, byte for byte.

Decision boundary (Phase 1): the lazy ``project_state_dir`` path keeps its
existing contract (``.awino`` wins, both preserved, no refusal) because
existing tests pin it. The refusal semantics live here, in the explicit
cutover, where the operator asked for a decision instead of a guess.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from awino.paths import AwinoPaths, IdentityMap, ProjectPaths, Workspace

JOURNAL_NAME = "cutover-journal.jsonl"
ARCHIVE_DIR_NAME = "archive"


class CutoverError(Exception):
    """Base class for cutover failures."""


class CutoverRefused(CutoverError):
    """Conflicting state: ``.awino`` exists with different content.

    Both directories are preserved untouched. The operator must resolve the
    conflict by hand; the cutover will not merge or delete.
    """


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    """sha256 of every file under *root*, keyed by relative posix path."""
    digest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != JOURNAL_NAME:
            digest[path.relative_to(root).as_posix()] = _sha256(path)
    return digest


@dataclass(frozen=True)
class CutoverPlan:
    project: Path
    legacy: Path
    canonical: Path
    archive_dir: Path
    identity: IdentityMap
    inventory: dict[str, str]
    status: str  # "migrate" | "redundant" | "nothing-to-do"


def _identity(project: Path, canonical: Path, home: AwinoPaths | None) -> IdentityMap:
    runtime_home = home.root if home is not None else AwinoPaths.discover(start=project).root
    artifact = runtime_home / "plugin.json"
    return IdentityMap.capture(
        runtime_home=runtime_home,
        target_project=project,
        state_root=canonical,
        plugin_artifact=artifact if artifact.is_file() else None,
    )


def plan_cutover(project: Path, home: AwinoPaths | None = None) -> CutoverPlan:
    """Decide what a cutover would do, without touching anything.

    Raises ``CutoverRefused`` when ``.awino`` exists with content that differs
    from ``.smith``: both are preserved and the operator must reconcile them.
    """
    legacy = project / ".smith"
    canonical = project / ".awino"
    archive_dir = canonical / ARCHIVE_DIR_NAME
    identity = _identity(project, canonical, home)

    if not legacy.exists():
        return CutoverPlan(
            project=project,
            legacy=legacy,
            canonical=canonical,
            archive_dir=archive_dir,
            identity=identity,
            inventory={},
            status="nothing-to-do",
        )
    inventory = snapshot(legacy)
    if canonical.exists():
        existing = snapshot(canonical)
        if existing == inventory:
            return CutoverPlan(
                project=project,
                legacy=legacy,
                canonical=canonical,
                archive_dir=archive_dir,
                identity=identity,
                inventory=inventory,
                status="redundant",
            )
        differing = sorted(
            {
                rel
                for rel in set(inventory) | set(existing)
                if inventory.get(rel) != existing.get(rel)
            }
        )
        raise CutoverRefused(
            f"conflicting state: .awino already exists with different content "
            f"({len(differing)} differing path(s): {', '.join(differing[:8])}"
            f"{'…' if len(differing) > 8 else ''}); both preserved, resolve by hand"
        )
    return CutoverPlan(
        project=project,
        legacy=legacy,
        canonical=canonical,
        archive_dir=archive_dir,
        identity=identity,
        inventory=inventory,
        status="migrate",
    )


def journal_path(plan: CutoverPlan) -> Path:
    return plan.canonical / JOURNAL_NAME


def _read_journal(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _append_journal(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


@dataclass
class CutoverResult:
    copied: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    resumed: bool = False


def execute(plan: CutoverPlan) -> CutoverResult:
    """Copy legacy state into ``.awino``, hashed and journaled.

    Restartable: copy operations already journaled (with a matching hash on
    disk) are verified, not re-copied; anything missing or mismatched is
    copied again. Copy, never move: the legacy directory stays intact until
    ``finalize`` archives it after the checks pass.
    """
    journal = journal_path(plan)
    records = _read_journal(journal)
    if not records:
        _append_journal(
            journal,
            {"op": "plan", "identity": plan.identity.as_dict(), "status": plan.status},
        )
    else:
        _append_journal(journal, {"op": "resume"})

    done = {
        r["rel"]: r["sha256"]
        for r in records
        if r.get("op") == "copy" and r.get("status") in ("ok", "already-present")
    }
    result = CutoverResult(resumed=bool(done))
    for rel in sorted(plan.inventory):
        expected = plan.inventory[rel]
        target = plan.canonical / rel
        if rel in done and target.is_file() and _sha256(target) == expected:
            result.verified.append(rel)
            continue
        if target.is_file() and _sha256(target) == expected and rel not in done:
            # "redundant" cutovers (and re-runs after a journal loss) find the
            # bytes already in place: journal that, copy nothing. Rollback only
            # ever removes files the cutover itself wrote (status "ok"), so
            # pre-existing bytes are never at risk from a rollback.
            _append_journal(
                journal,
                {"op": "copy", "rel": rel, "sha256": expected, "status": "already-present"},
            )
            result.verified.append(rel)
            continue
        source = plan.legacy / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        actual = _sha256(target)
        if actual != expected:
            raise CutoverError(
                f"hash mismatch copying {rel}: expected {expected[:12]}…, got {actual[:12]}…"
            )
        _append_journal(journal, {"op": "copy", "rel": rel, "sha256": actual, "status": "ok"})
        result.copied.append(rel)
        result.verified.append(rel)
    _append_journal(journal, {"op": "execute-complete", "copied": len(result.copied)})
    return result


def verify(plan: CutoverPlan) -> list[str]:
    """Re-hash every migrated file against the plan inventory.

    Returns the list of relative paths whose bytes do not match; empty means
    the migration is byte-identical.
    """
    mismatches = []
    for rel, expected in sorted(plan.inventory.items()):
        target = plan.canonical / rel
        if not target.is_file() or _sha256(target) != expected:
            mismatches.append(rel)
    return mismatches


@dataclass(frozen=True)
class CutoverChecks:
    launcher_ok: bool
    restart_ok: bool
    rollback_ok: bool
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.launcher_ok and self.restart_ok and self.rollback_ok


def _check_launcher_resolution(plan: CutoverPlan) -> tuple[bool, str]:
    """The same resolution launchers and CLI use must land on ``.awino``."""
    resolved = ProjectPaths(root=plan.project).awino_dir
    if resolved != plan.canonical:
        return False, f"ProjectPaths resolves to {resolved}, not {plan.canonical}"
    nested = Workspace.discover(start=plan.project)
    if nested.project.awino_dir != plan.canonical:
        return False, "nested Workspace discovery does not land on the canonical dir"
    return True, "ProjectPaths and Workspace.discover resolve to the canonical .awino"


def _check_restart(plan: CutoverPlan) -> tuple[bool, str]:
    """A fresh reader of the journal must see a complete, verifiable migration.

    Simulates a process restart: re-read the journal from disk and confirm
    every inventory file has a journaled copy op whose hash matches the bytes
    on disk.
    """
    records = _read_journal(journal_path(plan))
    journaled = {
        r["rel"]: r["sha256"] for r in records if r.get("op") == "copy" and r.get("status") == "ok"
    }
    missing = [rel for rel in plan.inventory if rel not in journaled]
    if missing:
        return False, f"journal missing copy ops for: {', '.join(missing[:5])}"
    if verify(plan):
        return False, f"hash mismatches after restart: {', '.join(verify(plan)[:5])}"
    return True, "journal re-read from disk covers the full inventory, hashes match"


def run_checks(plan: CutoverPlan) -> CutoverChecks:
    """Launcher, restart, and rollback checks, in that order.

    The rollback check performs a real rollback in place, verifies the legacy
    directory is byte-identical and the copies are gone, then re-executes the
    migration (which also proves resume-after-rollback). Nothing proceeds to
    ``finalize`` unless all three pass.
    """
    journal = journal_path(plan)
    notes: list[str] = []

    launcher_ok, launcher_note = _check_launcher_resolution(plan)
    notes.append(f"launcher: {launcher_note}")
    if not launcher_ok:
        _append_journal(journal, {"op": "checks", "passed": False, "notes": notes})
        return CutoverChecks(False, False, False, notes)

    restart_ok, restart_note = _check_restart(plan)
    notes.append(f"restart: {restart_note}")
    if not restart_ok:
        _append_journal(journal, {"op": "checks", "passed": False, "notes": notes})
        return CutoverChecks(launcher_ok, False, False, notes)

    # Rollback check: roll back for real, prove the prior invocation is
    # restored, then re-execute (proving resume works after a rollback too).
    rollback(plan)
    legacy_ok = plan.legacy.is_dir() and snapshot(plan.legacy) == plan.inventory
    copies_gone = not any((plan.canonical / rel).exists() for rel in plan.inventory)
    rollback_ok = legacy_ok and copies_gone
    notes.append(
        "rollback: prior invocation restored byte-identical, copies removed"
        if rollback_ok
        else "rollback: FAILED to restore the prior invocation"
    )
    if rollback_ok:
        execute(plan)
        notes.append("rollback: re-executed after rollback, migration complete again")

    checks = CutoverChecks(launcher_ok, restart_ok, rollback_ok, notes)
    _append_journal(
        journal,
        {
            "op": "checks",
            "passed": checks.passed,
            "launcher_ok": launcher_ok,
            "restart_ok": restart_ok,
            "rollback_ok": rollback_ok,
            "notes": notes,
        },
    )
    return checks


def finalize(plan: CutoverPlan, checks: CutoverChecks) -> Path:
    """Archive the legacy directory after all checks pass. Pointers switch last.

    The legacy directory is moved (never deleted) into a dated archive under
    ``.awino/archive/``. Only after the move succeeds is the journal marked
    with the pointer switch, recording the final identity map: anything that
    resolves state must now land on the canonical directory.
    """
    journal = journal_path(plan)
    if not checks.passed:
        raise CutoverError("cannot finalize: launcher/restart/rollback checks did not pass")
    if plan.status == "nothing-to-do" or not plan.legacy.exists():
        _append_journal(journal, {"op": "finalize", "archived": False, "reason": "no legacy dir"})
        _append_journal(journal, {"op": "pointers-switched", "identity": plan.identity.as_dict()})
        return plan.archive_dir

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    plan.archive_dir.mkdir(parents=True, exist_ok=True)
    archived = plan.archive_dir / f".smith-{stamp}"
    suffix = 0
    while archived.exists():
        suffix += 1
        archived = plan.archive_dir / f".smith-{stamp}-{suffix}"
    shutil.move(str(plan.legacy), str(archived))
    _append_journal(
        journal,
        {"op": "archive", "from": ".smith", "to": archived.relative_to(plan.project).as_posix()},
    )

    # Pointers switch last: verify resolution still lands on the canonical
    # directory now that the legacy source is gone, then record the switch.
    ok, note = _check_launcher_resolution(plan)
    if not ok:
        raise CutoverError(f"pointer switch refused: {note}")
    _append_journal(journal, {"op": "pointers-switched", "identity": plan.identity.as_dict()})
    return archived


def rollback(plan: CutoverPlan) -> list[str]:
    """Restore the prior invocation.

    Removes exactly the files the journal says the cutover wrote (status
    "ok"), refusing to remove any whose bytes changed since, so unknown
    local edits are preserved. Restores an archived legacy directory to
    ``.smith`` when the journal shows one was archived. When the canonical
    directory did not exist before the cutover, the cutover's own artifacts
    (journal, empty archive dir) are removed too, so the project is back to
    exactly the prior invocation: ``.smith`` present, ``.awino`` absent. The
    canonical directory is only removed when nothing unknown remains inside
    it; anything the cutover did not create is left alone.

    Returns the list of relative paths removed.
    """
    journal = journal_path(plan)
    records = _read_journal(journal)
    copied = [
        (r["rel"], r["sha256"])
        for r in records
        if r.get("op") == "copy" and r.get("status") == "ok"
    ]
    removed: list[str] = []
    skipped: list[str] = []
    for rel, sha in copied:
        target = plan.canonical / rel
        if not target.is_file():
            continue
        if _sha256(target) != sha:
            # Someone edited the migrated copy after the cutover: preserve it.
            skipped.append(rel)
            continue
        target.unlink()
        removed.append(rel)
    # Prune now-empty parent dirs inside the canonical dir (never the dir itself).
    for rel, _ in sorted(copied, reverse=True):
        parent = (plan.canonical / rel).parent
        while parent != plan.canonical and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    # Restore an archived legacy directory, if the journal shows one.
    archives = [r for r in records if r.get("op") == "archive"]
    restored = False
    if archives and not plan.legacy.exists():
        archived = plan.project / archives[-1]["to"]
        if archived.is_dir():
            shutil.move(str(archived), str(plan.legacy))
            restored = True

    _append_journal(
        journal,
        {"op": "rollback", "removed": removed, "skipped": skipped, "restored_legacy": restored},
    )

    if plan.status == "migrate":
        # The canonical directory is the cutover's own creation: take it back
        # down, but only as far as the cutover built it. Anything unknown
        # (files the inventory never listed) keeps the directory alive.
        if plan.archive_dir.is_dir() and not any(plan.archive_dir.iterdir()):
            plan.archive_dir.rmdir()
        if journal.is_file():
            journal.unlink()
        if plan.canonical.is_dir() and not any(plan.canonical.iterdir()):
            plan.canonical.rmdir()
    return removed
