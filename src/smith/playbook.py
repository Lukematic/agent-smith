"""Playbook: written orders that fire on session events.

The human should run one word, not remember forty commands. `awino best` runs
the `session-start` order; `gate close` fires `task-close`; `awino best --end`
fires `session-end`. The order per event is data, shipped with defaults and
overridable per project in `<state>/playbook.json`, so "what happens when" is a
file you can read and edit rather than a persona's habit.

Two steps carry the teaching methods the human asked for, wired to real work:

- `walkthrough` renders the most recently closed run as a teach-back page -
  what was asked, which files changed, which gates ran and what they proved,
  the lesson recorded - so understanding is offered every time work lands, not
  when someone remembers to ask.
- `grill-offer` derives three questions from that run (the "find my blind
  spots" method) and prints them; answering is the human's move.

`mission-refresh` regenerates MISSION.md from current answers and open Seeds,
which is what makes the Heilmeier document live rather than touched-once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from smith import heilmeier
from smith.enforce import Ledger

DEFAULT_PLAYBOOK: dict[str, list[str]] = {
    "session-start": ["start", "mission-gap", "next-seed"],
    "task-close": ["walkthrough", "grill-offer", "mission-refresh"],
    "session-end": ["summary", "lesson-check", "mission-refresh"],
}

StepFn = Callable[["Context"], list[str]]


class Context:
    def __init__(self, state_root: Path, project: Path, ledger: Ledger, open_seeds: list[str]):
        self.state_root = state_root
        self.project = project
        self.ledger = ledger
        self.open_seeds = open_seeds


def _last_closed_run(ledger: Ledger) -> str | None:
    runs_dir = ledger.state_root / "run"
    if not runs_dir.is_dir():
        return None
    closed: list[tuple[str, str]] = []
    for meta in runs_dir.glob("*/run.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("terminal_state") == "complete" or data.get("closed_at"):
            closed.append((str(data.get("closed_at") or data.get("opened_at")), data["run_id"]))
    if not closed:
        # fall back to the most recent run with any recorded evidence
        latest = sorted(runs_dir.glob("*/run.json"), key=lambda p: p.stat().st_mtime)
        return latest[-1].parent.name if latest else None
    return sorted(closed)[-1][1]


def walkthrough(ledger: Ledger, run_id: str) -> str:
    run = ledger.load(run_id)
    evidence = ledger.evidence(run_id)
    lines = [
        f"# Walkthrough - run {run_id}",
        "",
        f"**Asked:** {run.objective}",
        f"**Task class:** {run.task_class}",
        "",
        "## What changed",
        "",
    ]
    lines += [f"- `{p}`" for p in run.file_scope] or ["- (no files in scope)"]
    lines += ["", "## What was proven", ""]
    seen: set[str] = set()
    for item in evidence:
        if item.gate in seen:
            continue
        seen.add(item.gate)
        verdict = "passed" if item.passed else "FAILED"
        lines.append(f"- **{item.gate}** {verdict}: `{item.command[:90]}`")
    if not evidence:
        lines.append("- (no evidence recorded)")
    lessons = ledger.state_root / "memory" / "lessons.md"
    if lessons.is_file():
        last = [
            ln for ln in lessons.read_text(encoding="utf-8").splitlines() if ln.startswith("- [")
        ]
        if last:
            lines += ["", "## Lesson recorded", "", last[-1]]
    lines += ["", "## Grill me", ""]
    lines += [f"{i}. {q}" for i, q in enumerate(grill_questions(ledger, run_id), 1)]
    return "\n".join(lines) + "\n"


def grill_questions(ledger: Ledger, run_id: str) -> list[str]:
    """Three questions derived from the run - easy to hard - to expose gaps."""
    run = ledger.load(run_id)
    files = run.file_scope or ["the changed code"]
    first = files[0]
    gates = [str(g) for g in run.required] or ["the tests"]
    return [
        f"In one sentence, what was wrong before this change to {first}, and how would you have noticed it?",
        f"Which gate ({', '.join(gates)}) would fail first if this change were reverted, and why that one?",
        "What is the simplest input that would still pass every gate here but be wrong? If you cannot name one, which gate is missing?",
    ]


def _step_start(ctx: Context) -> list[str]:
    inspected = ctx.ledger.inspect_current()
    active = inspected.run_id if inspected.status == "active" else "none"
    return [f"active run: {active}", f"open seeds: {len(ctx.open_seeds)}"]


def _step_mission_gap(ctx: Context) -> list[str]:
    cat = heilmeier.load(ctx.state_root)
    gap = cat.next_gap()
    answered = sum(1 for q in heilmeier.QUESTIONS if cat.answers.get(q.key, "").strip())
    out = [f"heilmeier {answered}/8; exams wired: {len(cat.exam_commands())}"]
    if gap is not None:
        out.append(f"QUESTION  [{gap.key}] {gap.text}  (stance: {gap.stance})")
        out.append(f'          awino mission --set "{gap.key}=<answer>"')
    return out


def _step_next_seed(ctx: Context) -> list[str]:
    return [f"next: {ctx.open_seeds[0]}"] if ctx.open_seeds else ["next: no open seeds"]


def _step_walkthrough(ctx: Context) -> list[str]:
    run_id = _last_closed_run(ctx.ledger)
    if run_id is None:
        return ["no closed run to walk through"]
    path = ctx.state_root / "WALKTHROUGH.md"
    path.write_text(walkthrough(ctx.ledger, run_id), encoding="utf-8")
    return [f"walkthrough written: {path}", f"run: {run_id}"]


def _step_grill_offer(ctx: Context) -> list[str]:
    run_id = _last_closed_run(ctx.ledger)
    if run_id is None:
        return ["no closed run to grill on"]
    return ["Grill me (answer any, I stop you at the first gap):"] + [
        f"  {i}. {q}" for i, q in enumerate(grill_questions(ctx.ledger, run_id), 1)
    ]


def _step_mission_refresh(ctx: Context) -> list[str]:
    cat = heilmeier.load(ctx.state_root)
    path = heilmeier.render(ctx.state_root, cat, open_seeds=ctx.open_seeds)
    return [f"MISSION.md refreshed: {path}"]


def _step_summary(ctx: Context) -> list[str]:
    run_id = _last_closed_run(ctx.ledger)
    return [f"last run: {run_id or 'none'}", f"open seeds: {len(ctx.open_seeds)}"]


def _step_lesson_check(ctx: Context) -> list[str]:
    lessons = ctx.state_root / "memory" / "lessons.md"
    if not lessons.is_file():
        return ["no lessons file yet - if something durable was learned, write it now"]
    count = sum(
        1 for ln in lessons.read_text(encoding="utf-8").splitlines() if ln.startswith("- [")
    )
    return [
        f"lessons recorded: {count}. If nothing durable was learned this sitting, say so explicitly."
    ]


STEPS: dict[str, StepFn] = {
    "start": _step_start,
    "mission-gap": _step_mission_gap,
    "next-seed": _step_next_seed,
    "walkthrough": _step_walkthrough,
    "grill-offer": _step_grill_offer,
    "mission-refresh": _step_mission_refresh,
    "summary": _step_summary,
    "lesson-check": _step_lesson_check,
}


def load_playbook(state_root: Path) -> dict[str, list[str]]:
    book = {k: list(v) for k, v in DEFAULT_PLAYBOOK.items()}
    override = state_root / "playbook.json"
    if override.is_file():
        data = json.loads(override.read_text(encoding="utf-8"))
        for event, steps in data.items():
            for step in steps:
                if step not in STEPS:
                    raise ValueError(f"unknown step {step!r} in playbook event {event!r}")
            book[event] = list(steps)
    return book


def run_event(
    event: str, state_root: Path, project: Path, *, ledger: Ledger, open_seeds: list[str]
) -> list[str]:
    book = load_playbook(state_root)
    if event not in book:
        raise ValueError(f"unknown event {event!r}; known: {', '.join(book)}")
    ctx = Context(state_root, project, ledger, open_seeds)
    out: list[str] = []
    for step in book[event]:
        out.append(f"[{step}]")
        out += [f"  {ln}" for ln in STEPS[step](ctx)]
    return out
