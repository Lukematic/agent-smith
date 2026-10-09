"""Deep Plan: think it through, get grilled, break it down, then hand the
harness a plan it can hold the work to.

    frame -> grill -> directions -> recon -> slices -> redteam -> plan

Each document stage is a Markdown file with a structural check, like Brain
mode. The grill is a question log the engine keeps: one open question at a
time, each with the model's recommended answer and why it matters, answered
in the human's words (or taken from the code with file:line evidence). The
checks have teeth where a plan usually lies: referenced files must exist,
every slice names a command that runs on this machine, and every done
criterion is covered by a slice.

`compile` turns the session into a plan in the RPI format the harness
already validates (decisions, phases with checkboxes and a success command,
scope, tests, rollback, acceptance criteria). `go` opens a gated run bound to
that plan's exact bytes and scope, with the human's approval, so execution is
held to what was agreed.

Sessions live in `thoughts/plans/<id>/` (meant to be read and committed);
the active pointer lives in the project's state root.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from awino.deepplan_stages import STAGE_NAMES, STAGES, Stage, by_name
from awino.heilmeier import command_is_executable
from awino.mdcheck import _blocks, _each_has, _head, _need, _section

__all__ = ["STAGES", "STAGE_NAMES", "PlanError", "Session", "Stage", "by_name"]

PLANS_DIR = Path("thoughts") / "plans"
POINTER_DIR = "deepplan"
ACTIVE_FILE = "ACTIVE"
SESSION_FILE = "session.json"
BRIEF_FILE = "brief.md"
PLAN_FILE = "plan.md"
PROGRESS_FILE = "progress.md"
MIN_ANSWERED = 3
MAX_FILES_PER_SLICE = 5

_CRITERION_RE = re.compile(r"\bC(\d+)\b")
_REF_RE = re.compile(r"(?<![\w/.-])([\w./\\-]+\.[A-Za-z0-9]+):(\d+)")
_BACKTICK_RE = re.compile(r"`([^`]+)`")


class PlanError(Exception):
    """A Deep Plan command that cannot proceed, with the reason to show the human."""


# ── sessions ────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].strip("-") or "plan"


@dataclass
class Session:
    path: Path
    project: Path
    data: dict

    @property
    def id(self) -> str:
        return self.path.name

    @property
    def title(self) -> str:
        return str(self.data.get("title", self.id))

    @property
    def questions(self) -> list[dict]:
        return self.data.setdefault("questions", [])

    def stage_state(self, name: str) -> dict:
        return self.data.setdefault("stages", {}).get(name, {})

    def recorded(self, name: str) -> bool:
        return bool(self.stage_state(name).get("recorded_at"))

    def confirmed(self, name: str) -> bool:
        return bool(self.stage_state(name).get("confirmed_at"))

    def file_for(self, stage: Stage) -> Path:
        return self.path / stage.filename

    def read(self, name: str) -> str:
        f = self.path / by_name(name).filename
        return f.read_text(encoding="utf-8") if f.is_file() else ""

    def open_question(self) -> dict | None:
        return next((q for q in self.questions if q["status"] == "open"), None)

    def next_stage(self) -> Stage | None:
        for stage in STAGES:
            if not self.recorded(stage.name):
                return stage
            if stage.checkpoint and not self.confirmed(stage.name):
                return stage
        return None

    def save(self) -> None:
        (self.path / SESSION_FILE).write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _pointer(state_root: Path) -> Path:
    return state_root / POINTER_DIR / ACTIVE_FILE


def start(state_root: Path, project: Path, title: str, brief: str | None = None) -> Session:
    root = project / PLANS_DIR
    base = f"{datetime.now(UTC).date().isoformat()}-{_slug(title)}"
    path, n = root / base, 2
    while path.exists():
        path, n = root / f"{base}-{n}", n + 1
    path.mkdir(parents=True)
    (path / BRIEF_FILE).write_text(
        brief or f"# {title}\n\nEverything known so far: the ask, links, notes.\n",
        encoding="utf-8",
    )
    session = Session(path, project, {"title": title, "created_at": _now(), "stages": {}})
    session.save()
    pointer = _pointer(state_root)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(path.relative_to(project)), encoding="utf-8")
    return session


def load(project: Path, rel: str) -> Session:
    path = project / rel
    meta = path / SESSION_FILE
    if not meta.is_file():
        raise PlanError(f"no Deep Plan session at {rel}")
    return Session(path, project, json.loads(meta.read_text(encoding="utf-8")))


def active(state_root: Path, project: Path) -> Session | None:
    pointer = _pointer(state_root)
    if not pointer.is_file():
        return None
    try:
        return load(project, pointer.read_text(encoding="utf-8").strip())
    except PlanError:
        return None


def sessions(project: Path) -> list[Session]:
    root = project / PLANS_DIR
    if not root.is_dir():
        return []
    return [
        Session(meta.parent, project, json.loads(meta.read_text(encoding="utf-8")))
        for meta in sorted(root.glob(f"*/{SESSION_FILE}"))
    ]


def use(state_root: Path, project: Path, session_id: str) -> Session:
    session = load(project, str(PLANS_DIR / session_id))
    pointer = _pointer(state_root)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(session.path.relative_to(project)), encoding="utf-8")
    return session


# ── the grill: a question log, one open question at a time ──────────────────


def ask(session: Session, text: str, recommend: str, why: str, *, blocking: bool = True) -> dict:
    if not session.recorded("frame"):
        raise PlanError("record the frame first: the grill sharpens a stated goal")
    pending = session.open_question()
    if pending:
        raise PlanError(
            f"{pending['id']} is still open. One question at a time: record the answer "
            '(awino deepplan answer "<their words>") or defer it first'
        )
    if not text.strip() or not recommend.strip():
        raise PlanError("a question needs the question and your recommended answer")
    q = {
        "id": f"Q{sum(1 for q in session.questions if q.get('by') != 'code') + 1}",
        "text": text.strip(),
        "recommend": recommend.strip(),
        "why": why.strip(),
        "blocking": blocking,
        "status": "open",
        "asked_at": _now(),
    }
    session.questions.append(q)
    session.save()
    return q


def answer(session: Session, text: str = "", *, accept: bool = False) -> dict:
    q = session.open_question()
    if q is None:
        raise PlanError("no open question to answer")
    if accept:
        q.update(answer=q["recommend"], by="human (accepted the recommendation)")
    elif text.strip():
        q.update(answer=text.strip(), by="human")
    else:
        raise PlanError('record their words: awino deepplan answer "<their words>" (or --accept)')
    q.update(status="answered", answered_at=_now())
    session.save()
    return q


def defer(session: Session, reason: str) -> dict:
    q = session.open_question()
    if q is None:
        raise PlanError("no open question to defer")
    if not reason.strip():
        raise PlanError("say why it can wait: --reason")
    q.update(status="deferred", answer=f"deferred: {reason.strip()}", answered_at=_now())
    session.save()
    return q


def check_ref(project: Path, ref: str) -> str | None:
    """None when ``path:line`` points at a real line in the project, else why not."""
    m = re.fullmatch(r"(.+):(\d+)", ref.strip())
    if not m:
        return f"{ref!r} is not path:line"
    path = (project / m.group(1).replace("\\", "/")).resolve()
    try:
        path.relative_to(project.resolve())
    except ValueError:
        return f"{m.group(1)} is outside the project"
    if not path.is_file():
        return f"{m.group(1)} does not exist"
    lines = path.read_text(encoding="utf-8", errors="replace").count("\n") + 1
    if not 1 <= int(m.group(2)) <= lines:
        return f"{m.group(1)} has {lines} lines, not {m.group(2)}"
    return None


def learn(session: Session, fact: str, evidence: str) -> dict:
    """A question the code answered, so the human was not asked."""
    if not fact.strip():
        raise PlanError("say what is true")
    problem = check_ref(session.project, evidence)
    if problem:
        raise PlanError(f"evidence must be a real path:line in this project: {problem}")
    q = {
        "id": f"F{sum(1 for q in session.questions if q.get('by') == 'code') + 1}",
        "text": "(answered from the code)",
        "answer": fact.strip(),
        "evidence": evidence.strip(),
        "by": "code",
        "blocking": False,
        "status": "answered",
        "asked_at": _now(),
        "answered_at": _now(),
    }
    session.questions.append(q)
    session.save()
    return q


def finish_grill(session: Session, enough: str = "") -> list[str]:
    problems: list[str] = []
    if not session.recorded("frame"):
        problems.append("record the frame first")
    pending = session.open_question()
    if pending:
        problems.append(f"{pending['id']} is still open: get the answer or defer it")
    by_human = [q for q in session.questions if q.get("by", "").startswith("human")]
    if len(by_human) < MIN_ANSWERED and not enough.strip():
        problems.append(
            f"{len(by_human)} question(s) answered by the human; ask at least {MIN_ANSWERED}, "
            'or finish with --enough "<why fewer is right here>"'
        )
    if problems:
        return problems
    state = session.data.setdefault("stages", {}).setdefault("grill", {})
    state.update(recorded_at=_now(), file=None)
    if enough.strip():
        state["enough"] = enough.strip()
    session.save()
    return []


def grill_record(session: Session) -> str:
    rows = ["| # | Question | Answer | By |", "|---|---|---|---|"]
    for q in session.questions:
        answer_text = q.get("answer", "(open)")
        if q.get("evidence"):
            answer_text += f" ({q['evidence']})"
        rows.append(
            f"| {q['id']} | {q['text']} | {answer_text} | {q.get('by', '-')} |".replace("\n", " ")
        )
    return "\n".join(rows)


# ── structural checks per document stage ────────────────────────────────────


def _criteria(frame_text: str) -> list[str]:
    done = _section(frame_text, "done when", "done criteria")
    return sorted({f"C{n}" for n in _CRITERION_RE.findall(done)}, key=lambda c: int(c[1:]))


def _executable_in(text: str) -> list[str]:
    return [c for c in _BACKTICK_RE.findall(text) if command_is_executable(c)]


def _check_frame(text: str) -> list[str]:
    p: list[str] = []
    goal = _need(text, p, "Goal", "goal")
    _need(text, p, "Why", "why")
    done = _need(text, p, "Done when", "done when", "done criteria")
    _need(text, p, "Constraints", "constraint")
    outs = _need(text, p, "Out of scope", "out of scope")
    if goal.strip():
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", goal.strip()) if s.strip()]
        if len(sentences) > 2:
            p.append(f"the goal takes {len(sentences)} sentences; say it in one")
    if done.strip():
        blocks = _blocks(done)
        if len(blocks) < 2:
            p.append(f"{len(blocks)} done criterion; give at least two")
        for block in blocks:
            if not _CRITERION_RE.search(block):
                p.append(f"done criterion '{_head(block)}' has no id (C1:, C2:...)")
    if outs.strip() and not _blocks(outs):
        p.append("'Out of scope' needs at least one bullet")
    return p


def _check_directions(text: str) -> list[str]:
    p: list[str] = []
    variations = _need(text, p, "Variations", "variation")
    directions = _need(text, p, "Directions", "directions")
    _need(text, p, "Recommendation", "recommend")
    if variations.strip():
        blocks = _blocks(variations)
        if len(blocks) < 5:
            p.append(f"{len(blocks)} variation(s); go wider: at least five")
        _each_has(blocks, ("lens",), "variation", p)
    if directions.strip():
        blocks = _blocks(directions)
        if not 2 <= len(blocks) <= 3:
            p.append(f"{len(blocks)} direction(s); narrow to two or three")
        _each_has(blocks, ("value", "hardest part", "assumption"), "direction", p)
    return p


def _check_recon(text: str, project: Path) -> list[str]:
    p: list[str] = []
    code = _need(text, p, "Code that matters", "code that matters", "where it lives")
    _need(text, p, "Reuse", "reuse")
    verify = _need(text, p, "How we verify", "how we verify", "verify")
    risks = _need(text, p, "Risks", "risk")
    if code.strip():
        refs = [f"{m.group(1)}:{m.group(2)}" for m in _REF_RE.finditer(code)]
        good = [r for r in refs if check_ref(project, r) is None]
        for ref in refs:
            problem = check_ref(project, ref)
            if problem:
                p.append(f"reference {ref}: {problem}")
        if len(good) < 3:
            p.append(f"{len(good)} real path:line reference(s); read and cite at least three")
    if verify.strip() and not _executable_in(verify):
        p.append("'How we verify' needs a test command in backticks that runs on this machine")
    if risks.strip() and not _blocks(risks):
        p.append("'Risks' needs at least one bullet")
    return p


def slice_files(text: str) -> list[tuple[str, bool]]:
    """(path, is_new) for every file the slices list, in order, without repeats."""
    seen: dict[str, bool] = {}
    for block in _blocks(_section(text, "slices")):
        for line in block.splitlines():
            if line.strip().lower().lstrip("-* ").startswith("files:"):
                raw = line.split(":", 1)[1]
                for item in raw.split(","):
                    item = item.strip().strip("`")
                    if not item:
                        continue
                    is_new = "(new)" in item.lower()
                    name = re.sub(r"\(new\)", "", item, flags=re.I).strip().strip("`")
                    if name and name not in seen:
                        seen[name] = is_new
    return list(seen.items())


def _check_slices(text: str, project: Path, criteria: list[str]) -> list[str]:
    p: list[str] = []
    body = _need(text, p, "Slices", "slices")
    _need(text, p, "Critical path", "critical path")
    _need(text, p, "Will change", "will change")
    _need(text, p, "Will not change", "will not change")
    if not body.strip():
        return p
    blocks = _blocks(body)
    if not 2 <= len(blocks) <= 12:
        p.append(f"{len(blocks)} slice(s); use two to twelve")
    _each_has(blocks, ("files:", "verify:", "done when:", "covers:"), "slice", p)
    covered: set[str] = set()
    for block in blocks:
        head = _head(block)
        verify_lines = [ln for ln in block.splitlines() if "verify:" in ln.lower()]
        if verify_lines and not _executable_in("\n".join(verify_lines)):
            p.append(f"slice '{head}': 'Verify:' needs a command in backticks that runs here")
        files = [ln for ln in block.splitlines() if "files:" in ln.lower()]
        count = sum(len([f for f in ln.split(":", 1)[1].split(",") if f.strip()]) for ln in files)
        if count > MAX_FILES_PER_SLICE:
            p.append(f"slice '{head}' touches {count} files; split it (at most five)")
        for ln in block.splitlines():
            if "covers:" in ln.lower():
                covered |= {f"C{n}" for n in _CRITERION_RE.findall(ln)}
    for name, is_new in slice_files(text):
        if not is_new and not (project / name).is_file():
            p.append(f"{name} does not exist; fix the path or mark it (new)")
    missing = [c for c in criteria if c not in covered]
    if missing:
        p.append(f"no slice covers {', '.join(missing)}: every done criterion needs one")
    return p


def _check_redteam(text: str) -> list[str]:
    p: list[str] = []
    pre = _need(text, p, "Premortem", "premortem")
    obj = _need(text, p, "Objections", "objection")
    _need(text, p, "Rollback", "rollback")
    if pre.strip():
        blocks = _blocks(pre)
        if len(blocks) < 3:
            p.append(f"{len(blocks)} failure reason(s); find at least three")
        _each_has(blocks, ("warning sign", "mitigation"), "failure", p)
    if obj.strip():
        blocks = _blocks(obj)
        if len(blocks) < 2:
            p.append(f"{len(blocks)} objection(s); raise at least two")
        _each_has(blocks, ("answer",), "objection", p)
    return p


def validate(session: Session, stage: str, text: str) -> list[str]:
    by_name(stage)
    project = session.project
    if stage == "frame":
        return _check_frame(text)
    if stage == "directions":
        return _check_directions(text)
    if stage == "recon":
        return _check_recon(text, project)
    if stage == "slices":
        return _check_slices(text, project, _criteria(session.read("frame")))
    if stage == "redteam":
        return _check_redteam(text)
    raise PlanError("the grill is recorded with ask/answer/learn and finished with grill-done")


def _blockers(session: Session, stage: Stage) -> list[str]:
    for earlier in STAGES[: STAGES.index(stage)]:
        if not session.recorded(earlier.name):
            hint = "awino deepplan grill-done" if earlier.name == "grill" else "record it"
            return [f"'{earlier.name}' comes first ({hint})"]
        if earlier.checkpoint and not session.confirmed(earlier.name):
            return [
                f"'{earlier.name}' is a checkpoint: show it to the human and record their "
                f'answer with  awino deepplan confirm {earlier.name} --note "<their words>"'
            ]
    return []


def record(session: Session, stage_name: str, text: str) -> list[str]:
    stage = by_name(stage_name)
    if stage.name == "grill":
        return ["the grill is a conversation: ask, answer, learn, then awino deepplan grill-done"]
    problems = _blockers(session, stage) or validate(session, stage.name, text)
    if problems:
        return problems
    target = session.file_for(stage)
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")
    state = session.data.setdefault("stages", {}).setdefault(stage.name, {})
    state.update(recorded_at=_now(), file=target.name)
    state.pop("confirmed_at", None)
    session.save()
    return []


def confirm(session: Session, stage_name: str, note: str) -> None:
    stage = by_name(stage_name)
    if not stage.checkpoint:
        raise PlanError(f"'{stage.name}' is not a checkpoint; nothing to confirm")
    if not session.recorded(stage.name):
        raise PlanError(f"record '{stage.name}' before confirming it")
    if not note.strip():
        raise PlanError('record what the human said: --note "<their words>"')
    session.data["stages"][stage.name].update(confirmed_at=_now(), note=note.strip())
    session.save()


# ── the plan the harness executes ───────────────────────────────────────────


def _labeled(block: str, label: str) -> str:
    for line in block.splitlines():
        stripped = line.strip().lstrip("-* ")
        if stripped.lower().startswith(label.lower() + ":"):
            return stripped.split(":", 1)[1].strip()
    return ""


_SLICE_NUMBER_RE = re.compile(r"^(?:slice\s*)?\d+\s*[.:)\-]?\s*", re.I)


def _phase_title(block: str) -> str:
    """A slice heading without its own number: "Slice 2: tokens" -> "tokens"."""
    head = _head(block, 80)
    return _SLICE_NUMBER_RE.sub("", head).strip() or head


def render_plan(session: Session) -> str:
    """plan.md in the RPI plan format, from everything the session recorded."""
    missing = [s.name for s in STAGES if not session.recorded(s.name)]
    if missing:
        raise PlanError(f"not ready to compile: {', '.join(missing)} still to do")
    unconfirmed = [s.name for s in STAGES if s.checkpoint and not session.confirmed(s.name)]
    if unconfirmed:
        raise PlanError(f"checkpoint(s) not confirmed by the human: {', '.join(unconfirmed)}")
    frame, slices, red = session.read("frame"), session.read("slices"), session.read("redteam")
    directions = session.data["stages"].get("directions", {}).get("note", "")
    goal = " ".join(_section(frame, "goal").split())
    out: list[str] = [f"# Plan: {session.title}", ""]
    out += ["## Goal", "", goal, ""]
    out += ["## Source research", "", f"{session.path.relative_to(session.project)}/", ""]
    out += ["- `frame.md`, `directions.md`, `recon.md`, `slices.md`, `redteam.md`", ""]
    out += ["## Decisions made", "", grill_record(session), ""]
    if directions:
        out += [f"Direction chosen by the human: {directions}", ""]
    tests: list[str] = []
    for i, block in enumerate(_blocks(_section(slices, "slices")), 1):
        verify = _executable_in(_labeled(block, "verify")) or [_labeled(block, "verify")]
        tests.append(verify[0])
        out += [f"## Phase {i} — {_phase_title(block)}", ""]
        for name in [f.strip() for f in _labeled(block, "files").split(",") if f.strip()]:
            out.append(f"- [ ] {name}")
        out += [
            "",
            f"Depends on: {_labeled(block, 'depends on') or 'none'}",
            f"Covers: {_labeled(block, 'covers')}",
            f"**Automated success criteria:** `{verify[0]}`",
            f"**Manual verification:** {_labeled(block, 'done when')}",
            "",
        ]
    out += ["## Scope", "", "Will change:", _section(slices, "will change").strip(), ""]
    out += ["Will not change:", _section(slices, "will not change").strip(), ""]
    out += ["## Tests", ""] + [f"- `{t}`" for t in dict.fromkeys(tests)] + [""]
    out += ["## Rollback", "", _section(red, "rollback").strip(), ""]
    out += ["## Risks", "", _section(red, "premortem").strip(), ""]
    out += ["## Acceptance criteria", "", _section(frame, "done when").strip(), ""]
    out += ["## Out of scope", "", _section(frame, "out of scope").strip(), ""]
    return "\n".join(out).rstrip() + "\n"


def compile_plan(session: Session) -> Path:
    """Write plan.md; the human reviews this exact file before `go`."""
    plan = session.path / PLAN_FILE
    plan.write_text(render_plan(session), encoding="utf-8")
    session.data["plan_path"] = str(plan.relative_to(session.project))
    session.save()
    return plan


def approvable_plan(session: Session) -> Path:
    """The compiled plan, if it is still exactly what the session produces.

    The human approves the file they were shown. A plan edited by hand after
    compile, or a stage re-recorded since, must be compiled and shown again.
    """
    plan = session.path / PLAN_FILE
    if not plan.is_file():
        raise PlanError("no plan yet: awino deepplan compile, then show it to the human")
    if plan.read_text(encoding="utf-8") != render_plan(session):
        raise PlanError(
            "plan.md no longer matches the session (edited by hand, or a stage changed): "
            "run awino deepplan compile and show the human the new plan"
        )
    return plan


def phases(session: Session) -> list[tuple[str, str]]:
    """(title, success command) for every phase, in plan order."""
    out: list[tuple[str, str]] = []
    for block in _blocks(_section(session.read("slices"), "slices")):
        verify = _executable_in(_labeled(block, "verify")) or [_labeled(block, "verify")]
        out.append((_phase_title(block), verify[0]))
    return out


_PROGRESS_RE = re.compile(r"^- \[([ xX])\] Phase (\d+):", re.M)


def write_progress(session: Session) -> Path:
    """progress.md: phases are ticked here by `awino deepplan done`, only after the
    phase's own command passed, so plan.md keeps its approved bytes."""
    path = session.path / PROGRESS_FILE
    if not path.is_file():
        lines = [f"# Progress: {session.title}", ""]
        lines += [f"- [ ] Phase {i}: {name}" for i, (name, _cmd) in enumerate(phases(session), 1)]
        lines += ["", "## Follow-ups (found outside the plan's scope)", ""]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def done_phases(session: Session) -> set[int]:
    path = session.path / PROGRESS_FILE
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {int(n) for mark, n in _PROGRESS_RE.findall(text) if mark.lower() == "x"}


def next_phase_command(session: Session, number: int) -> str:
    """The success command for phase ``number``, if it may be verified now."""
    if not session.data.get("approved"):
        raise PlanError('the plan is not approved yet: awino deepplan go --by "<name>" --note ...')
    plan = phases(session)
    if not 1 <= number <= len(plan):
        raise PlanError(f"the plan has phases 1 to {len(plan)}, not {number}")
    done = done_phases(session)
    if number in done:
        raise PlanError(f"phase {number} is already verified")
    earlier = [n for n in range(1, number) if n not in done]
    if earlier:
        raise PlanError(f"phases go in order: verify phase {earlier[0]} first")
    return plan[number - 1][1]


def tick(session: Session, number: int) -> None:
    path = write_progress(session)
    text = path.read_text(encoding="utf-8")
    text = re.sub(rf"^- \[ \] Phase {number}:", f"- [x] Phase {number}:", text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")


def scope(session: Session) -> list[str]:
    """What the gated run may write: every file a slice names, plus this session's
    folder (for progress.md; plan.md itself is held to its approved hash)."""
    files = [name for name, _new in slice_files(session.read("slices"))]
    return [*files, f"{session.path.relative_to(session.project).as_posix()}/"]


def goal(session: Session) -> str:
    return " ".join(_section(session.read("frame"), "goal").split()) or session.title


# ── the gate's side: runs on a Deep Plan go through `go` and verify every phase ─

_OPENING = {"via_go": False}


@contextmanager
def opening_run() -> Iterator[None]:
    """Marks the gate opening that `awino deepplan go` performs."""
    _OPENING["via_go"] = True
    try:
        yield
    finally:
        _OPENING["via_go"] = False


def is_session_plan(plan_path: Path) -> bool:
    return plan_path.name == PLAN_FILE and (plan_path.parent / SESSION_FILE).is_file()


def refuse_hand_open(plan_path: Path) -> str | None:
    """Why a run on this plan may not be opened by hand, or None."""
    if _OPENING["via_go"] or not is_session_plan(plan_path):
        return None
    return (
        "this plan belongs to a Deep Plan session. Open its run with "
        'awino deepplan go --by "<name>" --note "<their words>": that records the '
        "human's approval and binds the phases, which awino deepplan done verifies"
    )


def unverified_phases(plan_path: str | None, run_id: str) -> list[int]:
    """Phases not yet verified for the run `go` opened on this plan; else []."""
    if not plan_path:
        return []
    folder = Path(plan_path).parent
    meta = folder / SESSION_FILE
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if data.get("run_id") != run_id:
        return []
    session = Session(folder, folder, data)
    done = done_phases(session)
    return [i for i in range(1, len(phases(session)) + 1) if i not in done]


# ── what a fresh context must be told ───────────────────────────────────────


def where(state_root: Path, project: Path) -> list[str]:
    """The active Deep Plan and the exact next command, for a fresh context.

    A new chat (or one after compaction) has no memory of the plan. Without
    this, "approved, build it" gets built around the plan instead of through
    it. Empty when there is no session or every phase is verified.
    """
    try:
        session = active(state_root, project)
        if session is None:
            return []
        head = f"DEEP_PLAN  {session.title} ({session.path.relative_to(project).as_posix()})"
        pending = session.open_question()
        if pending:
            return [
                f"{head}: {pending['id']} waits for the human's answer: {pending['text'][:140]}",
                '  record their reply: awino deepplan answer "<their words>" '
                "(load the awino-deepplan skill)",
            ]
        nxt = session.next_stage()
        if nxt is not None:
            if session.recorded(nxt.name) and nxt.checkpoint:
                return [
                    f"{head}: '{nxt.name}' waits for the human's pick",
                    f'  then: awino deepplan confirm {nxt.name} --note "<their words>"',
                ]
            return [
                f"{head}: planning, next stage '{nxt.name}'",
                "  continue with: awino deepplan (load the awino-deepplan skill)",
            ]
        if not session.data.get("approved"):
            if not session.data.get("plan_path"):
                return [f"{head}: every stage recorded; next: awino deepplan compile"]
            return [
                f"{head}: plan compiled, waiting for the human's approval",
                '  on their yes, and only then: awino deepplan go --by "<name>" --note '
                '"<their words>"',
                "  (not awino gate open: go binds the run to this plan and its phases)",
            ]
        run_id = session.data.get("run_id")
        if run_id:
            from awino.enforce import Ledger

            if Ledger(state_root).load(run_id).terminal_state is not None:
                return []  # the run is closed, paused or blocked; the ledger says so
        plan, done = phases(session), done_phases(session)
        remaining = [i for i in range(1, len(plan) + 1) if i not in done]
        if not remaining:
            return []
        n = remaining[0]
        return [
            f"{head}: executing, {len(done)}/{len(plan)} phases verified",
            f"  next: build phase {n} ({plan[n - 1][0]}), then: awino deepplan done {n}",
        ]
    except Exception:  # a status line must never break startup or a hook
        return []
