"""Working memory: the mind to the ledger's court record.

The architecture this serves: mission + success criteria = the target,
seeds = the commitments, checklist = the now, facts/decisions = the
understanding, ledger = the proof, user model = the who, buddy = the auditor.

Four pieces, all deterministic, all in project state (``.awino/``) except the
user model (``~/.awino/profile.yaml`` -- the human is not the project):

- ``Checklist`` (``.awino/checklist.json``): the live execution plan. JSON,
  not markdown: the loop drivers rewrite it at every phase boundary and the
  tests assert on parsed structure, not fragile prose parsing. The human reads
  it through ``awino best``'s compact brief (focus + blocked), never a dump.
- ``Facts`` (``.awino/facts.md``): durable project facts, append-only.
  Corrections never silently overwrite: the old entry is marked superseded
  with a dated note pointing at the new one.
- ``Decisions`` (``.awino/decisions.md``): every significant decision with
  its rationale. Fed automatically by the loop drivers (pair-planning answers
  and plan approvals). A decision entry without a recorded why is invalid;
  buddy flags it.
- ``UserModel`` (``~/.awino/profile.yaml``): how this human works -- learned,
  not assumed. Seeded from explicit answers, updated by a small named set of
  deterministic learning rules (``RULE-*`` below), never vibes. Fields the
  human set explicitly are never overwritten by a rule.

Update-as-needed, no write-once files: loop phase boundaries update the
checklist; outcome verdicts update the user model; session-end writes
facts/decisions deltas. This module is the storage and the rules; the drivers,
the CLI, the playbook, and buddy own the hooks that call it.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from awino.paths import user_config_dir

# ── shared ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ── checklist ────────────────────────────────────────────────────────────────
# Representation choice: checklist.json. The drivers update it at every phase
# boundary (advance, back, lock, close) and the tests assert on the parsed
# structure; a markdown checklist would force every test to parse prose and
# every driver to do string surgery. The human never reads the raw file:
# `awino best` prints the compact brief (focus + blocked items).

CHECKLIST_FILENAME = "checklist.json"
CHECKLIST_STALE_DAYS = 7

_ITEM_STATUSES = ("open", "doing", "done", "blocked")


class Checklist:
    """The live execution plan: one item per loop, statuses, focus, blockers."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root

    @property
    def path(self) -> Path:
        return self.state_root / CHECKLIST_FILENAME

    # -- persistence ------------------------------------------------------

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"version": 1, "focus": None, "updated_at": None, "items": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "focus": None, "updated_at": None, "items": []}
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return {"version": 1, "focus": None, "updated_at": None, "items": []}
        return data

    def _save(self, data: dict) -> None:
        data["updated_at"] = _now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
        )

    def _item(self, data: dict, loop_id: str) -> dict | None:
        for item in data["items"]:
            if item.get("loop_id") == loop_id:
                return item
        return None

    def _move(
        self,
        data: dict,
        item: dict,
        to: str,
        note: str,
        *,
        phase: str | None = None,
        blocker: str | None = None,
    ) -> None:
        from_status = item["status"]
        item["status"] = to
        if phase is not None:
            item["phase"] = phase
        if to == "blocked":
            item["blocker"] = blocker or item.get("blocker")
        elif to != "blocked":
            item["blocker"] = None
        at = _now_iso()
        item["updated_at"] = at
        item.setdefault("history", []).append(
            {
                "at": at,
                "session": _current_session_id(self.state_root),
                "from": from_status,
                "to": to,
                "note": note,
            }
        )
        if to != "done":
            data["focus"] = item["loop_id"]
        elif data.get("focus") == item["loop_id"]:
            data["focus"] = None

    # -- phase-boundary hooks (called by the loop drivers) ----------------

    def note_loop_created(
        self, loop_id: str, kind: str, task: str, phase: str
    ) -> str:
        """A new loop starts doing its first phase. Returns the item id."""
        data = self._load()
        item = self._item(data, loop_id)
        if item is not None:
            return item["id"]
        item_id = f"CHK-{len(data['items']) + 1:04d}"
        now = _now_iso()
        item = {
            "id": item_id,
            "loop_id": loop_id,
            "kind": kind,
            "title": task,
            "status": "doing",
            "phase": phase,
            "blocker": None,
            "updated_at": now,
            "history": [
                {
                    "at": now,
                    "session": _current_session_id(self.state_root),
                    "from": "open",
                    "to": "doing",
                    "note": f"loop created at phase '{phase}'",
                }
            ],
        }
        data["items"].append(item)
        data["focus"] = loop_id
        self._save(data)
        return item_id

    def note_phase(self, loop_id: str, old_phase: str, new_phase: str) -> None:
        """`loop next` advanced: the item follows the loop's phase."""
        data = self._load()
        item = self._item(data, loop_id)
        if item is None:
            return
        self._move(
            data, item, "doing", f"advanced: {old_phase} -> {new_phase}", phase=new_phase
        )
        # Entering a new phase: no receipt status is known for it yet. The
        # previous phase's statuses stay under "skills_by_phase".
        item["skills"] = {}
        self._save(data)

    def note_blocked(self, loop_id: str, blocker: str) -> None:
        """Three-strikes lock or escalation: the item is blocked, with why."""
        data = self._load()
        item = self._item(data, loop_id)
        if item is None:
            return
        self._move(data, item, "blocked", f"blocked: {blocker}", blocker=blocker)
        self._save(data)

    def note_unblocked(self, loop_id: str, note: str, *, phase: str | None = None) -> None:
        """`loop back` re-entered a phase: a human intervened, work resumes."""
        data = self._load()
        item = self._item(data, loop_id)
        if item is None:
            return
        self._move(
            data, item, "doing", note or "re-entered an earlier phase", phase=phase
        )
        self._save(data)

    def note_done(self, loop_id: str, note: str) -> None:
        """The loop's terminal transition: the item is done."""
        data = self._load()
        item = self._item(data, loop_id)
        if item is None:
            return
        self._move(data, item, "done", note or "loop complete")
        self._save(data)

    def note_verdict(self, loop_id: str, verdict: str, note: str) -> None:
        """`loop close` recorded the outcome verdict: the item closes on it."""
        data = self._load()
        item = self._item(data, loop_id)
        if item is None:
            item_id = f"CHK-{len(data['items']) + 1:04d}"
            item = {
                "id": item_id,
                "loop_id": loop_id,
                "kind": "",
                "title": "",
                "status": "open",
                "phase": "done",
                "blocker": None,
                "updated_at": _now_iso(),
                "history": [],
            }
            data["items"].append(item)
        detail = f"outcome verdict: {verdict}" + (f" -- {note}" if note else "")
        self._move(data, item, "done", detail, phase="done")
        self._save(data)

    def note_skill_status(
        self, loop_id: str, phase: str, statuses: dict[str, str]
    ) -> None:
        """Record per-skill receipt status for a phase.

        statuses maps skill name -> "received" | "missing" | "invalid".
        The item's "skills" entry is the current phase's statuses, exactly
        {"<skill>": "received|missing|invalid"}; per-phase history
        accumulates under "skills_by_phase". Additive: phases with no
        recorded status simply carry none.
        """
        data = self._load()
        item = self._item(data, loop_id)
        if item is None:
            return
        item["skills"] = statuses
        item.setdefault("skills_by_phase", {})[phase] = statuses
        self._save(data)

    def skill_status(self, loop_id: str) -> dict[str, str]:
        """Current phase's skill receipt status for a loop, {} when none."""
        data = self._load()
        item = self._item(data, loop_id)
        if item is None:
            return {}
        skills = item.get("skills")
        return skills if isinstance(skills, dict) else {}

    def skill_status_by_phase(self, loop_id: str) -> dict[str, dict[str, str]]:
        """Per-phase skill receipt history for a loop, or {} when none."""
        data = self._load()
        item = self._item(data, loop_id)
        if item is None:
            return {}
        history = item.get("skills_by_phase")
        return history if isinstance(history, dict) else {}

    # -- reads ------------------------------------------------------------

    def items(self) -> list[dict]:
        return self._load()["items"]

    def blocked_items(self) -> list[dict]:
        return [i for i in self.items() if i.get("status") == "blocked"]

    def focus(self) -> dict | None:
        data = self._load()
        focus_id = data.get("focus")
        if not focus_id:
            return None
        return self._item(data, focus_id)

    def last_move_at(self) -> str | None:
        latest: str | None = None
        for item in self.items():
            for hop in item.get("history", []):
                at = hop.get("at")
                if at and (latest is None or at > latest):
                    latest = at
        return latest

    def brief_lines(self) -> list[str]:
        """Compact session-start display: focus + blocked items, never a dump."""
        data = self._load()
        if not data["items"]:
            return ["CHECKLIST  empty (no loops yet)"]
        lines = []
        focus = self.focus()
        if focus is not None:
            lines.append(
                f"CHECKLIST  focus={focus['loop_id']} "
                f"(phase {focus.get('phase')}, {focus.get('status')})"
            )
            phase_skills = focus.get("skills") or {}
            if phase_skills:
                lines.append(
                    "SKILLS  "
                    + " ".join(
                        f"{name}={status}"
                        for name, status in phase_skills.items()
                    )
                )
        else:
            lines.append("CHECKLIST  no active focus (nothing in flight)")
        for item in self.blocked_items():
            blocker = item.get("blocker") or "(no reason recorded)"
            lines.append(f"BLOCKED  {item['id']}: {item['loop_id']} -- {blocker}")
        open_count = sum(
            1 for i in data["items"] if i.get("status") in ("open", "doing")
        )
        lines.append(
            f"  {open_count} in flight, "
            f"{sum(1 for i in data['items'] if i.get('status') == 'done')} done"
        )
        return lines

    def moves_since(self, since_iso: str | None) -> list[dict]:
        """Every status hop after `since_iso` (or all hops when None)."""
        moves: list[dict] = []
        for item in self.items():
            for hop in item.get("history", []):
                at = hop.get("at") or ""
                if since_iso is not None and at <= since_iso:
                    continue
                moves.append(
                    {
                        "at": at,
                        "item": item["id"],
                        "loop_id": item["loop_id"],
                        "from": hop.get("from"),
                        "to": hop.get("to"),
                        "note": hop.get("note", ""),
                    }
                )
        moves.sort(key=lambda m: m["at"])
        return moves

    def moves_summary_lines(
        self, since_iso: str | None, limit: int = 10
    ) -> list[str]:
        """Session-end summary: what moved this session."""
        moves = self.moves_since(since_iso)
        if not moves:
            return ["CHECKLIST  nothing moved this session"]
        lines = ["CHECKLIST  what moved this session:"]
        for move in moves[:limit]:
            lines.append(
                f"  MOVED  {move['item']}: {move['from']} -> {move['to']} "
                f"-- {move['note']}"
            )
        if len(moves) > limit:
            lines.append(f"  ... and {len(moves) - limit} more")
        return lines


def _current_session_id(state_root: Path) -> str:
    """Best-effort session id for checklist history; 'unknown' when none."""
    try:
        from awino import session_state

        session = session_state.load(state_root)
    except Exception:
        return "unknown"
    return session.session_id if session is not None else "unknown"


# ── facts ────────────────────────────────────────────────────────────────────
# facts.md is append-only markdown: human-readable, and corrections supersede
# with a dated note on the old entry pointing at the new one. Never silently
# overwritten -- the old entry stays, marked, so a reader sees the correction
# happened instead of history being rewritten.

FACTS_FILENAME = "facts.md"

_FACT_HEADER_RE = re.compile(
    r"^##\s+(F-\d+)\s+—\s+(\d{4}-\d{2}-\d{2})(.*?)$", re.MULTILINE
)


class Fact:
    def __init__(
        self,
        id: str,
        date: str,
        text: str,
        superseded_by: str | None = None,
        supersedes: str | None = None,
    ) -> None:
        self.id = id
        self.date = date
        self.text = text
        self.superseded_by = superseded_by
        self.supersedes = supersedes


class Facts:
    """Durable project facts, appended as discovered, corrected by supersession."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root

    @property
    def path(self) -> Path:
        return self.state_root / FACTS_FILENAME

    def _read(self) -> str:
        if not self.path.is_file():
            return ""
        return self.path.read_text(encoding="utf-8")

    def _write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8", newline="")

    def entries(self) -> list[Fact]:
        """Parse every entry, oldest first."""
        text = self._read()
        matches = list(_FACT_HEADER_RE.finditer(text))
        out: list[Fact] = []
        for i, match in enumerate(matches):
            body = text[match.end() : matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            tail = match.group(3)
            superseded_by: str | None = None
            supersedes: str | None = None
            m = re.search(r"SUPERSEDED by (F-\d+)", tail)
            if m:
                superseded_by = m.group(1)
            m = re.search(r"\(supersedes (F-\d+)\)", tail)
            if m:
                supersedes = m.group(1)
            out.append(
                Fact(
                    id=match.group(1),
                    date=match.group(2),
                    text=body.strip(),
                    superseded_by=superseded_by,
                    supersedes=supersedes,
                )
            )
        return out

    def get(self, fact_id: str) -> Fact | None:
        for entry in self.entries():
            if entry.id == fact_id:
                return entry
        return None

    def _next_id(self) -> str:
        ids = [e.id for e in self.entries()]
        nums = [int(i.split("-")[1]) for i in ids if i.startswith("F-")]
        return f"F-{max(nums, default=0) + 1:04d}"

    def append(self, text: str) -> str:
        """Append one fact. Returns its id."""
        text = text.strip()
        if not text:
            raise ValueError("cannot record an empty fact")
        fact_id = self._next_id()
        current = self._read()
        if not current:
            current = "# Facts\n"
        if not current.endswith("\n"):
            current += "\n"
        current += f"\n## {fact_id} — {_today()}\n\n{text}\n"
        self._write(current)
        return fact_id

    def correct(self, old_id: str, new_text: str, note: str = "") -> str:
        """Supersede an old fact: the old entry is marked (never rewritten),
        and the new entry points back at it. Returns the new id."""
        old = self.get(old_id)
        if old is None:
            raise KeyError(f"no such fact {old_id!r}")
        if old.superseded_by is not None:
            raise ValueError(
                f"{old_id} was already superseded by {old.superseded_by}; "
                "correct the latest entry instead"
            )
        new_text = new_text.strip()
        if not new_text:
            raise ValueError("cannot record an empty fact")
        new_id = self._next_id()
        today = _today()
        current = self._read()
        # Mark the old header in place: a dated note pointing at the new one.
        # This is the only edit ever made to an existing entry, and it only
        # ever adds the marker -- the old text is untouched.
        old_header = f"## {old.id} — {old.date}"
        marked = f"## {old.id} — {old.date} — SUPERSEDED by {new_id} on {today}"
        if old_header not in current:
            raise KeyError(f"fact {old_id!r} header not found; file changed under us")
        current = current.replace(old_header, marked, 1)
        extra = f"\n> Correction note: {note.strip()}\n" if note.strip() else ""
        current += f"\n## {new_id} — {today} (supersedes {old.id})\n{extra}\n{new_text}\n"
        self._write(current)
        return new_id


# ── decisions ────────────────────────────────────────────────────────────────
# decisions.md: every significant decision with its rationale. The loop drivers
# feed it (pair-planning answers/defaults, plan approvals); a decision entry
# without a recorded why is invalid and buddy flags it.

DECISIONS_FILENAME = "decisions.md"

#: Stored as the why when none was recorded. Buddy flags these; --fix prompts
#: the human -- the why is judgment, never invented.
WHY_MISSING = "(why not recorded)"

_DECISION_HEADER_RE = re.compile(
    r"^##\s+(D-\d+)\s+—\s+(\d{4}-\d{2}-\d{2})(.*?)$", re.MULTILINE
)
_FIELD_RE = re.compile(r"^(decision|why|source|key|at):\s*(.*?)\s*$", re.MULTILINE)


class Decision:
    def __init__(
        self,
        id: str,
        date: str,
        decision: str,
        why: str,
        source: str,
        key: str | None,
        at: str,
        superseded_by: str | None = None,
    ) -> None:
        self.id = id
        self.date = date
        self.decision = decision
        self.why = why
        self.source = source
        self.key = key
        self.at = at
        self.superseded_by = superseded_by

    @property
    def why_recorded(self) -> bool:
        return bool(self.why) and self.why != WHY_MISSING


class Decisions:
    """The project's decision log: what was decided, and why."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root

    @property
    def path(self) -> Path:
        return self.state_root / DECISIONS_FILENAME

    def _read(self) -> str:
        if not self.path.is_file():
            return ""
        return self.path.read_text(encoding="utf-8")

    def _write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8", newline="")

    def entries(self) -> list[Decision]:
        text = self._read()
        matches = list(_DECISION_HEADER_RE.finditer(text))
        out: list[Decision] = []
        for i, match in enumerate(matches):
            body = text[match.end() : matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            fields = {"decision": "", "why": "", "source": "", "key": "", "at": ""}
            for line in body.splitlines():
                m = _FIELD_RE.match(line)
                if m:
                    fields[m.group(1)] = m.group(2)
            tail = match.group(3)
            superseded_by: str | None = None
            m = re.search(r"SUPERSEDED by (D-\d+)", tail)
            if m:
                superseded_by = m.group(1)
            out.append(
                Decision(
                    id=match.group(1),
                    date=match.group(2),
                    decision=fields["decision"],
                    why=fields["why"],
                    source=fields["source"],
                    key=fields["key"] or None,
                    at=fields["at"],
                    superseded_by=superseded_by,
                )
            )
        return out

    def get(self, decision_id: str) -> Decision | None:
        for entry in self.entries():
            if entry.id == decision_id:
                return entry
        return None

    def by_key(self, key: str) -> Decision | None:
        """The latest non-superseded entry recorded under `key`, if any."""
        for entry in reversed(self.entries()):
            if entry.key == key and entry.superseded_by is None:
                return entry
        return None

    def why_less(self) -> list[Decision]:
        """Entries with no recorded why: invalid, buddy flags them."""
        return [e for e in self.entries() if not e.why_recorded]

    def _next_id(self) -> str:
        ids = [e.id for e in self.entries()]
        nums = [int(i.split("-")[1]) for i in ids if i.startswith("D-")]
        return f"D-{max(nums, default=0) + 1:04d}"

    def record(
        self,
        decision: str,
        why: str,
        source: str,
        key: str | None = None,
    ) -> str:
        """Record one decision with its rationale. Returns its id.

        When `key` names an existing live entry (e.g. a re-answered pairing
        question), the old entry is marked superseded and the new one takes
        its place -- a changed mind is auditable, not rewritten.
        """
        decision = decision.strip()
        if not decision:
            raise ValueError("cannot record an empty decision")
        why = why.strip() or WHY_MISSING
        new_id = self._next_id()
        superseded_note = ""
        if key is not None:
            previous = self.by_key(key)
            if previous is not None:
                current = self._read()
                old_header = f"## {previous.id} — {previous.date}"
                marked = (
                    f"## {previous.id} — {previous.date} "
                    f"— SUPERSEDED by {new_id} on {_today()}"
                )
                current = current.replace(old_header, marked, 1)
                self._write(current)
                superseded_note = f" (supersedes {previous.id})"
        current = self._read()
        if not current:
            current = "# Decisions\n"
        if not current.endswith("\n"):
            current += "\n"
        current += (
            f"\n## {new_id} — {_today()}{superseded_note}\n"
            f"decision: {decision}\n"
            f"why: {why}\n"
            f"source: {source.strip()}\n"
            + (f"key: {key}\n" if key else "")
            + f"at: {_now_iso()}\n"
        )
        self._write(current)
        return new_id


# ── precedent (case law) ─────────────────────────────────────────────────
# When a new decision is recorded (pair-planning answer, plan approval,
# thinking waiver), A.W.I.N.O. surfaces past similar decisions with their
# outcomes: "last time you chose X over Y because Z; outcome was <verdict>".
# Advisory only: it never blocks, never invents -- no match means silence.
#
# The matching rule, exactly:
#   1. Candidates are past decisions.md entries that are not superseded.
#      Entries without a decision-key are excluded from area-matched lookups
#      (the key's suffix carries the decision area: Q-ids -> "pairing",
#      "approval" -> "approval", "thinking-waiver" -> "thinking-waiver").
#   2. The past entry matches the lookup's decision area -- or, for
#      area-less lookups, the same loop kind -- AND shares at least two
#      content keywords with the new decision. (Pairing answers compare
#      against past pairing answers, approvals against past approvals:
#      the key's suffix carries the area -- Q-ids -> "pairing",
#      "approval" -> "approval", "thinking-waiver" -> "thinking-waiver" --
#      and the key prefix "<kind>-<id>" carries the loop for kind-only
#      lookups. A keyless entry carries no kind/area context and matches
#      only context-less lookups.)
#      Keywords are lowercase alphanumerics of length >= 4 drawn from the new
#      decision's decision + why text; stopwords ("with", "from", "this",
#      "that", "were", "have", "your", "they", "their", "which", "what",
#      "will", "over", "under", "than", "then", "into", "when", "loop") are
#      excluded. (The owner's example uses "over"; it still matches through
#      the other shared keywords.)
#   3. A past decision's outcome is the verdict of the latest
#      outcome_verdict ledger event for the loop the entry was recorded for
#      (the key's "<kind>-<id>" portion), if any.
#   4. Candidates are ranked deterministically: more shared keywords first,
#      then more recent entries first, then lower decision ids.
#   5. At most three precedents are returned; zero is a normal result and
#      means "no related past decision" -- the caller prints nothing.
#
# Documented in docs/architecture.md ("Precedent: case law").

_PRECEDENT_STOPWORDS = frozenset(
    {
        "with", "from", "this", "that", "were", "have", "your", "they",
        "their", "which", "what", "will", "over", "under", "than", "then",
        "into", "when", "loop",
    }
)


@dataclass
class Precedent:
    """One past similar decision and its outcome, ready to surface."""

    id: str  # the past decision's D-NNNN id
    decision: str  # its decision text
    why: str  # its why text
    outcome: str | None  # its loop's latest outcome verdict, if any
    shared_keywords: tuple[str, ...]  # the keywords that matched


def _precedent_keywords(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(
        word
        for word in words
        if len(word) >= 4 and word not in _PRECEDENT_STOPWORDS
    )


def _decision_area(key: str | None) -> str | None:
    """The decision area carried in a decisions.md key ("<kind>-<id>:<area>")."""
    if not key or ":" not in key:
        return None
    suffix = key.rsplit(":", 1)[1]
    if suffix.startswith("Q"):
        return "pairing"
    return suffix or None


def _decision_loop_id(key: str | None) -> str | None:
    """The "<kind>-<id>" loop the decision was recorded for, if any."""
    if not key or ":" not in key:
        return None
    return key.rsplit(":", 1)[0] or None


def _verdict_for_loop(ledger: object, loop_id: str) -> str | None:
    """The loop's latest outcome verdict from the ledger, if any."""
    if ledger is None:
        return None
    try:
        events = ledger.loop_events(loop_id)  # type: ignore[union-attr]
    except Exception:
        return None
    verdict: str | None = None
    for event in reversed(events):
        if getattr(event, "kind", None) == "outcome_verdict":
            detail = getattr(event, "detail", "") or ""
            match = re.search(r"\bverdict:\s*(yes|partial|no)\b", detail)
            if match:
                verdict = match.group(1)
                break
    return verdict


def find_precedents(
    decisions: Decisions,
    decision: str,
    why: str,
    *,
    area: str | None = None,
    loop_kind: str | None = None,
    ledger: object | None = None,
    limit: int = 3,
) -> list[Precedent]:
    """Find past similar decisions to surface as case law.

    Pure and deterministic: same inputs -> same outputs. Ranked by more
    shared keywords first, then newer entries first, then lower decision
    ids. Returns [] when nothing related exists -- the caller stays silent.
    Raises nothing on unreadable input.
    """
    try:
        wanted = _precedent_keywords(f"{decision} {why}")
        if not wanted:
            return []
        candidates: list[tuple[int, Decision, frozenset[str]]] = []
        for entry in decisions.entries():
            if entry.superseded_by is not None or not entry.decision.strip():
                continue
            entry_area = _decision_area(entry.key)
            if area is not None:
                # Area-scoped lookup: the past decision must carry the same
                # decision area. Unkeyed entries carry no area context and
                # cannot match.
                if entry_area != area:
                    continue
            elif loop_kind is not None:
                # Area-less lookup: fall back to the loop kind read from the
                # key prefix ("<kind>-<id>"); a keyless entry carries no kind
                # context, so it cannot match.
                past_loop = _decision_loop_id(entry.key)
                if past_loop is None or not past_loop.startswith(
                    loop_kind + "-"
                ):
                    continue
            shared = wanted & _precedent_keywords(
                f"{entry.decision} {entry.why}"
            )
            if len(shared) < 2:
                continue
            candidates.append((len(shared), entry, shared))
        # Deterministic rank, three stable passes: more shared keywords
        # first, then more recent entries first, then lower decision ids.
        # (One key with reverse=True would flip every component -- the
        # keyword count must descend while the id ascends.)
        candidates.sort(key=lambda c: c[1].id)
        candidates.sort(key=lambda c: c[1].date, reverse=True)
        candidates.sort(key=lambda c: -c[0])
        out: list[Precedent] = []
        for _, entry, shared in candidates[: max(limit, 0)]:
            loop_id = _decision_loop_id(entry.key)
            out.append(
                Precedent(
                    id=entry.id,
                    decision=entry.decision,
                    why=entry.why,
                    outcome=(
                        _verdict_for_loop(ledger, loop_id)
                        if ledger is not None and loop_id is not None
                        else None
                    ),
                    shared_keywords=tuple(sorted(shared)),
                )
            )
        return out
    except Exception:
        return []


def format_precedent(precedent: Precedent) -> str:
    """One human line: "last time you chose X over Y because Z; outcome was
    <verdict>." Omits the outcome clause when the past loop never closed
    with a verdict -- no invented outcomes."""
    line = (
        f"last time you chose {precedent.decision} "
        f"because {precedent.why}"
    )
    if precedent.outcome is not None:
        line += f"; outcome was {precedent.outcome}"
    return line.rstrip(". ") + "."


# ── user model ───────────────────────────────────────────────────────────────
# ~/.awino/profile.yaml: how this human works. Learned, not assumed -- the
# learning rules below are explicit and deterministic (a small named rule set,
# not vibes). Fields the human set explicitly (the `explicit` list) are never
# overwritten by a rule; the skipped update is recorded in `learned` so the
# rule firing is visible instead of silent.

PROFILE_FILENAME = "profile.yaml"
PROFILE_VERSION = 1

#: Verdict notes matching this count as "overrode the Honda/default again".
_OVERRIDE_RE = re.compile(r"overr(?:ode|idden|iding) the (?:default|honda)", re.I)

#: Two overrides and the human wants bigger recommendations up front.
OVERRIDE_THRESHOLD = 2

#: The named learning rules. Each maps evidence -> one field change.
RULES = (
    "RULE-VERBOSITY-TERSE",  # verdict note says "too verbose" -> terse summaries
    "RULE-VERBOSITY-VERBOSE",  # verdict note says "too terse"/"more detail" -> verbose
    "RULE-CHALLENGE-WANT",  # correction welcomes a challenge -> wants_challenges=true
    "RULE-CHALLENGE-AVOID",  # correction rejects a challenge -> wants_challenges=false
    "RULE-OPTIONS-FEW",  # correction says "fewer/too many options" -> options_style=few
    "RULE-OPTIONS-MANY",  # correction asks for more options -> options_style=standard
    "RULE-SCOPE-BIG",  # 2+ Honda overrides -> recommendation_scope=big-first
    "RULE-STANCE-DIRECT",  # correction says "be direct" -> preferred_stance=expert
)

_CHALLENGE_WANT_RE = re.compile(
    r"good challenge|challenge accepted|push ?back more|be blunt", re.I
)
_CHALLENGE_AVOID_RE = re.compile(
    r"stop challenging|don't push back|too confrontational", re.I
)
_OPTIONS_FEW_RE = re.compile(r"fewer options|too many options", re.I)
_OPTIONS_MANY_RE = re.compile(r"more options|show me (the )?alternatives", re.I)
_STANCE_DIRECT_RE = re.compile(
    r"be (more )?direct|just tell me straight|no sugarcoat", re.I
)
_VERBOSE_RE = re.compile(r"too verbose", re.I)
_TERSE_RE = re.compile(r"too terse|more detail", re.I)

_PROFILE_DEFAULTS: dict = {
    "version": PROFILE_VERSION,
    # Fields the human set by hand (explicit answers). Learning rules never
    # touch these; the file is the answer surface.
    "explicit": [],
    "narration_verbosity": "normal",  # normal|terse|verbose
    "wants_challenges": None,  # None|true|false (None = unknown, not assumed)
    "options_style": "standard",  # standard|few
    "recommendation_scope": "honda-first",  # honda-first|big-first
    "preferred_stance": None,  # null|stance name, seeded from explicit answers
    "honda_overrides": 0,  # counter feeding RULE-SCOPE-BIG
    "learned": [],  # audit trail: {at, rule, evidence, field, from, to}
}


class UserModel:
    """The human, learned. Reads/writes ~/.awino/profile.yaml."""

    @staticmethod
    def path() -> Path:
        return user_config_dir() / PROFILE_FILENAME

    @classmethod
    def load(cls) -> dict:
        """The profile merged over defaults. Missing or corrupt file ->
        defaults (never a crash: calibration must degrade, not refuse)."""
        profile = copy.deepcopy(_PROFILE_DEFAULTS)
        path = cls.path()
        if not path.is_file():
            return profile
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return profile
        if not isinstance(data, dict):
            return profile
        for key in _PROFILE_DEFAULTS:
            if key in data:
                profile[key] = data[key]
        return profile

    @classmethod
    def save(cls, profile: dict) -> Path:
        path = cls.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: profile.get(key, default)
            for key, default in _PROFILE_DEFAULTS.items()
        }
        path.write_text(
            yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
            newline="",
        )
        return path

    @staticmethod
    def _apply(
        profile: dict, field: str, value: object, rule: str, evidence: str
    ) -> bool:
        """Apply one rule's update. Returns True when the profile changed."""
        if field in profile.get("explicit", []):
            profile.setdefault("learned", []).append(
                {
                    "at": _now_iso(),
                    "rule": rule,
                    "evidence": evidence[:200],
                    "field": field,
                    "from": profile.get(field),
                    "to": value,
                    "skipped": "explicit",
                }
            )
            return False
        if profile.get(field) == value:
            return False
        old = profile.get(field)
        profile[field] = value
        profile.setdefault("learned", []).append(
            {
                "at": _now_iso(),
                "rule": rule,
                "evidence": evidence[:200],
                "field": field,
                "from": old,
                "to": value,
            }
        )
        return True

    @classmethod
    def apply_verdict(cls, profile: dict, verdict: str, note: str) -> bool:
        """Learn from an outcome verdict. Called by `awino loop close`."""
        note = note or ""
        changed = False
        if _VERBOSE_RE.search(note):
            changed |= cls._apply(
                profile,
                "narration_verbosity",
                "terse",
                "RULE-VERBOSITY-TERSE",
                f"verdict {verdict}: {note.strip()[:160]}",
            )
        elif _TERSE_RE.search(note):
            changed |= cls._apply(
                profile,
                "narration_verbosity",
                "verbose",
                "RULE-VERBOSITY-VERBOSE",
                f"verdict {verdict}: {note.strip()[:160]}",
            )
        if _OVERRIDE_RE.search(note):
            profile["honda_overrides"] = int(profile.get("honda_overrides") or 0) + 1
            changed = True
            profile.setdefault("learned", []).append(
                {
                    "at": _now_iso(),
                    "rule": "RULE-SCOPE-BIG",
                    "evidence": f"verdict {verdict}: {note.strip()[:160]}",
                    "field": "honda_overrides",
                    "from": profile["honda_overrides"] - 1,
                    "to": profile["honda_overrides"],
                }
            )
            if profile["honda_overrides"] >= OVERRIDE_THRESHOLD:
                changed |= cls._apply(
                    profile,
                    "recommendation_scope",
                    "big-first",
                    "RULE-SCOPE-BIG",
                    f"{profile['honda_overrides']} Honda overrides recorded",
                )
        return changed

    @classmethod
    def apply_corrections(cls, profile: dict, texts: list[str]) -> bool:
        """Learn from session corrections. Called by session-end."""
        changed = False
        for text in texts:
            text = text or ""
            if _CHALLENGE_WANT_RE.search(text):
                changed |= cls._apply(
                    profile, "wants_challenges", True, "RULE-CHALLENGE-WANT", text[:160]
                )
            if _CHALLENGE_AVOID_RE.search(text):
                changed |= cls._apply(
                    profile, "wants_challenges", False, "RULE-CHALLENGE-AVOID", text[:160]
                )
            if _OPTIONS_FEW_RE.search(text):
                changed |= cls._apply(
                    profile, "options_style", "few", "RULE-OPTIONS-FEW", text[:160]
                )
            if _OPTIONS_MANY_RE.search(text):
                changed |= cls._apply(
                    profile,
                    "options_style",
                    "standard",
                    "RULE-OPTIONS-MANY",
                    text[:160],
                )
            if _STANCE_DIRECT_RE.search(text):
                changed |= cls._apply(
                    profile,
                    "preferred_stance",
                    "expert",
                    "RULE-STANCE-DIRECT",
                    text[:160],
                )
        return changed

    # -- calibration: how the drivers and buddy read the model --------------

    @staticmethod
    def narration(profile: dict) -> str:
        """Narration verbosity the loop drivers calibrate to."""
        return profile.get("narration_verbosity") or "normal"

    @staticmethod
    def recommendation_scope(profile: dict) -> str:
        """How pairing approaches are presented: honda-first or big-first."""
        return profile.get("recommendation_scope") or "honda-first"

    @staticmethod
    def calibration_line(profile: dict) -> str | None:
        """One compact line for buddy/stance display, or None when unlearned."""
        learned = profile.get("learned") or []
        explicit = profile.get("explicit") or []
        if not learned and not explicit and profile.get("preferred_stance") is None:
            return None
        bits = [
            f"narration={UserModel.narration(profile)}",
            f"challenges={profile.get('wants_challenges')}",
            f"options={profile.get('options_style')}",
            f"scope={UserModel.recommendation_scope(profile)}",
        ]
        if profile.get("preferred_stance"):
            bits.append(f"stance={profile['preferred_stance']}")
        bits.append(f"({len(learned)} learned)")
        return " ".join(bits)
