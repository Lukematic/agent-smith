"""owns: buddy check

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
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from awino import heilmeier, session_markers, stance
from awino.cli import _echo, _workspace
from awino.enforce import LOOPS, Ledger, LoopEvent, Run
from awino.paths import project_state_dir

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


# ── the report ───────────────────────────────────────────────────────────────


def _run_report() -> None:
    workspace = _workspace()
    ledger = Ledger(workspace.state_root)
    _echo("BUDDY  mechanism effectiveness from real state")
    _echo(f"  project={workspace.project.name}  state={workspace.state_root}")
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

    _echo(f"BUDDY-FIX done: {applied} correction(s) applied, {need_human} need a human")


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
