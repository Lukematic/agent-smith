"""owns: buddy check, buddy health

Diagnostic: reports mechanism effectiveness FROM REAL STATE, never from
claims. Every number printed traces to a file this command names: the run
ledger under the project's state root, the stance detectors in
``awino.stance``, the playbook's event order, MISSION.md, and the Seeds
tracker. When a data source does not exist it prints "none found" or
"unmeasured" instead of inventing numbers.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from awino import (
    heilmeier,
    hygiene,
    loops,
    session_markers,
    session_state,
    skill_catalog,
    skill_receipts,
    stance,
    working_memory,
)
from awino.cli import _echo, _paths, _skill_catalog, _workspace
from awino.enforce import LOOPS, Ledger, LoopEvent, Run
from awino.paths import Workspace, project_state_dir

# Honest marker detail prefix written by `buddy --fix` for a declared loop
# that was never walked. The event kind stays "loop_started" (a real kind,
# never a new one); the detail carries the note. Loop honesty skips events
# whose detail starts with this prefix: audit notes are not declarations, so
# a marker never inflates a later report's declared counts.
_UNWALKED_MARKER = "buddy --fix: declared but never walked"

buddy_app = typer.Typer(
    # NOTE: deliberately not no_args_is_help=True. Typer intercepts a no-arg
    # invocation before the callback fires when that flag is set (verified:
    # help prints and the process exits 2), which would make bare
    # `awino buddy` unable to print this report. The diagnostic is the default.
    no_args_is_help=False,
    help="Diagnose mechanism effectiveness from real state, not claims.",
)

# ── section 1: stance self-test ──────────────────────────────────────────────
#
# Expands the single-case probe pattern in src/awino/exam.py ("stance.detects"
# fires steel-man on one sample) to every stance. The advisor is the default
# with no trigger words, so detect() returns None for advisor samples: no
# stance fires, the controller stays in advisor.

_STANCE_SAMPLES: tuple[tuple[str, str], ...] = (
    ("teach-back", "teach me how recursion works"),
    ("teach-back", "I don't understand how the ledger stores evidence"),
    ("research-intake", "research the causes of last night's outage"),
    ("research-intake", "look into the new uv resolver"),
    ("first-principles", "break this down from first principles"),
    ("first-principles", "let's rebuild the design from scratch"),
    ("assumption-audit", "so that means the cache is the bottleneck"),
    ("assumption-audit", "which implies we need more capacity"),
    ("steel-man", "I think we should rewrite the loader"),
    ("steel-man", "my plan is to ship on Friday"),
    ("expert", "honestly, is this a good idea?"),
    ("expert", "as a human, how would you handle the outage?"),
    ("advisor", "what is the status of the current run?"),
    ("advisor", "help me choose between the two plans"),
)


@dataclass(frozen=True)
class StanceProbe:
    expected: str
    sample: str
    fired: str | None


def _stance_self_test() -> list[StanceProbe]:
    """Run every stance's detector against its sample prompts."""
    probes: list[StanceProbe] = []
    for expected, sample in _STANCE_SAMPLES:
        found = stance.detect(sample)
        probes.append(StanceProbe(expected, sample, found.name if found else None))
    return probes


# ── section 2: loop honesty ──────────────────────────────────────────────────


def _iter_runs(ledger: Ledger) -> list[Run]:
    """Every run with a run.json, oldest first. Corrupt entries are skipped,
    not fatal: a diagnostic must survive the state it is diagnosing."""
    runs: list[Run] = []
    base = ledger.base
    if not base.is_dir():
        return runs
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not (child / "run.json").is_file():
            continue
        try:
            runs.append(ledger.load(child.name))
        except Exception:
            continue
    return runs


def _run_walked(run: Run) -> bool:
    """Phase evidence on a run: a checkpoint, or a skill actually used."""
    return bool(run.checkpoints) or any(
        event.state == "used" for event in run.skill_events
    )


def _loop_honesty_from_runs(ledger: Ledger) -> dict[str, tuple[int, int]]:
    """declared loop label -> (declared count, count with phase evidence).

    Phase evidence is checkpoints or skill-used records on the run: the
    traces a loop actually walked phases leaves behind, as opposed to the
    label chosen at ``gate open``. Only for runs that predate loop events;
    the loop ledger trail is authoritative when it exists.
    """
    declared: dict[str, int] = {}
    evidenced: dict[str, int] = {}
    for run in _iter_runs(ledger):
        loop = run.loop or "direct"
        declared[loop] = declared.get(loop, 0) + 1
        if _run_walked(run):
            evidenced[loop] = evidenced.get(loop, 0) + 1
    return {loop: (declared[loop], evidenced.get(loop, 0)) for loop in declared}


def _loop_honesty_from_events(events: list[LoopEvent]) -> dict[str, tuple[int, int]]:
    """declared loop kind -> (declared count, count with phase evidence).

    Declared counts come from loop_started events per kind. A loop counts as
    walked when it has an artifact_validated event, or a phase_started event
    for a phase beyond the first one the loop entered: both prove the driver
    actually moved, not just that the loop was started.
    """
    started: dict[str, list[str]] = {}
    walked: dict[str, set[str]] = {}
    first_phase: dict[str, str] = {}
    for event in events:
        if event.detail.startswith(_UNWALKED_MARKER):
            continue  # audit notes from a previous --fix, not loop declarations
        if event.kind == "loop_started":
            ids = started.setdefault(event.loop_kind, [])
            if event.loop_id not in ids:
                ids.append(event.loop_id)
        elif event.kind == "artifact_validated":
            walked.setdefault(event.loop_kind, set()).add(event.loop_id)
        elif event.kind == "phase_started":
            if event.loop_id not in first_phase:
                first_phase[event.loop_id] = event.phase
            elif event.phase != first_phase[event.loop_id]:
                walked.setdefault(event.loop_kind, set()).add(event.loop_id)
    return {
        kind: (len(ids), len(walked.get(kind, ())))
        for kind, ids in started.items()
    }


def _loop_honesty(ledger: Ledger) -> tuple[dict[str, tuple[int, int]], str]:
    """Loop honesty plus a label naming which source the numbers came from.

    The loop event trail (loops.jsonl) is authoritative when it exists; runs
    that predate it fall back to the run-level heuristic.
    """
    events = ledger.loop_events()
    if events:
        return _loop_honesty_from_events(events), "loop ledger events (loops.jsonl)"
    return (
        _loop_honesty_from_runs(ledger),
        "run checkpoints/skill-used (predates loop events)",
    )


# ── skill receipts ───────────────────────────────────────────────────────────
#
# A completed phase must carry a valid skill receipt for every required
# skill (see awino/skill_receipts.py). Buddy audits that trail; buddy --fix
# re-arms the phase -- it never writes a receipt file itself. Forging one
# would make the audit lie, so re-arming sends the loop back to the phase:
# the skill step runs again and the driver's check() writes a fresh receipt.

_DRIVERS: dict[str, type] = {
    "rpi": loops.RpiDriver,
    "ralph": loops.RalphDriver,
    "delegate": loops.DelegateDriver,
}


def _auditable_loops(
    workspace: Workspace, ledger: Ledger
) -> list[tuple[loops.LoopDriver, loops.LoopState]]:
    """(driver, state) for every loop with ledger events and a readable state.

    A diagnostic survives the state it diagnoses: loops with unreadable
    state files or unknown kinds are skipped, never fatal.
    """
    events = ledger.loop_events()
    if not events:
        return []
    skills_dir = _paths().skills
    found: list[tuple[loops.LoopDriver, loops.LoopState]] = []
    seen: set[str] = set()
    for event in events:
        loop_id = event.loop_id
        if loop_id in seen:
            continue
        seen.add(loop_id)
        try:
            kind = loops.kind_of(loop_id)
        except loops.LoopError:
            continue
        driver_cls = _DRIVERS.get(kind)
        if driver_cls is None:
            continue
        state_path = workspace.state_root / "loops" / f"{loop_id}.json"
        if not state_path.is_file():
            continue
        try:
            state = loops.LoopState.from_dict(
                json.loads(state_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError):
            continue
        skill_md = skills_dir / f"awino-{kind}" / "SKILL.md"
        found.append(
            (
                driver_cls(
                    project_root=workspace.project.root,
                    loops_dir=workspace.state_root / "loops",
                    skill_md=skill_md if skill_md.is_file() else None,
                    ledger=ledger,
                    state_root=workspace.state_root,
                ),
                state,
            )
        )
    return found


def _receipt_findings(
    workspace: Workspace, ledger: Ledger
) -> list[skill_receipts.ReceiptFinding]:
    """Completed phases without a valid receipt for each required skill."""
    events = ledger.loop_events()
    findings: list[skill_receipts.ReceiptFinding] = []
    for driver, state in _auditable_loops(workspace, ledger):
        findings.extend(skill_receipts.find_receipt_problems(driver, state, events))
    return findings


def _rearm_receiptless_phases(
    workspace: Workspace, ledger: Ledger
) -> tuple[list[str], list[str]]:
    """Re-arm each loop's earliest receiptless completed phase.

    Returns (rearmed_labels, human_prompts). Re-arming sends the loop back
    to the phase with the driver's reenter_phase (active loops) or
    reopen_phase (done loops, via the dedicated re-open path) -- the skill
    step runs again and the driver's check() writes a fresh receipt when
    the artifact validates. This function never writes a receipt file
    itself: buddy --fix can never forge one.
    """
    events = ledger.loop_events()
    by_loop: dict[str, tuple[loops.LoopDriver, loops.LoopState, list]] = {}
    for driver, state in _auditable_loops(workspace, ledger):
        findings = skill_receipts.find_receipt_problems(driver, state, events)
        if not findings:
            continue
        key = f"{driver.loop_kind}:{state.id}"
        if key in by_loop:
            by_loop[key][2].extend(findings)
        else:
            by_loop[key] = (driver, state, findings)
    rearmed: list[str] = []
    prompts: list[str] = []
    for _key, (driver, state, findings) in sorted(by_loop.items()):
        earliest = min(
            findings, key=lambda f: driver.phase_order.index(f.phase)
        )
        reason = (
            f"buddy --fix: re-running the '{earliest.phase}' skill step "
            f"({earliest.problem})"
        )
        try:
            if state.phase == "done":
                driver.reopen_phase(state, earliest.phase, reason)
            else:
                driver.reenter_phase(state, earliest.phase, reason)
        except loops.LoopError as exc:
            prompts.append(
                f"could not re-arm loop {state.id} phase '{earliest.phase}': {exc}"
            )
            continue
        rearmed.append(
            f"loop {state.id} re-armed at phase '{earliest.phase}': "
            f"rerun the '{earliest.skill}' skill step, then `awino loop next`"
        )
    return rearmed, prompts


# ── section 0: outcome rates ─────────────────────────────────────────────────
#
# The scoreboard. The owner's question is "what did we need to accomplish,
# and did we?"; the sections below measure how honestly the machine ran, not
# whether the work landed. Verdicts come from `awino loop close`, which
# records one outcome_verdict ledger event per loop with a
# "verdict: yes|partial|no" detail.

_VERDICT_RE = re.compile(r"(?:^|;)\s*verdict\s*:\s*(yes|partial|no)\b", re.IGNORECASE)


def _outcome_rates(
    events: list[LoopEvent],
) -> tuple[dict[str, dict[str, int]], list[tuple[str, str]]]:
    """Verdict counts per loop kind, and loops closed without a verdict.

    The latest outcome_verdict event per loop wins (a verdict can be
    re-recorded). Returns (per_kind, unmeasured): per_kind maps loop kind to
    {"yes": n, "partial": n, "no": n}, and unmeasured is a sorted list of
    (loop_id, loop_kind) with a loop_closed event but no verdict.
    """
    latest: dict[str, tuple[str, str]] = {}
    closed: dict[str, str] = {}
    for event in events:
        if event.kind == "loop_closed":
            closed.setdefault(event.loop_id, event.loop_kind)
        elif event.kind == "outcome_verdict":
            match = _VERDICT_RE.search(event.detail or "")
            if match:
                latest[event.loop_id] = (event.loop_kind, match.group(1).lower())
    per_kind: dict[str, dict[str, int]] = {}
    for _loop_id, (kind, verdict) in latest.items():
        counts = per_kind.setdefault(kind, {"yes": 0, "partial": 0, "no": 0})
        counts[verdict] += 1
    unmeasured = sorted(
        (loop_id, kind) for loop_id, kind in closed.items() if loop_id not in latest
    )
    return per_kind, unmeasured


def _counts_line(counts: dict[str, int]) -> str:
    """'N verdicts: a accomplished (x%), b partial (y%), c not (z%)'."""
    total = sum(counts.values())

    def _pct(n: int) -> str:
        return f"{100.0 * n / total:.0f}%" if total else "n/a"

    return (
        f"{total} verdicts: "
        f"{counts['yes']} accomplished ({_pct(counts['yes'])}), "
        f"{counts['partial']} partial ({_pct(counts['partial'])}), "
        f"{counts['no']} not ({_pct(counts['no'])})"
    )


# ── section 3: playbook events ───────────────────────────────────────────────


@dataclass(frozen=True)
class PlaybookEvents:
    task_close: int
    session_end: int  # measured from session_ends.jsonl markers, one per firing


def _playbook_events(ledger: Ledger) -> PlaybookEvents:
    """How often each playbook order actually fired.

    Honest accounting: ``playbook.run_event`` appends no markers to state (it
    returns text lines the caller prints), so there is no per-firing record
    for task-close; the closest real evidence is runs carrying close markers
    (closed_at set by mark_complete, which is exactly where the task-close
    order fires from ``gate close`` and the stepper's close node).
    Session-end firings are counted from the session_ends.jsonl markers that
    ``best --end`` and buddy's --fix catch-up append once per firing.
    """
    runs = _iter_runs(ledger)
    return PlaybookEvents(
        task_close=sum(1 for run in runs if run.closed_at is not None),
        session_end=session_markers.count_session_ends(ledger.state_root),
    )


# ── section 4: mission freshness ─────────────────────────────────────────────


@dataclass(frozen=True)
class MissionFreshness:
    mission_path: Path | None
    days_since_change: int | None
    seeds_closed_since: int | None
    notes: list[str] = field(default_factory=list)


def _file_changed_epoch(path: Path, project_root: Path) -> float | None:
    """Last-change time of a file, preferring git history over mtime.

    ``git log`` is authoritative when the file is tracked; otherwise fall back
    to the filesystem mtime. Returns epoch seconds, or None when the file is
    missing.
    """
    if not path.is_file():
        return None
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        relative = None
    if relative is not None:
        try:
            result = subprocess.run(
                ["git", "-C", str(project_root), "log", "-1", "--format=%ct", "--", str(relative)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip().isdigit():
                return float(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _parse_epoch(value: object) -> float | None:
    """Best-effort parse of an sd-JSONL timestamp field to epoch seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            pass
    return None


def _seeds_closed_since(project_root: Path, since_epoch: float) -> tuple[int | None, str | None]:
    """Count closed Seeds updated at/after ``since_epoch``, reading the
    tracker's issues.jsonl directly (no sd CLI required).

    Returns (count, note); note is set when the count is unmeasurable.
    """
    tracker = project_root / ".seeds" / "issues.jsonl"
    if not tracker.is_file():
        return None, "no .seeds tracker"
    closed = 0
    saw_issues = False
    saw_timestamps = False
    try:
        for line in tracker.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            saw_issues = True
            status = str(record.get("status") or "")
            if status in {"open", "in_progress", ""}:
                continue
            stamp = None
            for key in ("updated", "updated_at", "closed_at", "modified"):
                stamp = _parse_epoch(record.get(key))
                if stamp is not None:
                    break
            if stamp is None:
                continue
            saw_timestamps = True
            if stamp >= since_epoch:
                closed += 1
    except OSError as exc:
        return None, f"could not read tracker: {exc}"
    if not saw_issues:
        return None, "tracker is empty"
    if not saw_timestamps:
        return None, "closed issues carry no usable timestamps"
    return closed, None


def _scaffold_goals(project_root: Path) -> list[str]:
    """Stated goals to scaffold draft criteria from: the human's own words.

    project.yaml `goals:` and open seed titles -- both human-authored. The
    rendered MISSION.md is deliberately excluded: its headings are the
    catechism's questions, not the human's goals, and scaffolding from them
    would put the machine's own questions in the human's mouth.
    """
    goals: list[str] = []
    project_yaml = project_state_dir(project_root) / "project.yaml"
    if project_yaml.is_file():
        try:
            data = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("goals"), list):
            goals.extend(
                str(item).strip() for item in data["goals"] if str(item).strip()
            )
    goals.extend(_open_seed_titles(project_root))
    return goals


def _mission_freshness(
    project_root: Path, state_root: Path, now: float | None = None
) -> MissionFreshness:
    """Days since the mission last changed vs Seeds closed since then."""
    notes: list[str] = []
    now_epoch = now if now is not None else datetime.now(UTC).timestamp()
    mission = state_root / "MISSION.md"
    if not mission.is_file():
        fallback = state_root / "project.yaml"
        if fallback.is_file():
            mission = fallback
            notes.append("no MISSION.md; using the mission: line in project.yaml")
        else:
            return MissionFreshness(None, None, None, ["no mission found"])
    changed = _file_changed_epoch(mission, project_root)
    if changed is None:
        return MissionFreshness(mission, None, None, [*notes, "mission mtime unreadable"])
    days = max(0, int((now_epoch - changed) // 86400))
    closed, note = _seeds_closed_since(project_root, changed)
    if note:
        notes.append(note)
    return MissionFreshness(mission, days, closed, notes)


# ── section 6: working memory ────────────────────────────────────────────────
#
# Buddy as auditor: the checklist, facts, decisions, and user model are the
# mind to the ledger's court record, and stale-everything is a finding, not
# silent rot. Each finding names the exact state it came from.

_MEMORY_WEEK_SECONDS = 7 * 86400

_ANSWERED_RE = re.compile(r"^question=(\S+)\s+kind=(\S+)\s+by=(\S+):\s*(.*)$", re.S)
_APPROVAL_RE = re.compile(r"^by=(\S+?)(?:\s+reason=(.*))?$", re.S)


def _recent_loop_events(events: list[LoopEvent]) -> list[LoopEvent]:
    """Loop events from the last 7 days with a parseable timestamp."""
    now = datetime.now(UTC)
    out: list[LoopEvent] = []
    for event in events:
        at = event.at
        try:
            stamp = datetime.fromisoformat(at) if isinstance(at, str) else None
        except ValueError:
            continue
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if (now - stamp).total_seconds() <= _MEMORY_WEEK_SECONDS:
            out.append(event)
    return out


def _decision_key_for_event(event: LoopEvent) -> tuple[str, str] | None:
    """The decisions.md key a decision event should have produced.

    Returns (key, label): the key the drivers record under, and a human
    label for the finding. None when the event kind makes no decision.
    """
    if event.kind == "human_answered":
        match = _ANSWERED_RE.match(event.detail or "")
        if not match:
            return None
        qid = match.group(1)
        return f"{event.loop_id}:{qid}", f"pair-planning {qid} in loop {event.loop_id}"
    if event.kind == "approval_granted":
        return f"{event.loop_id}:approval", f"plan approval in loop {event.loop_id}"
    return None


def _unrecorded_decisions(
    events: list[LoopEvent], decisions: working_memory.Decisions
) -> list[tuple[LoopEvent, str, str]]:
    """Decision events from the last 7 days with no decisions.md entry."""
    out: list[tuple[LoopEvent, str, str]] = []
    for event in _recent_loop_events(events):
        keyed = _decision_key_for_event(event)
        if keyed is None:
            continue
        key, label = keyed
        if decisions.by_key(key) is None:
            out.append((event, key, label))
    return out


def _stale_fact_refs(
    state_root: Path, facts: working_memory.Facts
) -> list[tuple[str, str, str]]:
    """Superseded facts still referenced outside facts.md.

    Returns (old_id, new_id, filename) triples. The correction annotations
    inside facts.md itself are legitimate; references in decisions.md or the
    checklist are suspect and need a human's judgment.
    """
    refs: list[tuple[str, str, str]] = []
    superseded = [(f.id, f.superseded_by) for f in facts.entries() if f.superseded_by]
    if not superseded:
        return refs
    sources = {
        "decisions.md": working_memory.Decisions(state_root).path,
        "checklist.json": working_memory.Checklist(state_root).path,
    }
    for old_id, new_id in superseded:
        for name, path in sources.items():
            if not path.is_file():
                continue
            if re.search(rf"\b{re.escape(old_id)}\b", path.read_text(encoding="utf-8")):
                refs.append((old_id, new_id or "(unknown)", name))
    return refs


def _report_working_memory(
    state_root: Path, events: list[LoopEvent]
) -> None:
    _echo("WORKING MEMORY  (checklist, facts, decisions, user model)")
    # Checklist: the now.
    checklist = working_memory.Checklist(state_root)
    items = checklist.items()
    if not items:
        _echo("  checklist: none found (no loops yet)")
    else:
        focus = checklist.focus()
        if focus is not None:
            _echo(
                f"  checklist focus: {focus['loop_id']} "
                f"(phase {focus.get('phase')}, {focus.get('status')})"
            )
        blocked = checklist.blocked_items()
        for item in blocked:
            _echo(f"  BLOCKED  {item['id']}: {item['loop_id']} -- "
                  f"{item.get('blocker') or '(no reason recorded)'}")
        stale = _stale_checklist_prompt(checklist)
        if stale is not None:
            _echo(f"  STALE  {stale}")
        else:
            last = checklist.last_move_at()
            if last is not None:
                _echo(f"  checklist last moved: {last}")
    # Decisions: the why.
    decisions = working_memory.Decisions(state_root)
    entries = decisions.entries()
    week = [e for e in entries if _entry_within_week(e.at)]
    why_less = decisions.why_less()
    _echo(
        f"  decisions: {len(entries)} recorded "
        f"({len(week)} this week, {len(why_less)} with no why)"
    )
    for entry in why_less:
        _echo(f"  WHY_MISSING  {entry.id} '{entry.decision[:60]}' has no recorded why")
    unrecorded = _unrecorded_decisions(events, decisions)
    if unrecorded:
        _echo(
            f"  UNRECORDED  {len(unrecorded)} decision(s) made this week, "
            "none recorded in decisions.md:"
        )
        for _event, _key, label in unrecorded:
            _echo(f"    - {label}")
    # Facts: the understanding.
    facts = working_memory.Facts(state_root)
    fact_entries = facts.entries()
    superseded = [f for f in fact_entries if f.superseded_by]
    _echo(
        f"  facts: {len(fact_entries)} recorded ({len(superseded)} superseded)"
    )
    for old_id, new_id, name in _stale_fact_refs(state_root, facts):
        _echo(
            f"  STALE_REF  {old_id} superseded by {new_id} but still "
            f"referenced in {name}"
        )
    # User model: the who. Buddy reads it to calibrate, and says so.
    model = working_memory.UserModel.load()
    calibration = working_memory.UserModel.calibration_line(model)
    if calibration:
        _echo(f"  user model: {calibration}")
    else:
        _echo("  user model: no learned preferences yet "
              "(~/.awino/profile.yaml)")


def _entry_within_week(at: str) -> bool:
    stamp = working_memory._parse_iso(at)
    if stamp is None:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (datetime.now(UTC) - stamp).total_seconds() <= _MEMORY_WEEK_SECONDS


# ── section 7: state hygiene ─────────────────────────────────────────────────
#
# "Clean folders" is the operator's tidiness duty. The state dir accumulates:
# session files for sessions long over (state_root/session/), loop state
# files the ledger trail never mentions (orphaned) or that fail to parse
# (partial), temp/leftover files, and duplicate session-end markers. The
# report flags each; --fix archives stale sessions (never deletes them --
# nothing the ledger references may be destroyed) and tidies unambiguous
# clutter. Anything judgmental becomes a PROMPT, never a silent delete.

_SESSION_STALE_DAYS = 30
_HYGIENE_ARCHIVE = "archive"

_CLUTTER_EXACT_NAMES = frozenset({".DS_Store"})
_CLUTTER_SUFFIXES = (".tmp", ".bak", ".swp", ".orig", ".pyc")


@dataclass(frozen=True)
class HygieneFinding:
    kind: str  # stale_session | orphaned_loop | partial_loop | clutter | duplicate_marker | unexamined_plan
    target: str  # session id, loop id, or file name: what the finding is about
    path: str  # filesystem path ("; "-joined when several)
    detail: str


def _active_session_id(state_root: Path) -> str | None:
    """The session the .active pointer names, or None when unreadable.

    A diagnostic survives the state it diagnoses: a missing or corrupt
    pointer reads as "no active session", never a crash.
    """
    try:
        state = session_state.load(state_root)
    except Exception:
        return None
    return state.session_id if state is not None else None


def _session_groups(state_root: Path) -> dict[str, list[Path]]:
    """session id -> its state/log files, excluding the .active pointer."""
    session_dir = state_root / "session"
    groups: dict[str, list[Path]] = {}
    if not session_dir.is_dir():
        return groups
    for child in sorted(session_dir.iterdir(), key=lambda p: p.name):
        if not child.is_file() or child.name == ".active":
            continue
        stem = child.name.rsplit(".", 1)[0] if "." in child.name else child.name
        groups.setdefault(stem, []).append(child)
    return groups


def _stale_sessions(
    state_root: Path, *, now: float | None = None
) -> list[HygieneFinding]:
    """Sessions neither active nor touched in _SESSION_STALE_DAYS."""
    now_epoch = now if now is not None else datetime.now(UTC).timestamp()
    cutoff = now_epoch - _SESSION_STALE_DAYS * 86400
    active = _active_session_id(state_root)
    out: list[HygieneFinding] = []
    for session_id, files in _session_groups(state_root).items():
        if session_id == active:
            continue
        try:
            newest = max(path.stat().st_mtime for path in files)
        except OSError:
            continue
        if newest < cutoff:
            days = int((now_epoch - newest) // 86400)
            out.append(
                HygieneFinding(
                    kind="stale_session",
                    target=session_id,
                    path="; ".join(str(path) for path in files),
                    detail=(
                        f"{len(files)} file(s), untouched for {days} days "
                        f"(active session: {active or 'none'})"
                    ),
                )
            )
    return out


def _loop_state_findings(
    state_root: Path, events: list[LoopEvent]
) -> list[HygieneFinding]:
    """Loop state files the trail never mentions, or that fail to parse."""
    out: list[HygieneFinding] = []
    loops_dir = state_root / "loops"
    if not loops_dir.is_dir():
        return out
    event_ids = {event.loop_id for event in events}
    for child in sorted(loops_dir.iterdir(), key=lambda p: p.name):
        if not child.is_file() or child.suffix != ".json":
            continue
        loop_id = child.stem
        try:
            state = loops.LoopState.from_dict(
                json.loads(child.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError) as exc:
            out.append(
                HygieneFinding(
                    kind="partial_loop",
                    target=loop_id,
                    path=str(child),
                    detail=f"loop state does not parse: {exc}",
                )
            )
            continue
        if loop_id not in event_ids:
            out.append(
                HygieneFinding(
                    kind="orphaned_loop",
                    target=loop_id,
                    path=str(child),
                    detail="no events for this loop in loops.jsonl",
                )
            )
            continue
        try:
            kind = loops.kind_of(loop_id)
        except loops.LoopError:
            continue
        driver_cls = _DRIVERS.get(kind)
        if driver_cls is None:
            continue
        if state.phase != "done" and state.phase not in driver_cls.phase_order:
            out.append(
                HygieneFinding(
                    kind="partial_loop",
                    target=loop_id,
                    path=str(child),
                    detail=f"invalid phase {state.phase!r}",
                )
            )
    return out


# ── unexamined plans ───────────────────────────────────────────────────
# A plan approved long ago with no thinking-mode output and no waiver never
# got challenged. Buddy flags it and prompts the exact command to run one
# retroactively -- it never invents the thinking itself.

UNEXAMINED_PLAN_AGE_DAYS = 7


def _unexamined_plan_findings(state_root: Path) -> list[HygieneFinding]:
    """RPI loops whose plan was approved long ago with no thinking-mode run
    and no waiver on record."""
    out: list[HygieneFinding] = []
    loops_dir = state_root / "loops"
    if not loops_dir.is_dir():
        return out
    now = datetime.now(UTC).timestamp()
    for child in sorted(loops_dir.iterdir(), key=lambda p: p.name):
        if not child.is_file() or child.suffix != ".json":
            continue
        loop_id = child.stem
        try:
            kind = loops.kind_of(loop_id)
        except loops.LoopError:
            continue
        if kind != "rpi":
            continue
        try:
            state = loops.LoopState.from_dict(
                json.loads(child.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError):
            continue
        plan_approvals = [a for a in state.approvals if a.get("phase") == "plan"]
        if not plan_approvals or state.phase == "done":
            continue
        if state.thinking_runs or state.thinking_waiver is not None:
            continue
        latest = plan_approvals[-1]
        approved_epoch = _parse_epoch(latest.get("at"))
        if approved_epoch is None:
            continue
        age_days = (now - approved_epoch) / 86400
        if age_days < UNEXAMINED_PLAN_AGE_DAYS:
            continue
        out.append(
            HygieneFinding(
                kind="unexamined_plan",
                target=loop_id,
                path=str(child),
                detail=(
                    f"plan approved {str(latest.get('at'))[:10]} "
                    f"({age_days:.0f}d ago) with no thinking-mode output "
                    "and no waiver"
                ),
            )
        )
    return out


def _clutter_files(state_root: Path) -> list[Path]:
    """Temp/leftover files under the state dir. The archive is not clutter."""
    out: list[Path] = []
    for child in sorted(state_root.rglob("*"), key=lambda p: str(p)):
        if not child.is_file():
            continue
        if child.relative_to(state_root).parts[:1] == (_HYGIENE_ARCHIVE,):
            continue
        name = child.name
        if (
            name in _CLUTTER_EXACT_NAMES
            or name.endswith(_CLUTTER_SUFFIXES)
            or name.endswith("~")
        ):
            out.append(child)
    return out


def _ledger_text(ledger: Ledger) -> str:
    """Every ledger-side text that could reference a state file by name."""
    candidates: list[Path] = []
    if ledger.base.is_dir():
        candidates.extend(
            child
            for child in sorted(ledger.base.rglob("*"), key=lambda p: str(p))
            if child.is_file() and child.suffix in {".json", ".jsonl", ".md"}
        )
    for name in (
        "loops.jsonl",
        "heilmeier.json",
        "decisions.md",
        "checklist.json",
        "session_ends.jsonl",
        "project.yaml",
    ):
        candidates.append(ledger.state_root / name)
    loops_dir = ledger.state_root / "loops"
    if loops_dir.is_dir():
        candidates.extend(sorted(loops_dir.glob("*.json"), key=lambda p: p.name))
    chunks: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def _referenced_by_ledger(ledger: Ledger, name: str) -> bool:
    """Whether any ledger text mentions ``name``.

    Substring, deliberately conservative: a temp file whose name appears in
    the ledger is never deleted silently -- the human decides.
    """
    return name in _ledger_text(ledger)


def _duplicate_markers(state_root: Path) -> list[HygieneFinding]:
    """Exact-duplicate session-end marker lines."""
    path = state_root / "session_ends.jsonl"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seen: set[str] = set()
    duplicates = 0
    for line in lines:
        if not line.strip():
            continue
        if line in seen:
            duplicates += 1
        else:
            seen.add(line)
    if not duplicates:
        return []
    return [
        HygieneFinding(
            kind="duplicate_marker",
            target=path.name,
            path=str(path),
            detail=f"{duplicates} duplicate session-end marker line(s)",
        )
    ]


def _hygiene_findings(
    state_root: Path, ledger: Ledger, *, now: float | None = None
) -> list[HygieneFinding]:
    """Every hygiene finding, in fix order: sessions, loops, clutter, markers."""
    findings = _stale_sessions(state_root, now=now)
    findings.extend(_loop_state_findings(state_root, ledger.loop_events()))
    for path in _clutter_files(state_root):
        findings.append(
            HygieneFinding(
                kind="clutter",
                target=path.name,
                path=str(path),
                detail=(
                    "referenced by ledger data -- needs a human"
                    if _referenced_by_ledger(ledger, path.name)
                    else "unreferenced temp/leftover file"
                ),
            )
        )
    findings.extend(_duplicate_markers(state_root))
    return findings


def _report_hygiene(state_root: Path, ledger: Ledger) -> None:
    _echo("STATE HYGIENE  (stale sessions archived, never deleted; clutter tidied)")
    findings = _hygiene_findings(state_root, ledger)
    if not findings:
        _echo("  state dir is clean")
    for finding in findings:
        _echo(f"  {finding.kind.upper()}  {finding.target}: {finding.detail}")
        _echo(f"    at {finding.path}")


def _archive_stale_session(
    state_root: Path, ledger: Ledger, finding: HygieneFinding
) -> str:
    """Move a stale session's files to archive/sessions/ with a ledger note.

    Archive, never delete: the files survive the tidy, and the ledger note
    records where they went. Returns the human summary line.
    """
    dest_dir = state_root / _HYGIENE_ARCHIVE / "sessions"
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for raw in finding.path.split("; "):
        src = Path(raw)
        if not src.is_file():
            continue
        dest = dest_dir / src.name
        if dest.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            dest = dest_dir / f"{src.stem}.{stamp}{src.suffix}"
        shutil.move(str(src), str(dest))
        moved += 1
    ledger.record_loop_event(
        LoopEvent(
            loop_id="hygiene",
            loop_kind="hygiene",
            phase="",
            kind="state_archived",
            at=datetime.now(UTC).isoformat(),
            detail=(
                f"buddy --fix: archived stale session {finding.target} "
                f"({moved} file(s)) to {_HYGIENE_ARCHIVE}/sessions/"
            ),
        )
    )
    return (
        f"archived stale session {finding.target} ({moved} file(s)) "
        f"to {_HYGIENE_ARCHIVE}/sessions/"
    )


def _dedupe_markers(path: Path) -> int:
    """Drop exact-duplicate non-blank marker lines, keeping the first of each.

    Returns the number of lines removed.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    kept: list[str] = []
    for line in lines:
        if line.strip():
            if line in seen:
                continue
            seen.add(line)
        kept.append(line)
    removed = len(lines) - len(kept)
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


# ── the report ───────────────────────────────────────────────────────────────


# ── section 8: repo hygiene ("one clean") ────────────────────────────────────
#
# Dead code, docs coverage, docs drift. Two tiers for dead code: the fast
# static pass (ruff F401) runs in the default report; the deep coverage tier
# runs only under `buddy health --deep` because it executes the whole suite.


def _command_help_entries() -> list[tuple[str, str]]:
    """(command, short help) for every registered command, via click."""
    from typer.main import get_command

    from awino.cli import app as cli_app

    root = get_command(cli_app)
    entries: list[tuple[str, str]] = []

    def walk(node, prefix: str) -> None:
        children = getattr(node, "commands", None)
        if children:
            for name in sorted(children):
                walk(children[name], f"{prefix}{name} ")
            return
        short = ""
        try:
            short = node.get_short_help_str() or ""
        except Exception:
            short = ""
        if not short:
            help_text = getattr(node, "help", "") or ""
            short = help_text.strip().splitlines()[0] if help_text.strip() else ""
        entries.append((prefix.strip(), short))

    walk(root, "")
    return entries


def _report_repo_hygiene(project_root: Path) -> None:
    """Section 8: dead code (fast tier), docs coverage, docs drift."""
    _echo("REPO HYGIENE  (one clean: dead code, docs coverage, docs drift)")
    # Dead code, fast tier: ruff F401 over the project.
    diagnostics = hygiene.ruff_diagnostics(project_root)
    if diagnostics is None:
        _echo("  DEAD CODE (fast): ruff unavailable -- static pass skipped")
    else:
        dead = hygiene.dead_code_from_ruff(diagnostics, project_root)
        if not dead:
            _echo("  DEAD CODE (fast): no unused imports (ruff F401)")
        for finding in dead:
            _echo(f"  DEAD_CODE  {finding.target}: {finding.detail}")
        _echo("  (deep tier: `buddy health --deep` runs the suite under coverage)")
    # Docs coverage: every registered command and every skill documented.
    entries = _command_help_entries()
    names = [name for name, _ in entries]
    mentions = hygiene.doc_mentions(project_root / "docs")
    missing = hygiene.undocumented_commands(names, mentions)
    for command in missing:
        _echo(f"  DOCS_COVERAGE  'awino {command}' has no documentation in docs/")
    catalog = _skill_catalog()
    undocumented_skills = [
        skill.name
        for skill in catalog.skills
        if not skill_catalog.describe(skill).documented
    ]
    for name in undocumented_skills:
        _echo(f"  DOCS_COVERAGE  skill '{name}': SKILL.md lacks purpose/when-to-use")
    if not missing and not undocumented_skills:
        _echo("  DOCS_COVERAGE  every command and skill is documented")
    # Docs drift: generated reference vs live --help, plus dead references.
    for finding in hygiene.reference_drift(entries, project_root / "docs"):
        _echo(f"  DOCS_DRIFT  {finding.target}: {finding.detail}")
    for ref in hygiene.dead_doc_refs(mentions, names):
        _echo(f"  DOCS_DRIFT  docs mention `{ref}` which is not a registered command")
    _echo("")


def _unknown_loop_verdicts(
    state_root: Path, events: list[LoopEvent]
) -> list[LoopEvent]:
    """outcome_verdict events for loops that never existed on the trail.

    A verdict is real when its loop has a state file or at least a
    loop_started event; otherwise it is a hand-edited trail (or a deleted
    loop), and neither buddy's scoreboard nor the brief may present it as a
    deliverable. Each failure names the phantom loop id.
    """
    started = {event.loop_id for event in events if event.kind == "loop_started"}
    out: list[LoopEvent] = []
    for event in events:
        if event.kind != "outcome_verdict":
            continue
        if event.loop_id in started:
            continue
        if (state_root / "loops" / f"{event.loop_id}.json").is_file():
            continue
        out.append(event)
    return out


def _run_report() -> None:
    workspace = _workspace()
    ledger = Ledger(workspace.state_root)
    _echo("BUDDY  mechanism effectiveness from real state")
    _echo(f"  project={workspace.project.name}  state={workspace.state_root}")
    _echo("")

    # -1. ledger integrity: corrupt trail lines are reported with file and
    # line number, never silently skipped; verdicts for phantom loops are
    # named, never counted.
    _echo("LEDGER INTEGRITY  (corruption is reported, never silently skipped)")
    corrupt = ledger.loop_trail_corruption()
    if not corrupt:
        _echo("  trail clean: every loops.jsonl line parses")
    else:
        trail = ledger._loop_events_path()
        for lineno, preview in corrupt:
            _echo(
                f"  CORRUPT  {trail} line {lineno}: invalid loop event "
                f"({preview}) -- restore the line from backup or delete it; "
                "events on other lines are unaffected"
            )
    phantom = _unknown_loop_verdicts(workspace.state_root, ledger.loop_events())
    for event in phantom:
        _echo(
            f"  PHANTOM  outcome_verdict for unknown loop {event.loop_id!r} "
            f"({event.at}): no loop state and no loop_started event -- "
            "not counted in outcome rates"
        )
    _echo("")

    # 0. outcome rates (the headline: outcomes are the scoreboard)
    _echo("OUTCOME RATES  (verdicts from `awino loop close`; outcomes are the scoreboard)")
    per_kind, unmeasured = _outcome_rates(ledger.loop_events())
    if not per_kind:
        _echo("  none found (no outcome_verdict events in loops.jsonl)")
    else:
        total = {"yes": 0, "partial": 0, "no": 0}
        for counts in per_kind.values():
            for verdict, n in counts.items():
                total[verdict] += n
        _echo(f"  project {workspace.project.name}: {_counts_line(total)}")
        order = [loop for loop in LOOPS if loop in per_kind] + sorted(
            set(per_kind) - set(LOOPS)
        )
        for kind in order:
            _echo(f"  {kind}: {_counts_line(per_kind[kind])}")
    for loop_id, kind in unmeasured:
        _echo(f"  UNMEASURED  outcome unmeasured: loop {loop_id} (kind {kind})")
    _echo("")

    # 1. stance self-test
    _echo("STANCE SELF-TEST  (which stance each sample prompt fires)")
    probes = _stance_self_test()
    covered = sorted({probe.expected for probe in probes})
    _echo(f"  stances covered: {len(covered)}/7")
    for probe in probes:
        fired = probe.fired if probe.fired is not None else "none (advisor default)"
        mark = "ok " if (probe.fired or "advisor") == probe.expected else "MISS"
        _echo(f"  [{mark}] {probe.expected:<16} <- {probe.sample!r} -> {fired}")
    misses = [
        probe for probe in probes if (probe.fired or "advisor") != probe.expected
    ]
    if misses:
        _echo(f"  WARNING  {len(misses)} sample(s) did not fire the expected stance")
    _echo("")

    # 2. loop honesty
    _echo("LOOP HONESTY  (declared loop label vs phase evidence)")
    honesty, source = _loop_honesty(ledger)
    _echo(f"  source: {source}")
    if not honesty:
        _echo("  none found (no runs, no loop events)")
    else:
        order = [loop for loop in LOOPS if loop in honesty] + sorted(
            set(honesty) - set(LOOPS)
        )
        for loop in order:
            declared, evidenced = honesty[loop]
            _echo(f"  declared {loop}: {declared}, with phase evidence: {evidenced}")
        gap = sum(declared - evidenced for declared, evidenced in honesty.values())
        if gap:
            _echo(f"  gap: {gap} run(s) declared a loop with no phase evidence")
    _echo("")

    # 2b. skill receipts: a completed phase must carry a valid receipt for
    # every required skill -- the receipt gate's audit trail.
    _echo("SKILL RECEIPTS  (completed phases must carry a valid skill receipt)")
    if not ledger.loop_events():
        _echo("  none found (no loop events)")
    else:
        findings = _receipt_findings(workspace, ledger)
        if not findings:
            _echo("  every completed phase carries a valid skill receipt")
        else:
            for finding in findings:
                _echo(
                    f"  RECEIPT_{finding.status.upper()}  loop {finding.loop_id} "
                    f"phase '{finding.phase}': {finding.problem}"
                )
    _echo("")

    # 3. playbook events
    _echo("PLAYBOOK EVENTS  (task-close from run markers; session-end from session_ends.jsonl)")
    events = _playbook_events(ledger)
    _echo(f"  task-close: {events.task_close} (runs with close markers)")
    _echo(f"  session-end: {events.session_end} (firings recorded in session_ends.jsonl)")
    _echo("")

    # 4. mission freshness
    _echo("MISSION FRESHNESS")
    fresh = _mission_freshness(workspace.project.root, workspace.state_root)
    if fresh.mission_path is None:
        _echo("  none found")
    else:
        _echo(f"  mission: {fresh.mission_path}")
        _echo(f"  days since change: {fresh.days_since_change}")
        if fresh.seeds_closed_since is None:
            _echo("  seeds closed since then: unmeasured")
        else:
            _echo(f"  seeds closed since then: {fresh.seeds_closed_since}")
    for note in fresh.notes:
        _echo(f"  note: {note}")
    _echo("")

    # 5. mission criteria: the mission must be measurable -- objective plus
    # success criteria -- or loop artifacts and verdicts judge against prose.
    _echo(
        "MISSION CRITERIA  (objective + success criteria: "
        "how will we know we've reached the goal vs. not)"
    )
    cat = heilmeier.load(workspace.state_root)
    if not cat.answers:
        _echo("  no mission on file")
    else:
        problems = heilmeier.validate_mission(cat)
        for problem in problems:
            _echo(f"  MISSING  {problem}")
        if not problems:
            _echo(
                f"  objective answered; "
                f"{len(heilmeier.success_criteria(cat))} success criteria on file"
            )
    _echo("")

    # 6. working memory: the checklist (now), facts/decisions (understanding),
    # and the user model (who). Buddy reads these as an auditor.
    _report_working_memory(workspace.state_root, ledger.loop_events())
    _echo("")

    # 7. state hygiene: stale sessions, orphaned/partial loop state, clutter.
    _report_hygiene(workspace.state_root, ledger)
    _echo("")

    # 8. repo hygiene ("one clean"): dead code (fast tier), docs coverage,
    # docs drift. The deep coverage tier is `buddy health --deep`.
    _report_repo_hygiene(workspace.project.root)
    _echo("")

    # 9. unexamined plans: approved long ago with no thinking-mode output
    # and no waiver. Buddy flags them and prompts the exact command to run
    # one retroactively -- it never invents the thinking itself.
    _echo(
        "UNEXAMINED PLANS  (approved long ago, no thinking-mode output, no waiver)"
    )
    unexamined = _unexamined_plan_findings(workspace.state_root)
    if not unexamined:
        _echo("  none found")
    else:
        for finding in unexamined:
            _echo(f"  UNEXAMINED_PLAN  loop {finding.target}: {finding.detail}")
            _echo(
                "  PROMPT  run one retroactively -- the thinking is yours to "
                "do, never the driver's:"
            )
            _echo(
                f"          awino loop think --mode premortem "
                f"--record thoughts/thinking/{finding.target}-premortem.md "
                f"--id {finding.target}"
            )


def _backfill_unrecorded_decisions(
    ledger: Ledger, decisions: working_memory.Decisions
) -> tuple[list[str], list[str]]:
    """Record the decisions.md entries the ledger already saw this week.

    Mechanical, never invented: the ledger event's detail supplies the
    question, kind, by, and text (or the approver and reason). Returns
    (fixed_labels, human_prompts): each prompt is a case where no safe
    mechanical fix exists (a missing why, a misread detail).

    Reusable pure helper so tests assert on findings, not rendered text.
    """
    fixed: list[str] = []
    prompts: list[str] = []
    for event, key, label in _unrecorded_decisions(ledger.loop_events(), decisions):
        detail = event.detail or ""
        if event.kind == "human_answered":
            match = _ANSWERED_RE.match(detail)
            if match is None:
                prompts.append(
                    f"could not parse ledger event for '{label}': record it "
                    f"by hand in decisions.md (key {key})"
                )
                continue
            qid, kind, _by, text = match.groups()
            kind_word = "DEFAULT" if kind == "default" else "ANSWER"
            decisions.record(
                decision=f"{qid} -> {kind_word}: {text}",
                why=text,
                source=(
                    f"backfilled by buddy --fix from ledger human_answered "
                    f"event (loop {event.loop_id})"
                ),
                key=key,
            )
            fixed.append(label)
        elif event.kind == "approval_granted":
            match = _APPROVAL_RE.match(detail)
            if match is None:
                prompts.append(
                    f"could not parse ledger event for '{label}': record it "
                    f"by hand in decisions.md (key {key})"
                )
                continue
            _by, reason = match.groups()
            why = reason.strip() if reason else ""
            decisions.record(
                decision=f"approved loop {event.loop_id} plan",
                why=why or working_memory.WHY_MISSING,
                source=(
                    f"backfilled by buddy --fix from ledger approval_granted "
                    f"event (loop {event.loop_id})"
                ),
                key=key,
            )
            fixed.append(label)
            if not why:
                prompts.append(
                    f"approve with a reason next time: '{label}' has no "
                    f"recorded why; add it in decisions.md ({key})"
                )
    return fixed, prompts


def _stale_checklist_prompt(checklist: working_memory.Checklist) -> str | None:
    """Prompt text when the checklist hasn't moved in CHECKLIST_STALE_DAYS."""
    last = checklist.last_move_at()
    if not checklist.items() or last is None:
        return None
    stamp = working_memory._parse_iso(last)
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    days = int((datetime.now(UTC) - stamp).total_seconds() // 86400)
    if days < working_memory.CHECKLIST_STALE_DAYS:
        return None
    return (
        f"checklist hasn't moved in {days} days (last: {last}) -- archive "
        "the items or confirm the work is still in flight"
    )


# ── --fix: mechanical corrections ────────────────────────────────────────────
#
# The report above is read-only. --fix is the only mutating path: for each
# failing check it applies the correction that needs no judgment, and prints
# exactly one concrete human action where no safe mechanical fix exists.

_FIX_HELP = "Apply the mechanical correction for each failing check."

# stance name -> the exact regex string from src/awino/stance.py _RULES.
# Advisor has no entry: it is the default when no rule fires.
_STANCE_PATTERNS: dict[str, str] = {
    name: pattern.pattern for name, pattern in stance._RULES
}

# The loop driver records the original task on loop_started as "task: ...".
_TASK_IN_DETAIL = re.compile(r"^task:\s*(.+)$", re.S)


def _objective_from_detail(detail: str) -> str | None:
    """The original task from a loop_started detail, if the driver recorded one."""
    match = _TASK_IN_DETAIL.match(detail or "")
    if not match:
        return None
    return match.group(1).strip() or None


def _unwalked_from_events(
    events: list[LoopEvent],
) -> dict[str, list[tuple[str, str | None]]]:
    """loop_kind -> [(loop_id, objective)] for loops declared but never walked.

    Mirrors _loop_honesty_from_events, but keeps the loop ids and the original
    objective (from the loop_started detail) so --fix can mark each one and
    print its exact redo command.
    """
    started: dict[str, list[tuple[str, str | None]]] = {}
    walked: dict[str, set[str]] = {}
    first_phase: dict[str, str] = {}
    for event in events:
        if event.detail.startswith(_UNWALKED_MARKER):
            continue  # audit notes from a previous --fix, not loop declarations
        if event.kind == "loop_started":
            ids = started.setdefault(event.loop_kind, [])
            if all(loop_id != event.loop_id for loop_id, _ in ids):
                ids.append((event.loop_id, _objective_from_detail(event.detail)))
        elif event.kind == "artifact_validated":
            walked.setdefault(event.loop_kind, set()).add(event.loop_id)
        elif event.kind == "phase_started":
            if event.loop_id not in first_phase:
                first_phase[event.loop_id] = event.phase
            elif event.phase != first_phase[event.loop_id]:
                walked.setdefault(event.loop_kind, set()).add(event.loop_id)
    return {
        kind: [
            (loop_id, objective)
            for loop_id, objective in ids
            if loop_id not in walked.get(kind, set())
        ]
        for kind, ids in started.items()
    }


def _mark_unwalked(ledger: Ledger, loop_id: str, loop_kind: str, source: str) -> bool:
    """Record the honest marker for a never-walked loop.

    The marker reuses the loop's own id and the real "loop_started" kind, so
    honesty counting (which dedups loop_started by loop id, and skips
    _UNWALKED_MARKER details) is unchanged by it. Returns True when a new
    marker was written, False when one already existed: --fix is idempotent.
    """
    for event in ledger.loop_events(loop_id):
        if event.detail.startswith(_UNWALKED_MARKER):
            return False
    ledger.record_loop_event(
        LoopEvent(
            loop_id=loop_id,
            loop_kind=loop_kind,
            phase="",
            kind="loop_started",
            at=datetime.now(UTC).isoformat(),
            detail=(
                f"{_UNWALKED_MARKER}: declared via {source} "
                "with no phase evidence; not walked"
            ),
        )
    )
    return True


def _redo_command(loop_kind: str, objective: str | None) -> str:
    """The exact re-run command for a never-walked loop.

    `awino loop run rpi` is the only hand starter in the codebase
    (src/awino/cli/loopctl.py); anything else restarts through the one door.
    """
    task = (objective if objective else "...").replace('"', '\\"')
    if loop_kind == "rpi":
        return f'awino loop run rpi --task "{task}"'
    return f'awino best "{task}"'


def _open_seed_titles(project_root: Path) -> list[str]:
    """Open seed titles, built exactly the way `awino best --end` builds them."""
    from awino import seeds

    tracker = seeds.Seeds(project_root)
    return [i.title for i in tracker.list_open()] if tracker.state()[0].usable else []


def _run_fix() -> None:
    from awino import playbook

    workspace = _workspace()
    project_root = workspace.project.root
    state_root = workspace.state_root
    ledger = Ledger(state_root)
    applied = 0
    need_human = 0

    # Read-only pass first: every diagnosis is computed before anything is
    # mutated, in the same section order as the report.
    probes = _stance_self_test()
    events = ledger.loop_events()
    if events:
        unwalked = _unwalked_from_events(events)
        honesty_source = "loop ledger events (loops.jsonl)"
        gap_runs: list[Run] = []
    else:
        unwalked = {}
        honesty_source = "run checkpoints/skill-used (predates loop events)"
        gap_runs = [run for run in _iter_runs(ledger) if not _run_walked(run)]
    playbook_events = _playbook_events(ledger)
    fresh = _mission_freshness(project_root, state_root)
    open_titles = _open_seed_titles(project_root)

    _echo("BUDDY-FIX  mechanical corrections, in report section order")
    _echo(f"  project={workspace.project.name}  state={state_root}")
    _echo("")

    # 0. outcome rates: a verdict is a human judgment, never auto-invented.
    # For each loop closed without one, print the exact command the human
    # should run.
    _echo("OUTCOME RATES")
    _, unmeasured = _outcome_rates(events)
    if not unmeasured:
        _echo("  every closed loop has a verdict (no correction needed)")
    for loop_id, kind in unmeasured:
        _echo(f"  UNMEASURED  outcome unmeasured: loop {loop_id} (kind {kind})")
        _echo(
            f"  PROMPT  run: awino loop close --id {loop_id} "
            "--verdict yes|partial|no"
        )
        need_human += 1
    _echo("")

    # 1. stance self-test: the code fix stays human, so print the exact
    # failing sample and the exact pattern it was expected to match.
    _echo("STANCE SELF-TEST")
    misses = [probe for probe in probes if (probe.fired or "advisor") != probe.expected]
    if not misses:
        _echo(f"  all {len(probes)} samples fired their stance (no correction needed)")
    for probe in misses:
        regex = _STANCE_PATTERNS.get(probe.expected)
        if regex is None:
            expectation = (
                "no pattern fires "
                "(advisor is the default when no _RULES entry matches)"
            )
        else:
            expectation = f"expected pattern r'{regex}' (src/awino/stance.py _RULES)"
        _echo(f"  MISS {probe.expected} <- {probe.sample!r} : {expectation}")
        _echo(
            f"  ACTION  update the {probe.expected} regex in src/awino/stance.py "
            f"_RULES to match {probe.sample!r}"
        )
        need_human += 1
    _echo("")

    # 2. loop honesty: mark each never-walked loop honestly in the ledger,
    # then print its exact redo command.
    _echo("LOOP HONESTY")
    if events:
        order = [loop for loop in LOOPS if loop in unwalked] + sorted(
            set(unwalked) - set(LOOPS)
        )
        if not any(unwalked.values()):
            _echo("  no declared-but-unwalked loops (no correction needed)")
        for loop_kind in order:
            for loop_id, objective in unwalked[loop_kind]:
                if _mark_unwalked(ledger, loop_id, loop_kind, honesty_source):
                    _echo(f"  FIX recorded honest marker for loop {loop_id}")
                    applied += 1
                else:
                    _echo(f"  already marked: loop {loop_id} (skipped)")
                _echo(f"  REDO  {_redo_command(loop_kind, objective)}")
                if objective is None:
                    _echo('  note: fill in the original task where "..." stands')
    else:
        # Run-level data carries no loop id, so a loop_started marker would be
        # fake data in the authoritative trail: no marker, just the redo.
        if not gap_runs:
            _echo("  no declared-but-unwalked runs (no correction needed)")
        for run in gap_runs:
            loop_kind = run.loop or "direct"
            _echo(
                f"  ACTION  re-run the {loop_kind} loop by hand: "
                f"{_redo_command(loop_kind, run.objective)}"
            )
            _echo(
                "  note: no ledger marker recorded "
                "(run-level data has no loop id; a marker would be fake)"
            )
            need_human += 1
    _echo("")

    # 2b. skill receipts: --fix never forges a receipt. It re-arms the
    # phase's skill step -- the loop goes back to the phase so the work
    # runs again and the driver's check() writes a fresh receipt.
    _echo("SKILL RECEIPTS")
    rearmed, prompts = _rearm_receiptless_phases(workspace, ledger)
    if not rearmed and not prompts:
        _echo("  every completed phase carries a valid skill receipt (no fix needed)")
    for label in rearmed:
        _echo(f"  FIX {label}")
        applied += 1
    for prompt in prompts:
        _echo(f"  ACTION  {prompt}")
        need_human += 1
    _echo("")

    # 3. playbook events: the session-end order fires only via `best --end`
    # or this catch-up, so when no marker exists run the order once now.
    # One-shot catch-up, no looping; the firing is recorded as a marker.
    _echo("PLAYBOOK EVENTS")
    if playbook_events.session_end == 0:
        try:
            lines = playbook.run_event(
                "session-end",
                state_root,
                project_root,
                ledger=ledger,
                open_seeds=open_titles,
            )
        except Exception as exc:
            _echo(f"  session-end catch-up failed: {exc}")
            _echo("  ACTION  run the session-end order by hand: awino best --end")
            need_human += 1
        else:
            for line in lines:
                _echo(f"  FIX {line}")
            marker = session_markers.record_session_end(state_root)
            _echo(f"  FIX SESSION_END_MARKED  {marker}")
            applied += 1
    else:
        _echo("  session-end is measured; no catch-up needed")
    _echo("")

    # 4. mission freshness: refresh the same way the playbook does.
    _echo("MISSION FRESHNESS")
    if fresh.mission_path is None:
        _echo(
            "  ACTION  write the mission line: "
            'awino mission --set "objective=<one sentence>"'
        )
        need_human += 1
    elif fresh.seeds_closed_since is not None and fresh.seeds_closed_since > 0:
        try:
            ctx = playbook.Context(state_root, project_root, ledger, open_titles)
            step_out = playbook._step_mission_refresh(ctx)
        except Exception as exc:
            _echo(f"  mission refresh failed: {exc}")
            _echo(
                "  ACTION  refresh by hand: "
                'awino mission --set "objective=<one sentence>"'
            )
            need_human += 1
        else:
            rendered = (
                step_out[0].removeprefix("MISSION.md refreshed: ").strip()
                if step_out
                else str(state_root / "MISSION.md")
            )
            _echo(f"  FIX mission refreshed: {rendered}")
            applied += 1
    else:
        _echo("  no stale-mission evidence (no correction needed)")
    _echo("")

    # 5. mission criteria: scaffold missing sections from the project's
    # stated goals, marked as draft for human review -- never presented as
    # final. A scaffolded draft has no wired verify commands, so the mission
    # stays invalid until the human finalizes it.
    _echo("MISSION CRITERIA")
    cat = heilmeier.load(state_root)
    missing = heilmeier.missing_mission_fields(cat)
    if not missing:
        _echo("  objective and success criteria on file (no correction needed)")
    else:
        goals = _scaffold_goals(project_root)
        if not goals:
            _echo(
                "  no stated goals to scaffold from; the mission still lacks: "
                + ", ".join(missing)
            )
            _echo(
                '  ACTION  write the mission by hand: '
                'awino mission --set "objective=<one sentence>"'
            )
            # No count change: the freshness section already asked the human.
        else:
            if "objective" in missing:
                cat.answers["objective"] = goals[0]
                cat.source["objective"] = (
                    "draft: scaffolded by buddy --fix from stated goals; "
                    "human review required"
                )
                _echo(
                    f"  FIX scaffolded draft objective from stated goal: "
                    f"'{goals[0]}' (marked for human review)"
                )
            if "success_criteria" in missing:
                drafted = [
                    f"[DRAFT -- human review required] {goal} -- replace "
                    "this line with the claim and the command that verifies "
                    "it, then delete this marker"
                    for goal in goals[:3]
                ]
                existing = cat.answers.get("exams", "").strip()
                cat.answers["exams"] = (
                    (existing + "\n" if existing else "") + "\n".join(drafted)
                )
                cat.source["exams"] = (
                    "draft: scaffolded by buddy --fix from stated goals; "
                    "human review required"
                )
                _echo(
                    f"  FIX scaffolded draft success criteria from "
                    f"{min(len(goals), 3)} stated goal(s) (marked for human "
                    "review; still not measurable until a human wires commands)"
                )
            heilmeier.save(state_root, cat)
            applied += 1
            if "success_criteria" in missing:
                _echo(
                    '  ACTION  finalize the mission: '
                    'awino mission --set "exams=<claim> -> <verify command>"'
                )
                need_human += 1
    _echo("")

    # 6. working memory: mechanical backfills only. A why, a verdict, and a
    # judgment about a stale reference are human; the record of a decision
    # the ledger already saw is mechanical. --fix never invents rationale.
    _echo("WORKING MEMORY")
    decisions = working_memory.Decisions(state_root)
    fixed, prompts = _backfill_unrecorded_decisions(ledger, decisions)
    if not fixed and not prompts:
        _echo("  all this week's ledger decisions are recorded in decisions.md")
    for label in fixed:
        _echo(f"  FIX backfilled decision '{label}' from ledger event")
        applied += 1
    for prompt in prompts:
        _echo(f"  ACTION  {prompt}")
        need_human += 1
    for entry in decisions.why_less():
        _echo(
            f"  PROMPT  decision {entry.id} '{entry.decision[:60]}' has no "
            f"recorded why -- add it in decisions.md (key {entry.key or 'none'})"
        )
        need_human += 1
    facts = working_memory.Facts(state_root)
    for old_id, new_id, name in _stale_fact_refs(state_root, facts):
        _echo(
            f"  PROMPT  {old_id} was superseded by {new_id} but is still "
            f"referenced in {name} -- update the reference or confirm it means "
            "the old entry"
        )
        need_human += 1
    checklist = working_memory.Checklist(state_root)
    stale_prompt = _stale_checklist_prompt(checklist)
    if stale_prompt is not None:
        _echo(f"  PROMPT  {stale_prompt}")
        need_human += 1
    model = working_memory.UserModel.load()
    calibration = working_memory.UserModel.calibration_line(model)
    if calibration:
        _echo(f"  user model: {calibration}")
    _echo("")

    # 7. state hygiene: archive stale sessions, tidy unambiguous clutter.
    # Orphaned/partial loop state is judgmental -- a PROMPT, never a silent
    # delete. Nothing the ledger references is deleted, ever.
    _echo("STATE HYGIENE")
    findings = _hygiene_findings(state_root, ledger)
    if not findings:
        _echo("  state dir is clean (no correction needed)")
    for finding in findings:
        if finding.kind == "stale_session":
            if _referenced_by_ledger(ledger, finding.target):
                _echo(
                    f"  PROMPT  session {finding.target} is stale but the "
                    "ledger references it -- archive it by hand: move "
                    f"{finding.path} to "
                    f"{state_root / _HYGIENE_ARCHIVE / 'sessions'}/"
                )
                need_human += 1
                continue
            _echo(f"  FIX {_archive_stale_session(state_root, ledger, finding)}")
            applied += 1
        elif finding.kind == "clutter":
            if "referenced by ledger" in finding.detail:
                _echo(
                    f"  PROMPT  {finding.path} looks like clutter but is "
                    "referenced by ledger data -- remove it by hand when sure"
                )
                need_human += 1
                continue
            try:
                Path(finding.path).unlink()
            except OSError as exc:
                _echo(f"  could not remove {finding.path}: {exc}")
                need_human += 1
                continue
            _echo(f"  FIX removed clutter {finding.path}")
            applied += 1
        elif finding.kind == "duplicate_marker":
            removed = _dedupe_markers(Path(finding.path))
            _echo(
                f"  FIX removed {removed} duplicate session-end marker line(s) "
                f"from {finding.path}"
            )
            applied += 1
        else:  # orphaned_loop, partial_loop: judgmental, never silent
            _echo(
                f"  PROMPT  {finding.kind} {finding.target}: {finding.detail} -- "
                "confirm the work is abandoned, then move "
                f"{finding.path} to {_HYGIENE_ARCHIVE}/loops/ by hand"
            )
            need_human += 1
    _echo("")

    # 8. repo hygiene: regenerate the command reference from live --help when
    # the drift is one-sided (reference missing or out of sync). The content
    # comes from the code itself, never invented; rows for commands with no
    # help text are marked draft for a human to describe. A project with no
    # docs/ at all is left alone -- creating a docs tree unprompted is
    # presumptuous, and the report's coverage section already names the gap.
    # Dead code is never auto-deleted -- removing code is judgment, so it
    # stays a prompt.
    _echo("REPO HYGIENE")
    entries = _command_help_entries()
    docs_dir = project_root / "docs"
    if not docs_dir.is_dir():
        _echo("  note: no docs/ directory -- command reference not generated")
    else:
        names = [name for name, _ in entries]
        mentions = hygiene.doc_mentions(docs_dir)
        missing = hygiene.undocumented_commands(names, mentions)
        drift = hygiene.reference_drift(entries, docs_dir)
        if not missing and not drift:
            _echo("  command reference is current (no correction needed)")
        else:
            path = hygiene.write_commands_reference(docs_dir, entries)
            _echo(
                f"  FIX regenerated {path} from live --help "
                f"({len(entries)} commands)"
            )
            applied += 1
    diagnostics = hygiene.ruff_diagnostics(project_root)
    if diagnostics is None:
        _echo("  note: ruff unavailable -- dead-code pass skipped")
    else:
        for finding in hygiene.dead_code_from_ruff(diagnostics, project_root):
            _echo(
                f"  PROMPT  {finding.target}: {finding.detail} -- "
                "remove it by hand when sure"
            )
            need_human += 1
    _echo("")

    _echo(f"BUDDY-FIX done: {applied} correction(s) applied, {need_human} need a human")


@buddy_app.command("health")
def buddy_health(
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Run the full suite under coverage and flag unexecuted "
        "modules/functions (slow: minutes, not seconds).",
    ),
) -> None:
    """Repo hygiene on demand: dead code, docs coverage, docs drift.

    The default is the fast tier (static ruff pass, no test run).
    ``--deep`` runs the whole suite under coverage instead.
    """
    project_root = _workspace().project.root
    if not deep:
        _report_repo_hygiene(project_root)
        return
    _echo("BUDDY HEALTH --deep  (coverage tier: the suite runs under coverage)")
    findings, note = hygiene.run_coverage_deep(project_root)
    _echo(f"  {note}")
    if findings is None:
        _echo("  SKIP  deep tier unavailable here")
        return
    if not findings:
        _echo("  every measured module and function was executed by the suite")
    for finding in findings:
        _echo(f"  DEAD_CODE  {finding.target}: {finding.detail}")


@buddy_app.command("check")
def buddy_check(
    fix: bool = typer.Option(False, "--fix", help=_FIX_HELP),
) -> None:
    """Print the mechanism-effectiveness report from real project state."""
    if fix:
        _run_fix()
    else:
        _run_report()


@buddy_app.callback(invoke_without_command=True)
def buddy_default(
    ctx: typer.Context,
    fix: bool = typer.Option(False, "--fix", help=_FIX_HELP),
) -> None:
    """Bare `awino buddy` runs the check: the diagnostic is the default."""
    if ctx.invoked_subcommand is None:
        if fix:
            _run_fix()
        else:
            _run_report()
