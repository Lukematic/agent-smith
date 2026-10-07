"""Brain mode: one repeatable way to turn a messy problem into a plan you can
explain and a proposal a decision maker can say yes to.

The human dumps everything they have (a sponsor's ask, notes, links, the
project space) and Brain walks seven stages, each a Markdown file with a
structural check, the same contract as ``awino think``:

    problem  -> you -> options -> plan -> report -> notes -> grow

Two stages are checkpoints: after ``problem`` (did we get the problem and the
scope right?) and after ``options`` (which approach?). The next stage refuses
to record until the human's answer is recorded with ``confirm``, so the
pauses that matter cannot be skipped and the rest runs straight through.

The output is two documents: ``report.md`` (two to four pages for a
non-technical reader, plain words, analogies, a clear ask) and
``speaker-notes.md`` (the human's private prep: a 30-second version, a slide
outline with what to say, analogies to keep in your pocket, where the
audience will get lost and what to try then). That is the Feynman standard
made checkable: jargon in the report must be defined in its glossary, the
explanation must carry an analogy, and the notes must plan for confusion.

Two places, deliberately separate:

- Sessions live in the project at ``.awino/brain/<id>/``. The folder ignores
  itself in git, because a sponsor's problem is not something to commit by
  accident.
- The profile (background, strengths, interests, what you want to get better
  at) lives only on this machine at ``~/.awino/brain/me.md``. The ``you``
  stage needs it filled, and ``grow`` appends what each session taught.

The checks are structural, like the stance and think validators: they prove
the required parts exist, not that the thinking is good. The human's
checkpoint answers are what judge that.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from awino.brain_stages import STAGE_NAMES, STAGES, Stage, by_name
from awino.paths import user_config_dir

__all__ = ["STAGES", "STAGE_NAMES", "BrainError", "Session", "Stage", "by_name"]

BRAIN_DIR = "brain"
ACTIVE_FILE = "ACTIVE"
SESSION_FILE = "session.json"
BRIEF_FILE = "brief.md"
PROFILE_FILE = "me.md"
REPORT_WORDS = (400, 2500)  # roughly two to four pages


class BrainError(Exception):
    """A Brain command that cannot proceed, with the reason to show the human."""


# ── structural checks ───────────────────────────────────────────────────────

# Technical terms that make a non-technical reader stop. In the report each
# one used must be defined in the glossary; the one-sentence problem may use
# none. A trailing "*" matches any ending ("fine-tun*" covers fine-tuning).
JARGON: tuple[str, ...] = (
    "llm",
    "large language model",
    "rag",
    "retrieval-augmented",
    "embedding",
    "vector database",
    "fine-tun*",
    "transformer",
    "inference",
    "agentic",
    "orchestrat*",
    "api",
    "mlops",
    "hallucinat*",
    "prompt engineering",
    "neural network",
    "token",
    "classifier",
)
# Words that hide muddled thinking. Banned in the one-sentence problem.
BUZZWORDS: tuple[str, ...] = ("leverage", "synerg*", "paradigm", "game-chang*", "holistic")


def _term_re(term: str) -> re.Pattern[str]:
    if term.endswith("*"):
        return re.compile(rf"\b{re.escape(term[:-1])}\w*", re.I)
    return re.compile(rf"\b{re.escape(term)}s?\b", re.I)


_TERM_RE = {t: _term_re(t) for t in JARGON + BUZZWORDS}


def _label(term: str) -> str:
    return term.rstrip("*")


_ANALOGY_RE = re.compile(r"\b(think of|like a|like an|imagine|similar to|picture|as if)\b", re.I)
_SOURCE_RE = re.compile(r"(https?://|doi\.org|\bsource:|\[inferred\])", re.I)
_TOP_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def _blocks(section: str) -> list[str]:
    """The items of a section: ``###`` sub-sections when it has them, else its
    top-level list items with their indented continuation lines."""
    lines = section.splitlines()
    if any(re.match(r"^#{3,6}\s", ln) for ln in lines):
        out: list[list[str]] = []
        for ln in lines:
            if re.match(r"^#{3,6}\s", ln):
                out.append([ln])
            elif out:
                out[-1].append(ln)
        return ["\n".join(b).strip() for b in out if "\n".join(b).strip()]
    items: list[list[str]] = []
    for ln in lines:
        if _TOP_ITEM_RE.match(ln):
            items.append([ln])
        elif items and ln.strip() and (ln[:1].isspace() or not _TOP_ITEM_RE.match(ln)):
            items[-1].append(ln)
    return ["\n".join(b).strip() for b in items]


def _head(block: str, width: int = 50) -> str:
    first = re.sub(r"^(#+|[-*+]|\d+[.)])\s+", "", block.splitlines()[0].strip())
    return first[:width]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


def _section(text: str, *synonyms: str) -> str:
    """The body under the first heading containing a synonym, up to the next
    heading of the same or higher level. ``##`` headings win over others, so a
    document title like "# Acme's scheduling problem" never swallows the
    whole document as the "problem" section."""
    heads = list(_HEADING_RE.finditer(text))
    wanted = tuple(s.lower() for s in synonyms)
    for prefer in (2, None):
        for i, m in enumerate(heads):
            level = len(m.group(1))
            if prefer is not None and level != prefer:
                continue
            if not any(w in m.group(2).lower() for w in wanted):
                continue
            end = len(text)
            for nxt in heads[i + 1 :]:
                if len(nxt.group(1)) <= level:
                    end = nxt.start()
                    break
            return text[m.end() : end]
    return ""


def jargon_in(text: str, terms: tuple[str, ...] = JARGON) -> list[str]:
    """The terms that appear in ``text``, in list order."""
    return [t for t in terms if _TERM_RE[t].search(text)]


def _need(text: str, problems: list[str], name: str, *synonyms: str) -> str:
    body = _section(text, *synonyms)
    if not body.strip():
        problems.append(f"missing section: '{name}'")
    return body


def _each_has(
    blocks: list[str], labels: tuple[str, ...], what: str, problems: list[str], skip_last: str = ""
) -> None:
    for i, block in enumerate(blocks):
        low = block.lower()
        for label in labels:
            if label == skip_last and i == len(blocks) - 1:
                continue
            if label not in low:
                problems.append(f"{what} '{_head(block)}' has no '{label.capitalize()}:'")


def _check_problem(text: str) -> list[str]:
    p: list[str] = []
    one = _need(text, p, "The problem in one sentence", "one sentence")
    known = _need(text, p, "What we know vs what we are assuming", "what we know", "facts vs")
    _need(text, p, "The assumption to test first", "test first")
    ins = _need(text, p, "In scope", "in scope")
    outs = _need(text, p, "Out of scope", "out of scope")
    _need(text, p, "Questions for the sponsor", "questions for")
    if one.strip():
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", one.strip()) if s.strip()]
        if len(sentences) > 2:
            p.append(f"the problem takes {len(sentences)} sentences; say it in one")
        found = jargon_in(one, JARGON + BUZZWORDS)
        if found:
            words = ", ".join(_label(t) for t in found)
            p.append(f"the one-sentence problem uses jargon ({words}); say it plainly")
    if known.strip():
        if not re.search(r"\bfact\s*:", known, re.I):
            p.append("no 'Fact:' line: what do we actually know, and from where?")
        if not re.search(r"\bassumption\s*:", known, re.I):
            p.append("no 'Assumption:' line: every problem rests on some; name them")
    if ins.strip() and not _blocks(ins):
        p.append("'In scope' needs at least one bullet")
    if outs.strip() and not _blocks(outs):
        p.append("'Out of scope' needs at least one bullet")
    return p


def _check_you(text: str) -> list[str]:
    p: list[str] = []
    _need(text, p, "What you bring", "what you bring")
    _need(text, p, "What is new to you", "new to you")
    spots = _need(text, p, "Blindspots", "blindspot", "blind spot")
    _need(text, p, "Where your interests fit", "interests")
    if spots.strip():
        blocks = _blocks(spots)
        if len(blocks) < 2:
            p.append(f"{len(blocks)} blindspot(s); name at least two")
        _each_has(blocks, ("why it matters", "cover it by"), "blindspot", p)
    return p


def _check_options(text: str) -> list[str]:
    p: list[str] = []
    prior = _need(text, p, "What others have done", "others have done", "precedent")
    _need(text, p, "Glaring holes", "glaring holes", "holes")
    opts = _need(text, p, "Options", "options")
    _need(text, p, "Recommendation", "recommend")
    if prior.strip():
        blocks = _blocks(prior)
        if len(blocks) < 2:
            p.append(f"{len(blocks)} precedent(s); find at least two")
        for block in blocks:
            if not _SOURCE_RE.search(block):
                p.append(f"precedent '{_head(block)}' has no link, 'Source:', or [inferred] mark")
    if opts.strip():
        blocks = _blocks(opts)
        if len(blocks) < 2:
            p.append(f"{len(blocks)} option(s); give at least two real alternatives")
        _each_has(blocks, ("catch", "proof"), "option", p)
    return p


def _check_plan(text: str) -> list[str]:
    p: list[str] = []
    chain = _need(text, p, "The chain", "the chain", "chain")
    _need(text, p, "First move this week", "first move")
    _need(text, p, "What we need from the sponsor", "need from")
    if chain.strip():
        blocks = _blocks(chain)
        if not 3 <= len(blocks) <= 6:
            p.append(f"the chain has {len(blocks)} step(s); use three to six")
        _each_has(blocks, ("done when", "unlocks"), "step", p, skip_last="unlocks")
    return p


def _check_report(text: str) -> list[str]:
    p: list[str] = []
    _need(text, p, "Summary", "summary")
    _need(text, p, "The problem as we understand it", "problem")
    _need(text, p, "Where things stand today", "stand today", "today")
    _need(text, p, "What we propose", "propose")
    how = _need(text, p, "How it works, in plain words", "how it works")
    know = _need(text, p, "How we will know it worked", "know it worked", "how we will know")
    _need(text, p, "Scope", "scope")
    _need(text, p, "Risks and how we will handle them", "risk")
    ask = _need(text, p, "What we need from you", "need from you", "the ask")
    if how.strip() and not _ANALOGY_RE.search(how):
        p.append("'How it works' has no analogy ('think of it like...'); give the reader a picture")
    if know.strip() and not _blocks(know):
        p.append("'How we will know it worked' needs specific checks as bullets")
    if ask.strip() and not _blocks(ask):
        p.append("'What we need from you' needs the ask as bullets: decisions, data, people, dates")
    words = len(re.findall(r"\b\w+\b", text))
    lo, hi = REPORT_WORDS
    if words < lo:
        p.append(f"{words} words; a report the sponsor can act on needs at least {lo}")
    elif words > hi:
        p.append(f"{words} words; cut to {hi} or fewer (two to four pages)")
    glossary = _section(text, "glossary")
    body = text.replace(glossary, "") if glossary else text
    undefined = [t for t in jargon_in(body) if not _TERM_RE[t].search(glossary)]
    if undefined:
        p.append(
            "technical terms with no plain-language glossary entry: "
            + ", ".join(_label(t) for t in undefined)
        )
    return p


def _check_notes(text: str) -> list[str]:
    p: list[str] = []
    _need(text, p, "Your 30-second version", "30-second", "30 second")
    slides = _need(text, p, "Slide outline", "slide")
    pocket = _need(text, p, "Analogies to keep in your pocket", "analogies")
    lost = _need(text, p, "Where they will get lost", "get lost", "lost")
    asked = _need(text, p, "Questions they will ask", "questions they")
    _need(text, p, "Check your own understanding", "check your own", "understanding")
    if slides.strip():
        blocks = _blocks(slides)
        if not 4 <= len(blocks) <= 10:
            p.append(f"{len(blocks)} slide(s); outline four to ten")
        _each_has(blocks, ("say:",), "slide", p)
    if pocket.strip() and len(_blocks(pocket)) < 2:
        p.append("keep at least two analogies in your pocket")
    if lost.strip():
        blocks = _blocks(lost)
        if len(blocks) < 2:
            p.append(f"{len(blocks)} confusion spot(s); plan for at least two")
        _each_has(blocks, ("if you see", "try"), "confusion spot", p)
    if asked.strip() and len(_blocks(asked)) < 2:
        p.append("list at least two questions they will ask, with answers")
    return p


def _check_grow(text: str) -> list[str]:
    p: list[str] = []
    _need(text, p, "What you learned", "learned")
    _need(text, p, "Blindspot to work on next", "blindspot", "blind spot")
    add = _need(text, p, "Add to your profile", "add to your profile", "profile")
    if add.strip() and not _blocks(add):
        p.append("'Add to your profile' needs at least one bullet")
    return p


_CHECKS: dict[str, Callable[[str], list[str]]] = {
    "problem": _check_problem,
    "you": _check_you,
    "options": _check_options,
    "plan": _check_plan,
    "report": _check_report,
    "notes": _check_notes,
    "grow": _check_grow,
}


def validate(stage: str, text: str) -> list[str]:
    """What the stage's output is missing; empty means it passes."""
    by_name(stage)
    return _CHECKS[stage](text)


# ── interest fit: the seam a typed scorer plugs into ────────────────────────


class InterestScorer(Protocol):
    """Scores how well a piece of work fits the human's interests, 0..1.

    The default is word overlap with the profile. A typed decision model such
    as TypeSafe's Jev (one Score question per step, answered with a
    probability) belongs here; it is not wired yet because its request format
    has not been checked against the real API.
    """

    def score(self, work: str, interests: list[str]) -> float: ...


_WORD = re.compile(r"[a-z][a-z0-9-]{3,}")
_STOP = frozenset(
    re.findall(
        r"\w+",
        "that this with from into what when your their them they have will would about "
        "more than then also just make work using done step uses",
    )
)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


class OverlapScorer:
    """Shared words between the step and the interests; three or more is a full fit."""

    def score(self, work: str, interests: list[str]) -> float:
        want = set().union(*(_words(i) for i in interests)) if interests else set()
        return round(min(1.0, len(_words(work) & want) / 3), 2)


def interest_fit(
    plan_text: str, interests: list[str], scorer: InterestScorer | None = None
) -> list[tuple[str, float]]:
    """(step headline, score) for each step of the plan's chain."""
    scorer = scorer or OverlapScorer()
    chain = _section(plan_text, "the chain", "chain")
    return [(_head(b, 70), scorer.score(b, interests)) for b in _blocks(chain)]


# ── the profile: private, on this machine only ──────────────────────────────

PROFILE_SECTIONS = (
    "Background",
    "Domains I know well",
    "Strengths",
    "Interests",
    "What I want to get better at",
    "How I like to work",
    "Growth log",
)

PROFILE_TEMPLATE = (
    "# Me\n\n"
    "Private profile for A.W.I.N.O. Brain mode. It lives only on this machine and is\n"
    "never committed. Bullets are fine; plain words are best.\n\n"
    + "".join(f"## {s}\n\n" for s in PROFILE_SECTIONS)
)


def profile_path() -> Path:
    return user_config_dir() / BRAIN_DIR / PROFILE_FILE


def init_profile() -> Path:
    path = profile_path()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PROFILE_TEMPLATE, encoding="utf-8")
    return path


def profile_text() -> str:
    path = profile_path()
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def profile_gaps(text: str | None = None) -> list[str]:
    """Profile sections the ``you`` stage needs that are still empty."""
    text = profile_text() if text is None else text
    return [s for s in ("Background", "Strengths", "Interests") if not _section(text, s).strip()]


def interests(text: str | None = None) -> list[str]:
    text = profile_text() if text is None else text
    body = _section(text, "Interests")
    return [_head(b, 200) for b in _blocks(body)] or [ln for ln in body.splitlines() if ln.strip()]


def append_growth(entries: list[str], session_title: str) -> Path:
    """Add the grow stage's profile lines under the profile's Growth log."""
    path = init_profile()
    text = path.read_text(encoding="utf-8")
    stamp = datetime.now(UTC).date().isoformat()
    lines = [f"- {stamp} ({session_title}): {e}" for e in entries]
    marker = re.search(r"^## Growth log\s*$", text, re.M)
    if marker:
        # Newest first, right under the heading.
        rest = text[marker.end() :].lstrip("\n")
        text = text[: marker.end()] + "\n\n" + "\n".join(lines) + "\n" + (rest or "")
    else:
        text = text.rstrip("\n") + "\n\n## Growth log\n\n" + "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


# ── sessions ────────────────────────────────────────────────────────────────


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].strip("-") or "session"


def brain_root(state_root: Path) -> Path:
    root = state_root / BRAIN_DIR
    root.mkdir(parents=True, exist_ok=True)
    ignore = root / ".gitignore"
    if not ignore.is_file():
        # A sponsor's problem is never committed by accident.
        ignore.write_text("*\n", encoding="utf-8")
    return root


@dataclass
class Session:
    path: Path
    data: dict

    @property
    def id(self) -> str:
        return self.path.name

    @property
    def title(self) -> str:
        return str(self.data.get("title", self.id))

    def stage_state(self, name: str) -> dict:
        return self.data.setdefault("stages", {}).get(name, {})

    def recorded(self, name: str) -> bool:
        return bool(self.stage_state(name).get("recorded_at"))

    def confirmed(self, name: str) -> bool:
        return bool(self.stage_state(name).get("confirmed_at"))

    def next_stage(self) -> Stage | None:
        for stage in STAGES:
            if not self.recorded(stage.name):
                return stage
            if stage.checkpoint and not self.confirmed(stage.name):
                return stage
        return None

    def file_for(self, stage: Stage) -> Path:
        return self.path / stage.filename

    def save(self) -> None:
        (self.path / SESSION_FILE).write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def start(state_root: Path, title: str, brief: str | None = None) -> Session:
    root = brain_root(state_root)
    base = f"{datetime.now(UTC).date().isoformat()}-{_slug(title)}"
    path, n = root / base, 2
    while path.exists():
        path, n = root / f"{base}-{n}", n + 1
    path.mkdir(parents=True)
    (path / BRIEF_FILE).write_text(
        brief
        if brief
        else f"# {title}\n\nPaste everything here: the ask, notes, links, the project space.\n",
        encoding="utf-8",
    )
    session = Session(path, {"title": title, "created_at": _now(), "stages": {}})
    session.save()
    (root / ACTIVE_FILE).write_text(path.name, encoding="utf-8")
    return session


def load(state_root: Path, session_id: str) -> Session:
    path = state_root / BRAIN_DIR / session_id
    meta = path / SESSION_FILE
    if not meta.is_file():
        raise BrainError(f"no Brain session {session_id!r}")
    return Session(path, json.loads(meta.read_text(encoding="utf-8")))


def active(state_root: Path) -> Session | None:
    pointer = state_root / BRAIN_DIR / ACTIVE_FILE
    if not pointer.is_file():
        return None
    try:
        return load(state_root, pointer.read_text(encoding="utf-8").strip())
    except BrainError:
        return None


def use(state_root: Path, session_id: str) -> Session:
    session = load(state_root, session_id)
    (state_root / BRAIN_DIR / ACTIVE_FILE).write_text(session.id, encoding="utf-8")
    return session


def sessions(state_root: Path) -> list[Session]:
    root = state_root / BRAIN_DIR
    if not root.is_dir():
        return []
    found = []
    for meta in sorted(root.glob(f"*/{SESSION_FILE}")):
        found.append(Session(meta.parent, json.loads(meta.read_text(encoding="utf-8"))))
    return found


def _blockers(session: Session, stage: Stage) -> list[str]:
    """Why ``stage`` cannot be recorded yet: an earlier stage missing, or a
    checkpoint the human has not answered."""
    for earlier in STAGES[: STAGES.index(stage)]:
        if not session.recorded(earlier.name):
            return [f"record '{earlier.name}' first"]
        if earlier.checkpoint and not session.confirmed(earlier.name):
            return [
                f"'{earlier.name}' is a checkpoint: show it to the human and record their "
                f'answer with  awino brain confirm {earlier.name} --note "<their words>"'
            ]
    if stage.name == "you":
        gaps = profile_gaps()
        if gaps:
            return [
                f"the profile is missing {', '.join(gaps)}: interview the human, fill "
                f"{profile_path()} and run this again"
            ]
    return []


def record(session: Session, stage_name: str, text: str) -> list[str]:
    """Check and store a stage. Returns problems; empty means recorded."""
    stage = by_name(stage_name)
    problems = _blockers(session, stage) or validate(stage.name, text)
    if problems:
        return problems
    target = session.file_for(stage)
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")
    state = session.data.setdefault("stages", {}).setdefault(stage.name, {})
    state.update({"recorded_at": _now(), "file": target.name})
    state.pop("confirmed_at", None)  # a changed checkpoint needs a fresh yes
    if stage.name == "grow":
        add = _section(text, "add to your profile", "profile")
        append_growth([_head(b, 300) for b in _blocks(add)], session.title)
    session.save()
    return []


def confirm(session: Session, stage_name: str, note: str) -> None:
    stage = by_name(stage_name)
    if not stage.checkpoint:
        raise BrainError(f"'{stage.name}' is not a checkpoint; nothing to confirm")
    if not session.recorded(stage.name):
        raise BrainError(f"record '{stage.name}' before confirming it")
    if not note.strip():
        raise BrainError('record what the human said: --note "<their words>"')
    state = session.data["stages"][stage.name]
    state.update({"confirmed_at": _now(), "note": note.strip()})
    session.save()


def copy_brief(source: Path) -> str:
    """Read a brief file the human points at (Markdown or plain text)."""
    if not source.is_file():
        raise BrainError(f"cannot read brief {source}")
    return source.read_text(encoding="utf-8", errors="replace")


def export(session: Session, dest: Path) -> list[Path]:
    """Copy the two deliverables somewhere the human chooses."""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for name in ("report", "notes"):
        f = session.file_for(by_name(name))
        if f.is_file():
            out.append(Path(shutil.copy2(f, dest / f"{session.id}-{f.name}")))
    return out
