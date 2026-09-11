"""owns: buddy check

Diagnostic: reports mechanism effectiveness FROM REAL STATE, never from
claims. Every number printed traces to a file this command names: the run
ledger under the project's state root, the stance detectors in
``smith.stance``, the playbook's event order, MISSION.md, and the Seeds
tracker. When a data source does not exist it prints "none found" or
"unmeasured" instead of inventing numbers.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer

from smith import stance
from smith.cli import _echo, _workspace
from smith.enforce import LOOPS, Ledger, Run

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
# Expands the single-case probe pattern in src/smith/exam.py ("stance.detects"
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


def _loop_honesty(ledger: Ledger) -> dict[str, tuple[int, int]]:
    """declared loop label -> (declared count, count with phase evidence).

    Phase evidence is checkpoints or skill-used records on the run: the
    traces a loop actually walked phases leaves behind, as opposed to the
    label chosen at ``gate open``.
    """
    declared: dict[str, int] = {}
    evidenced: dict[str, int] = {}
    for run in _iter_runs(ledger):
        loop = run.loop or "direct"
        declared[loop] = declared.get(loop, 0) + 1
        walked = bool(run.checkpoints) or any(
            event.state == "used" for event in run.skill_events
        )
        if walked:
            evidenced[loop] = evidenced.get(loop, 0) + 1
    return {loop: (declared[loop], evidenced.get(loop, 0)) for loop in declared}


# ── section 3: playbook events ───────────────────────────────────────────────


@dataclass(frozen=True)
class PlaybookEvents:
    task_close: int
    session_end: int | None  # None: no marker is recorded, so it is unmeasured


def _playbook_events(ledger: Ledger) -> PlaybookEvents:
    """How often each playbook order actually fired.

    Honest accounting: ``playbook.run_event`` appends no markers to state (it
    returns text lines the caller prints), so there is no per-firing record.
    The closest real evidence for task-close is runs carrying close markers
    (closed_at set by mark_complete, which is exactly where the task-close
    order fires from ``gate close`` and the stepper's close node). ``best
    --end`` writes no marker at all, so session-end is unmeasured, not zero.
    """
    runs = _iter_runs(ledger)
    return PlaybookEvents(
        task_close=sum(1 for run in runs if run.closed_at is not None),
        session_end=None,
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


# ── the report ───────────────────────────────────────────────────────────────


def _run_report() -> None:
    workspace = _workspace()
    ledger = Ledger(workspace.state_root)
    _echo("BUDDY  mechanism effectiveness from real state")
    _echo(f"  project={workspace.project.name}  state={workspace.state_root}")
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
    _echo("LOOP HONESTY  (declared loop label vs phase evidence: checkpoints or skill-used records)")
    honesty = _loop_honesty(ledger)
    if not honesty:
        _echo(f"  none found (no runs in {ledger.base})")
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

    # 3. playbook events
    _echo("PLAYBOOK EVENTS  (playbook steps append no markers; counted from run close markers)")
    events = _playbook_events(ledger)
    _echo(f"  task-close: {events.task_close} (runs with close markers)")
    if events.session_end is None:
        _echo("  session-end: unmeasured (best --end records no marker)")
    else:
        _echo(f"  session-end: {events.session_end}")
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


@buddy_app.command("check")
def buddy_check() -> None:
    """Print the mechanism-effectiveness report from real project state."""
    _run_report()


@buddy_app.callback(invoke_without_command=True)
def buddy_default(ctx: typer.Context) -> None:
    """Bare `awino buddy` runs the check: the diagnostic is the default."""
    if ctx.invoked_subcommand is None:
        _run_report()
