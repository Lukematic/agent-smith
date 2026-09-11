"""The one front door: bare ``awino`` orients and operates.

The operator reads the whole project state -- mission (+ success criteria),
checklist, seeds, working memory, last session state, open loops with their
SPINE status -- determines the SINGLE next action, and narrates it in plain
language: "here's where we are, here's what's next, here's what I need from
you". It chains the subcommands' underlying functions internally (never
subprocesses); the human never sees subcommand names in the narration unless
a step is theirs to run.

THE PAUSE RULE (exact, enforced by ``_SelfAction`` below):

  The operator may act UNPAUSED only on:
    1. read-only orientation -- every read in ``_read_snapshot``; and
    2. safe internal state writes -- exactly the ``session-start-marker``
       action: one append-only line in the project's own
       ``session_starts.jsonl`` (project-local, reversible, invisible
       outside the project).

  EVERYTHING else pauses for the human:
    - loop work (advance, answer, approve, close), buddy --fix, seed-tracker
      init, seed creation, thinking passes: narrated with the exact command;
      the human (or their agent) runs it. The operator never advances a loop
      itself -- advancing validates artifacts and can execute project
      commands, which is neither read-only nor trivially reversible.
    - destructive or externally-visible actions (deletes, pushes, sends,
      publishes, deploys, or running a project-configured command that does
      any of those, e.g. a Ralph loop's check command): the operator PAUSES
      with an explicit yes/no naming the exact effect, and performs nothing
      until the human answers. A non-interactive run prints the question; it
      never prompts on stdin.

  Structurally: ``_decide`` emits at most one ``_SelfAction``; ``_run_self``
  refuses any action id outside ``_SAFE_SELF_ACTIONS`` and any command text
  matching ``_PAUSE_PATTERNS``. The rule is allowlist-shaped, so a future
  self-action that is not explicitly safe pauses by default.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from awino import (
    heilmeier,
    loops,
    mission,
    onboarding,
    proof,
    seeds,
    session_markers,
    session_state,
    working_memory,
)
from awino.enforce import Ledger, LoopEvent
from awino.paths import Workspace

# ── the pause rule, as data ──────────────────────────────────────────────

#: Self-action ids the operator may perform without asking. Everything else
#: pauses. Keep this to append-only, project-local, reversible writes.
_SAFE_SELF_ACTIONS = frozenset({"session-start-marker"})

#: Command-text patterns that always pause with an explicit yes/no:
#: irreversible deletes and outward-facing effects. Conservative on purpose.
_PAUSE_PATTERNS = (
    r"\brm\s+-[a-z]*r",  # rm -r / rm -rf
    r"\bdel(ete)?\b",  # del / delete
    r"\bdrop\b",  # drop table/database/...
    r"\bdestroy\b",
    r"\buninstall\b",
    r"\bpush\b",  # externally visible: git push et al.
    r"\bpublish\b",
    r"\bdeploy\b",
    r"\bsend\b",
)
_PAUSE_RE = re.compile("|".join(f"(?:{p})" for p in _PAUSE_PATTERNS), re.I)


def _pauses(text: str) -> str | None:
    """The first pause-pattern matched in ``text``, or None.

    Returns the matched wording so the pause can name the exact effect.
    """
    match = _PAUSE_RE.search(text or "")
    return match.group(0) if match else None


@dataclass(frozen=True)
class _SelfAction:
    """One thing the operator itself would do (not narrate for the human)."""

    id: str
    description: str
    command_text: str = ""  # the underlying effect, for the pause scan


def _run_self(action: _SelfAction, state_root: Path) -> str:
    """Perform one self-action under the pause rule; return a narration line.

    Anything outside the safe allowlist, or whose effect matches a pause
    pattern, is refused here -- the caller renders the pause instead.
    """
    if action.id not in _SAFE_SELF_ACTIONS:
        raise _PauseNeeded(action, f"not in the unpaused safe set: {action.id}")
    hit = _pauses(action.command_text)
    if hit:
        raise _PauseNeeded(action, f"destructive/externally-visible effect: {hit!r}")
    if action.id == "session-start-marker":
        path = session_markers.record_session_start(state_root)
        return f"recorded a session-start marker ({path.name})"
    raise _PauseNeeded(action, f"no handler for safe action {action.id!r}")


class _PauseNeeded(Exception):
    """The operator must ask before doing this. Carries the explicit ask."""

    def __init__(self, action: _SelfAction, reason: str) -> None:
        self.action = action
        self.reason = reason
        super().__init__(reason)


# ── state snapshot ───────────────────────────────────────────────────────


@dataclass
class LoopView:
    id: str
    kind: str
    task: str
    phase: str
    locked: bool
    seed_id: str | None
    check_command: str
    spine: list[tuple[str, str, str]] = field(default_factory=list)
    status_next: str = ""
    verdict: str | None = None

    @property
    def is_open(self) -> bool:
        return self.phase != "done"


@dataclass
class Snapshot:
    project_name: str
    today: str
    objective: str
    criteria: list[str]
    missing_mission_fields: list[str]
    checklist_lines: list[str]
    stale_prompt: str | None
    seeds_state: str
    seeds_reason: str
    seeds_open: list[tuple[str, str]] = field(default_factory=list)  # (id, title)
    facts_count: int = 0
    decisions_count: int = 0
    user_model_line: str | None = None
    session_ends: int = 0
    last_session_end: str | None = None
    active_session: str | None = None
    loops: list[LoopView] = field(default_factory=list)
    criterion_judgments: list[tuple[str, str]] = field(default_factory=list)  # (criterion, status)


_DRIVERS = {
    "rpi": loops.RpiDriver,
    "ralph": loops.RalphDriver,
    "delegate": loops.DelegateDriver,
}


def _driver_for(kind: str, project_root: Path, loops_dir: Path) -> loops.LoopDriver:
    # Read-only construction: no ledger, no skill doc needed for
    # spine_status()/status_next(); state files are read, never written.
    return _DRIVERS[kind](project_root=project_root, loops_dir=loops_dir)


def _latest_verdicts(events: list[LoopEvent]) -> dict[str, LoopEvent]:
    latest: dict[str, LoopEvent] = {}
    for event in events:  # loops.jsonl is oldest-first; later wins
        if event.kind == "outcome_verdict":
            latest[event.loop_id] = event
    return latest


def _judge_criteria(
    criteria: list[str], events: list[LoopEvent]
) -> list[tuple[str, str]]:
    """Per-criterion met/unmet/unjudgeable; the latest verdict mentioning a
    criterion wins. Same rule as the stakeholder brief's aggregation."""
    norm = lambda text: re.sub(r"\s+", " ", text).strip().lower()  # noqa: E731
    index = {norm(criterion): criterion for criterion in criteria}
    judgments = dict.fromkeys(criteria, "unjudgeable")
    for event in events:
        if event.kind != "outcome_verdict":
            continue
        parsed = proof.verdict_criteria(event.detail)
        for status in ("met", "unmet", "unjudgeable"):
            for item in parsed[status]:
                criterion = index.get(norm(item))
                if criterion is not None:
                    judgments[criterion] = status
    return [(criterion, judgments[criterion]) for criterion in criteria]


def _stale_checklist_prompt(checklist: working_memory.Checklist) -> str | None:
    """Same staleness rule buddy audits against: CHECKLIST_STALE_DAYS."""
    last = checklist.last_move_at()
    if not checklist.items() or last is None:
        return None
    try:
        stamp = datetime.fromisoformat(last)
    except ValueError:
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


def _read_snapshot(workspace: Workspace) -> Snapshot:
    state_root = workspace.state_root
    project_root = workspace.project.root

    cat = heilmeier.load(state_root)
    objective = (cat.answers.get("objective") or "").strip()
    criteria = heilmeier.success_criteria(cat)

    checklist = working_memory.Checklist(state_root)
    tracker = seeds.Seeds(project_root)
    seeds_state, seeds_reason = tracker.state()
    open_seeds: list[tuple[str, str]] = []
    if seeds_state.usable:
        try:
            open_seeds = [
                (issue.id, issue.title) for issue in tracker.ready(limit=5)
            ]
        except Exception:
            open_seeds = []

    decisions = working_memory.Decisions(state_root)
    live_decisions = [d for d in decisions.entries() if d.superseded_by is None]
    model = working_memory.UserModel.load()

    active = session_state.load(state_root)
    active_line = None
    if active is not None:
        active_line = (
            f"{active.session_id} (started {active.started_at}"
            + (f", run {active.run_id}" if active.run_id else ", no run bound")
            + ")"
        )

    events: list[LoopEvent] = []
    try:
        events = Ledger(state_root).loop_events()
    except Exception:
        events = []
    verdicts = _latest_verdicts(events)

    loop_views: list[LoopView] = []
    loops_dir = state_root / "loops"
    if loops_dir.is_dir():
        for path in sorted(loops_dir.glob("*.json")):
            try:
                state = loops.LoopState.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                kind = loops.kind_of(state.id)
                driver = _driver_for(kind, project_root, loops_dir)
            except (OSError, ValueError, TypeError, loops.LoopError, KeyError):
                continue
            verdict_event = verdicts.get(state.id)
            loop_views.append(
                LoopView(
                    id=state.id,
                    kind=kind,
                    task=state.task,
                    phase=state.phase,
                    locked=state.locked,
                    seed_id=state.seed_id,
                    check_command=getattr(state, "check_command", "") or "",
                    spine=driver.spine_status(state),
                    status_next=driver.status_next(state),
                    verdict=(
                        proof.verdict_word(verdict_event.detail)
                        if verdict_event is not None
                        else None
                    ),
                )
            )

    return Snapshot(
        project_name=project_root.name,
        today=datetime.now(UTC).date().isoformat(),
        objective=objective,
        criteria=criteria,
        missing_mission_fields=heilmeier.missing_mission_fields(cat),
        checklist_lines=checklist.brief_lines(),
        stale_prompt=_stale_checklist_prompt(checklist),
        seeds_state=str(seeds_state),
        seeds_reason=seeds_reason,
        seeds_open=open_seeds,
        facts_count=len(working_memory.Facts(state_root).entries()),
        decisions_count=len(live_decisions),
        user_model_line=working_memory.UserModel.calibration_line(model),
        session_ends=session_markers.count_session_ends(state_root),
        last_session_end=session_markers.last_session_end_time(state_root),
        active_session=active_line,
        loops=loop_views,
        criterion_judgments=_judge_criteria(criteria, events),
    )


# ── the single next action ───────────────────────────────────────────────


@dataclass
class NextAction:
    kind: str  # onboard | resume | nudge | outcome | suggest | paused
    headline: str  # the single next action, plain language
    ask: str  # "here's what I need from you"
    detail: list[str] = field(default_factory=list)
    self_action: _SelfAction | None = None


def _onboard_action(workspace: Workspace) -> NextAction:
    # Reuse the onboarding machinery: draft from repo evidence, then the
    # unresolved frontier one question at a time. The operator narrates;
    # the human's answers arrive via `awino onboard --set`.
    project_root = workspace.project.root
    tracker = seeds.Seeds(project_root)
    found = mission.discover(project_root, tracker=tracker)
    intent = onboarding.load(project_root) or onboarding.seed_from_mission(found)
    questions = onboarding.frontier(intent)
    if questions:
        first = questions[0]
        headline = f"Answer one question: {first.prompt}"
        ask = (
            f"{first.why} Reply with: "
            f"`awino onboard --set {first.key}=...`"
        )
        detail = [
            f"mission draft: {intent.mission or '(unknown)'} (source: {intent.source})",
        ]
        if len(questions) > 1:
            detail.append(
                "then: "
                + ", ".join(q.key for q in questions[1:3])
                + (" ..." if len(questions) > 3 else "")
            )
    else:
        headline = "Confirm the onboarding draft"
        ask = "Reply with: `awino onboard --confirm`"
        detail = [f"mission draft: {intent.mission or '(unknown)'}"]
    detail.append(
        "mission capture includes success criteria: wire each claim to a "
        "verify command (`awino mission --set \"exams=<claim> -> <command>\"`); "
        "the first checklist starts with the first loop or seed"
    )
    return NextAction(kind="onboard", headline=headline, ask=ask, detail=detail)


def _resume_action(view: LoopView) -> NextAction:
    missing = [name for name, status, _ in view.spine if status == "missing"]
    detail = [
        f"loop {view.id} ({view.kind}), phase {view.phase}"
        + (" -- LOCKED" if view.locked else ""),
        f"task: {view.task}",
    ]
    if view.seed_id:
        detail.append(f"seed: {view.seed_id}")
    if missing:
        detail.append("spine steps still missing: " + ", ".join(missing))
    # Continuing a Ralph verify phase would execute the project's own check
    # command -- a configured, arbitrary subprocess. Scan it against the
    # pause rule before anything is narrated as runnable.
    if view.kind == "ralph" and view.phase == "verify" and view.check_command:
        hit = _pauses(view.check_command)
        if hit:
            return NextAction(
                kind="paused",
                headline=(
                    "Continuing this loop would run the project's check "
                    f"command: `{view.check_command}`"
                ),
                ask="That matches the pause rule "
                f"({hit!r} is destructive/externally-visible), so I am not "
                "running it. Say YES to run it yourself, or NO to leave the "
                "loop where it is.",
                detail=detail,
                self_action=_SelfAction(
                    id="continue-loop",
                    description=f"run check command for loop {view.id}",
                    command_text=view.check_command,
                ),
            )
    headline = f"Continue the {view.kind} loop: {view.status_next}"
    ask = (
        "That step is yours -- advancing a loop validates artifacts and can "
        "run project commands, so I don't advance it silently."
    )
    return NextAction(kind="resume", headline=headline, ask=ask, detail=detail)


def _nudge_action(view: LoopView, stale_prompt: str) -> NextAction:
    detail = [
        f"STALE  {stale_prompt}",
        f"loop {view.id} ({view.kind}) is still open at phase {view.phase}",
        f"task: {view.task}",
    ]
    return NextAction(
        kind="nudge",
        headline=f"Unstick the loop: {view.status_next}",
        ask=(
            "Tell me what's blocked (or unblock it), then run the exact "
            "command above -- I won't run loop commands on your behalf."
        ),
        detail=detail,
    )


def _outcome_action(snapshot: Snapshot) -> NextAction:
    detail: list[str] = []
    for view in snapshot.loops:
        if view.verdict is None:
            continue
        word = {"yes": "accomplished", "partial": "partially accomplished"}.get(
            view.verdict, "not accomplished"
        )
        detail.append(f"- {view.task or view.id}: {word} (verdict: {view.verdict})")
    if snapshot.criteria:
        detail.append("success criteria, judged by the outcome verdicts:")
        for criterion, status in snapshot.criterion_judgments:
            detail.append(f"  [{status}] {criterion}")
    else:
        detail.append("no success criteria were on file, so there is nothing to judge against")
    return NextAction(
        kind="outcome",
        headline="Everything is closed -- here is the outcome against the mission",
        ask="Nothing needs doing. Say the word if you want a new loop or a fresh mission.",
        detail=detail,
    )


def _suggest_action(snapshot: Snapshot) -> NextAction:
    if snapshot.seeds_open:
        seed_id, title = snapshot.seeds_open[0]
        return NextAction(
            kind="suggest",
            headline=f"Commit to the next piece of work: '{title}' ({seed_id})",
            ask=(
                "Start it with a loop: "
                f"`awino loop run rpi --task \"{title}\" --seed {seed_id}` "
                "-- or tell me to hold off."
            ),
            detail=[
                f"{len(snapshot.seeds_open)} seed(s) ready; showing the first",
                "no open loops and no verdicts -- the mission is waiting on its first commitment",
            ],
        )
    return NextAction(
        kind="suggest",
        headline="No open work and no commitments yet -- make the first one concrete",
        ask=(
            "Either create the first seed from the mission objective "
            f"(`awino work` shows the tracker state: {snapshot.seeds_state}), "
            "or run a thinking-mode pass on the mission first: "
            "`awino think premortem`."
        ),
        detail=[
            "mission exists, but nothing is in flight and nothing has closed",
        ],
    )


def _decide(snapshot: Snapshot, workspace: Workspace) -> NextAction:
    open_loops = [view for view in snapshot.loops if view.is_open]
    if open_loops:
        loops_dir = workspace.state_root / "loops"
        current: LoopView | None = None
        marker = loops_dir / "current"
        if marker.is_file():
            current_id = marker.read_text(encoding="utf-8").strip()
            current = next((v for v in open_loops if v.id == current_id), None)
        focus = current or open_loops[0]
        if snapshot.stale_prompt is not None:
            return _nudge_action(focus, snapshot.stale_prompt)
        return _resume_action(focus)
    if not snapshot.objective:
        return _onboard_action(workspace)
    if any(view.verdict is not None for view in snapshot.loops):
        return _outcome_action(snapshot)
    return _suggest_action(snapshot)


# ── rendering ────────────────────────────────────────────────────────────


def _render(snapshot: Snapshot, action: NextAction, marker_note: str) -> list[str]:
    lines = [
        f"OPERATOR  {snapshot.project_name}  {snapshot.today}",
        f"Here's where we are: {_where_we_are(snapshot, action)}",
        f"Here's what's next: {action.headline}",
        f"Here's what I need from you: {action.ask}",
        "",
        "MISSION",
    ]
    if snapshot.objective:
        lines.append(f"  Objective: {snapshot.objective}")
    else:
        lines.append("  no mission on file")
    if snapshot.criteria:
        lines.append("  Success criteria:")
        for criterion in snapshot.criteria:
            lines.append(f"    - {criterion}")
    else:
        lines.append("  no success criteria on file")
    if snapshot.missing_mission_fields:
        lines.append(
            "  missing: " + ", ".join(snapshot.missing_mission_fields)
        )
    lines.append("")
    lines.append("CHECKLIST")
    for line in snapshot.checklist_lines:
        lines.append(f"  {line}")
    if snapshot.stale_prompt:
        lines.append(f"  STALE  {snapshot.stale_prompt}")
    lines.append("")
    lines.append("SEEDS")
    lines.append(f"  tracker: {snapshot.seeds_state} ({snapshot.seeds_reason})")
    for seed_id, title in snapshot.seeds_open:
        lines.append(f"  - {seed_id}: {title}")
    lines.append("")
    lines.append("MEMORY")
    lines.append(
        f"  facts: {snapshot.facts_count}, decisions: {snapshot.decisions_count}"
    )
    if snapshot.user_model_line:
        lines.append(f"  user model: {snapshot.user_model_line}")
    else:
        lines.append("  user model: no learned preferences yet")
    lines.append("")
    lines.append("SESSION")
    lines.append(f"  session ends recorded: {snapshot.session_ends}")
    if snapshot.last_session_end:
        lines.append(f"  last session ended: {snapshot.last_session_end}")
    if snapshot.active_session:
        lines.append(f"  active session: {snapshot.active_session}")
    lines.append(f"  {marker_note}")
    lines.append("")
    lines.append("LOOPS")
    if not snapshot.loops:
        lines.append("  none")
    for view in snapshot.loops:
        state_word = "open" if view.is_open else "closed"
        lines.append(
            f"  {view.id} ({view.kind}, {state_word}, phase {view.phase})"
            + (f" -- verdict: {view.verdict}" if view.verdict else "")
        )
        for name, status, artifact in view.spine:
            lines.append(f"    [{status}] {name}: {artifact}")
        lines.append(f"    next: {view.status_next}")
    lines.append("")
    lines.append("NEXT")
    if action.kind == "paused":
        lines.append(f"  PAUSED  {action.headline}")
        for item in action.detail:
            lines.append(f"  {item}")
        lines.append(f"  {action.ask}")
    else:
        lines.append(f"  {action.headline}")
        for item in action.detail:
            lines.append(f"  {item}")
        lines.append(f"  {action.ask}")
    return lines


def _where_we_are(snapshot: Snapshot, action: NextAction) -> str:
    open_loops = [v for v in snapshot.loops if v.is_open]
    if action.kind == "onboard":
        return "a fresh project -- no mission on file yet"
    if action.kind in ("resume", "nudge"):
        kinds = ", ".join(sorted({v.kind for v in open_loops}))
        return f"{len(open_loops)} open loop(s) ({kinds})"
    if action.kind == "outcome":
        return "all loops closed with outcome verdicts"
    if action.kind == "paused":
        return "a loop whose next step is destructive -- paused for your call"
    return "mission on file, nothing in flight, nothing closed yet"


# ── entry point ──────────────────────────────────────────────────────────


def run(workspace: Workspace, echo) -> int:
    """Run the operator: orient, decide the single next action, narrate.

    Returns the process exit code. The only unpaused self-action is the
    session-start marker; a destructive next action pauses with an explicit
    yes/no instead of acting.
    """
    snapshot = _read_snapshot(workspace)
    action = _decide(snapshot, workspace)
    # The one unpaused self-action: the session-start marker. If the decided
    # action itself needs a pause (destructive), _run_self refuses and the
    # pause is rendered -- nothing is performed.
    marker_note = session_markers.record_session_start(workspace.state_root)
    marker_line = f"orientation only; recorded a session-start marker ({marker_note.name})"
    if action.kind == "paused" and action.self_action is not None:
        try:
            _run_self(action.self_action, workspace.state_root)
        except _PauseNeeded as exc:
            action = NextAction(
                kind="paused",
                headline=action.headline,
                ask=action.ask,
                detail=[*action.detail, f"pause rule: {exc.reason}"],
            )
    for line in _render(snapshot, action, marker_line):
        echo(line)
    return 0
