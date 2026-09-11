"""Self-contained FAIR proof packs: export the evidence, verify it anywhere.

A proof pack answers "prove it" with files instead of claims: the mission
(objective + success criteria), the approved plan hash from the run ledger,
the loop-transition trail from loops.jsonl, the recorded test outputs, the
outcome verdicts, and the compiled stakeholder brief. Every file is JSON or
Markdown -- no proprietary blobs (Interoperable, Accessible) -- and
``index.json`` lists every artifact with its SHA-256 so a third party can
find and check each one (Findable).

``awino proof export`` builds the pack from live project state.
``awino proof verify <pack-dir>`` re-checks it with no access to the
original project: hashes against the index, internal ledger consistency
(event order, loop transitions), verdicts referencing real loops, and the
brief's claims tracing to pack artifacts.

This module also owns the outcome-verdict detail parsing (the ``verdict:``
word and the per-criterion met/unmet/unjudgeable lists), which
``awino.cli.brief`` reuses so the brief and the pack can never disagree on
what a verdict said.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from awino import heilmeier
from awino.enforce import Ledger, LoopEvent

# ── outcome-verdict detail parsing ──────────────────────────────────────────
# `awino loop close` records one outcome_verdict event per loop with a
# "verdict: yes|partial|no" detail plus "criteria_met: ...",
# "criteria_unmet: ...", "criteria_unjudgeable: ..." lists judged against the
# mission's success criteria at close time.

_CRITERION_LABEL_RE = re.compile(
    r"(?i)(?:^|;\s*)(criteria_met|criteria_unmet|criteria_unjudgeable)\s*:"
)
_LABEL_TO_STATUS = {
    "criteria_met": "met",
    "criteria_unmet": "unmet",
    "criteria_unjudgeable": "unjudgeable",
}
_VERDICT_IN_DETAIL = re.compile(r"(?:^|;)\s*verdict\s*:\s*(yes|partial|no)\b", re.I)
_NO_CRITERIA_NOTE = "no success criteria on file"
_NONE_PLACEHOLDER = "(none)"


def verdict_word(detail: str | None) -> str | None:
    """The ``yes`` | ``partial`` | ``no`` word from an outcome_verdict detail."""
    match = _VERDICT_IN_DETAIL.search(detail or "")
    return match.group(1).lower() if match else None


def verdict_criteria(detail: str | None) -> dict[str, list[str]]:
    """Criteria lists from one outcome_verdict detail, keyed met/unmet/unjudgeable."""
    out: dict[str, list[str]] = {"met": [], "unmet": [], "unjudgeable": []}
    detail = detail or ""
    matches = list(_CRITERION_LABEL_RE.finditer(detail))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(detail)
        chunk = detail[match.end() : end].strip().rstrip(";").strip()
        if not chunk or chunk.lower() in {_NONE_PLACEHOLDER, _NO_CRITERIA_NOTE}:
            continue
        status = _LABEL_TO_STATUS[match.group(1).lower()]
        out[status] = [
            item.strip()
            for item in chunk.split(";")
            if item.strip() and item.strip().lower() != _NONE_PLACEHOLDER
        ]
    return out


# ── pack contents ───────────────────────────────────────────────────────────
# Every pack artifact is JSON or Markdown. The index lists every other file
# with its SHA-256; index.json itself is not self-listed.

PACK_FILES = (
    "README.md",
    "mission.json",
    "plan.json",
    "ledger.jsonl",
    "tests.json",
    "verdicts.json",
    "brief.md",
)

# Loop event kinds that carry test evidence: the suite results as the ledger
# recorded them. A verdict judges criteria; these events are the runs of the
# wired verify commands behind the judgment.
TEST_EVIDENCE_KINDS = (
    "verify_passed",
    "verify_failed",
    "success_criteria_evaluated",
    "artifact_validated",
)


def _pack_readme() -> str:
    return """# Proof pack

A self-contained FAIR proof pack for this project, exported by
`awino proof export`. Every file is JSON or Markdown.

| File | Contents |
| --- | --- |
| `mission.json` | The mission: objective + success criteria (from heilmeier.json). |
| `plan.json` | The latest hash-bound approved plan from the run ledger. |
| `ledger.jsonl` | The loop transition trail: every loop event, oldest first. |
| `tests.json` | Recorded test outputs: verify/exam events from the trail. |
| `verdicts.json` | Outcome verdicts with per-criterion met/unmet/unjudgeable lists. |
| `brief.md` | The compiled stakeholder brief. |
| `index.json` | Every artifact above with its SHA-256. |

Re-verify from this directory alone (no access to the original project):

```bash
awino proof verify <this-directory>
```

The verifier re-checks: hashes against `index.json`, the ledger trail's
internal consistency (event order, loop transitions), that every verdict
references a real loop from the trail, and that the brief's claims
(objective, criteria, closed loops) trace to pack artifacts.
"""


def mission_payload(state_root: Path) -> dict:
    """The mission: objective + success criteria, never invented."""
    cat = heilmeier.load(state_root)
    return {
        "objective": (cat.answers.get("objective") or "").strip(),
        "success_criteria": heilmeier.success_criteria(cat),
        "source": "heilmeier.json",
    }


def plan_payload(state_root: Path) -> dict:
    """The latest hash-bound approved plan across every run in the ledger.

    The plan bytes stay in the project's ledger; the pack carries the hash
    the human approved, which is what binds the approval to exact bytes.
    """
    run_base = state_root / "run"
    best: dict | None = None
    if run_base.is_dir():
        for child in sorted(run_base.iterdir()):
            run_json = child / "run.json"
            if not child.is_dir() or not run_json.is_file():
                continue
            try:
                data = json.loads(run_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            decisions = data.get("plan_decisions") or []
            for item in decisions:
                if not isinstance(item, dict) or item.get("decision") != "approved":
                    continue
                if best is None or str(item.get("decided_at", "")) > str(
                    best.get("decided_at", "")
                ):
                    best = item
    if best is None:
        return {
            "approved_plan_sha256": None,
            "note": "no approved plan in the run ledger",
        }
    return {
        "approved_plan_sha256": best.get("plan_sha256"),
        "plan_path": best.get("plan_path"),
        "approved_scope": best.get("approved_scope", []),
        "approver": best.get("approver"),
        "reason": best.get("reason", ""),
        "decided_at": best.get("decided_at"),
    }


def ledger_payload(events: list[LoopEvent]) -> str:
    """The loop transition trail as JSON Lines, oldest first."""
    lines = [json.dumps(asdict(event), sort_keys=True) for event in events]
    return "\n".join(lines) + ("\n" if lines else "")


def tests_payload(events: list[LoopEvent]) -> dict:
    """The suite evidence: recorded verify/exam events from the trail."""
    evidence = [
        {
            "loop_id": event.loop_id,
            "kind": event.kind,
            "at": event.at,
            "detail": event.detail,
            "passed": event.kind != "verify_failed",
        }
        for event in events
        if event.kind in TEST_EVIDENCE_KINDS
    ]
    return {
        "evidence": evidence,
        "note": (
            "no verify/exam events recorded in the loop trail"
            if not evidence
            else f"{len(evidence)} test-evidence event(s) from the loop trail"
        ),
    }


def verdicts_payload(events: list[LoopEvent]) -> dict:
    """Outcome verdicts with their per-criterion judgments."""
    verdicts = []
    for event in events:
        if event.kind != "outcome_verdict":
            continue
        criteria = verdict_criteria(event.detail)
        verdicts.append(
            {
                "loop_id": event.loop_id,
                "loop_kind": event.loop_kind,
                "at": event.at,
                "verdict": verdict_word(event.detail),
                "criteria_met": criteria["met"],
                "criteria_unmet": criteria["unmet"],
                "criteria_unjudgeable": criteria["unjudgeable"],
            }
        )
    return {
        "verdicts": verdicts,
        "note": (
            "no outcome verdicts recorded"
            if not verdicts
            else f"{len(verdicts)} outcome verdict(s)"
        ),
    }


def build_pack(state_root: Path, brief_text: str) -> dict[str, str]:
    """Assemble the pack: relative path -> file text, without index.json."""
    events = Ledger(state_root).loop_events()
    pack = {
        "README.md": _pack_readme(),
        "mission.json": json.dumps(mission_payload(state_root), indent=2) + "\n",
        "plan.json": json.dumps(plan_payload(state_root), indent=2) + "\n",
        "ledger.jsonl": ledger_payload(events),
        "tests.json": json.dumps(tests_payload(events), indent=2) + "\n",
        "verdicts.json": json.dumps(verdicts_payload(events), indent=2) + "\n",
        "brief.md": brief_text if brief_text.endswith("\n") else brief_text + "\n",
    }
    assert set(pack) == set(PACK_FILES), "pack contents drifted from PACK_FILES"
    return pack


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_pack(pack: dict[str, str], out_dir: Path, *, generator: str) -> Path:
    """Write the pack files, then index.json listing every artifact's hash."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for rel, text in pack.items():
        (out_dir / rel).write_text(text, encoding="utf-8")
    index = {
        "generator": generator,
        "generated_at": datetime.now(UTC).isoformat(),
        "artifacts": [
            {"path": rel, "sha256": _sha256(out_dir / rel)} for rel in sorted(pack)
        ],
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    return out_dir


# ── verification ────────────────────────────────────────────────────────────
# verify_pack reads ONLY the pack directory: no project state, no ledger, no
# git, no network. A third party re-verifies from the pack alone.


@dataclass(frozen=True)
class ProofFailure:
    """One failed re-verification check, naming the pack file at fault."""

    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _read_json(pack_dir: Path, rel: str, failures: list[ProofFailure]):
    try:
        return json.loads((pack_dir / rel).read_text(encoding="utf-8"))
    except FileNotFoundError:
        failures.append(ProofFailure(rel, "missing from pack"))
    except (OSError, ValueError) as exc:
        failures.append(ProofFailure(rel, f"unparseable: {exc}"))
    return None


def _check_integrity(
    pack_dir: Path, failures: list[ProofFailure]
) -> dict[str, str] | None:
    """Phase 1: every file matches the index; every file is indexed."""
    index = _read_json(pack_dir, "index.json", failures)
    if index is None:
        return None
    artifacts = index.get("artifacts")
    if not isinstance(artifacts, list):
        failures.append(ProofFailure("index.json", "'artifacts' is not a list"))
        return None
    wanted: dict[str, str] = {}
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
        ):
            failures.append(
                ProofFailure("index.json", f"malformed artifact entry: {item!r}")
            )
            continue
        wanted[item["path"]] = item["sha256"]
    for rel in sorted(wanted):
        path = pack_dir / rel
        if not path.is_file():
            failures.append(
                ProofFailure(rel, "listed in index.json but missing from pack")
            )
        elif _sha256(path) != wanted[rel]:
            failures.append(
                ProofFailure(rel, "hash mismatch: file changed after export")
            )
    for path in sorted(pack_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pack_dir).as_posix()
        if rel != "index.json" and rel not in wanted:
            failures.append(
                ProofFailure(rel, "present in pack but not listed in index.json")
            )
    return wanted if not failures else None


def _check_ledger(
    pack_dir: Path, failures: list[ProofFailure]
) -> list[dict] | None:
    """Phase 2: the trail parses, is append-ordered, and loops transition sanely."""
    path = pack_dir / "ledger.jsonl"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(ProofFailure("ledger.jsonl", f"unreadable: {exc}"))
        return None
    events: list[dict] = []
    previous_at: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            failures.append(
                ProofFailure("ledger.jsonl", f"line {lineno}: not JSON ({exc})")
            )
            continue
        if not isinstance(event, dict):
            failures.append(
                ProofFailure("ledger.jsonl", f"line {lineno}: not an object")
            )
            continue
        missing = [
            key
            for key in ("loop_id", "loop_kind", "phase", "kind", "at")
            if key not in event
        ]
        if missing:
            failures.append(
                ProofFailure(
                    "ledger.jsonl", f"line {lineno}: missing keys {missing}"
                )
            )
            continue
        try:
            at = datetime.fromisoformat(str(event["at"]))
        except ValueError:
            failures.append(
                ProofFailure(
                    "ledger.jsonl", f"line {lineno}: bad timestamp {event['at']!r}"
                )
            )
            continue
        if previous_at is not None and at.isoformat() < previous_at:
            failures.append(
                ProofFailure(
                    "ledger.jsonl",
                    f"line {lineno}: out of order (trail must be append-only)",
                )
            )
        previous_at = at.isoformat()
        events.append(event)
    first_kind: dict[str, str] = {}
    closed: set[str] = set()
    for event in events:
        loop_id = str(event["loop_id"])
        first_kind.setdefault(loop_id, str(event["kind"]))
        if event["kind"] == "loop_closed":
            if loop_id in closed:
                failures.append(
                    ProofFailure(
                        "ledger.jsonl", f"loop {loop_id}: closed twice"
                    )
                )
            closed.add(loop_id)
    for loop_id, kind in sorted(first_kind.items()):
        if kind != "loop_started":
            failures.append(
                ProofFailure(
                    "ledger.jsonl",
                    f"loop {loop_id}: trail starts with {kind!r}, not loop_started",
                )
            )
    return events


def _check_verdicts(
    pack_dir: Path, events: list[dict], failures: list[ProofFailure]
) -> list[dict] | None:
    """Phase 3: every verdict references a real loop from the trail."""
    data = _read_json(pack_dir, "verdicts.json", failures)
    if data is None:
        return None
    verdicts = data.get("verdicts")
    if not isinstance(verdicts, list):
        failures.append(ProofFailure("verdicts.json", "'verdicts' is not a list"))
        return None
    loop_ids = {str(event["loop_id"]) for event in events}
    for item in verdicts:
        if not isinstance(item, dict):
            failures.append(
                ProofFailure("verdicts.json", f"malformed verdict: {item!r}")
            )
            continue
        loop_id = str(item.get("loop_id", ""))
        if loop_id not in loop_ids:
            failures.append(
                ProofFailure(
                    "verdicts.json",
                    f"verdict for unknown loop {loop_id!r} (no loop_started in trail)",
                )
            )
        if item.get("verdict") not in ("yes", "partial", "no", None):
            failures.append(
                ProofFailure(
                    "verdicts.json",
                    f"loop {loop_id}: verdict {item.get('verdict')!r} "
                    "is not yes|partial|no",
                )
            )
    return verdicts


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _check_brief(
    pack_dir: Path,
    events: list[dict],
    failures: list[ProofFailure],
) -> None:
    """Phase 4: the brief's claims trace to pack artifacts.

    Objective, success criteria, and closed loops named in the brief must
    come from mission.json / the ledger trail -- a brief that invents its
    own facts fails here.
    """
    try:
        brief = (pack_dir / "brief.md").read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(ProofFailure("brief.md", f"unreadable: {exc}"))
        return
    flat = _norm(brief)
    mission = _read_json(pack_dir, "mission.json", failures)
    if mission is not None:
        objective = _norm(str(mission.get("objective") or ""))
        if objective and objective not in flat:
            failures.append(
                ProofFailure("brief.md", "mission objective not traced in brief")
            )
        criteria = mission.get("success_criteria") or []
        for criterion in criteria:
            if _norm(str(criterion)) not in flat:
                failures.append(
                    ProofFailure(
                        "brief.md",
                        "success criterion not traced in brief: "
                        f"{str(criterion)[:60]}",
                    )
                )
    deliverable_loops = sorted(
        {
            str(event["loop_id"])
            for event in events
            if event["kind"] in ("loop_closed", "outcome_verdict")
        }
    )
    for loop_id in deliverable_loops:
        if loop_id not in brief:
            failures.append(
                ProofFailure("brief.md", f"closed loop {loop_id} not named in brief")
            )


def _check_plan(pack_dir: Path, failures: list[ProofFailure]) -> None:
    """Phase 5: the approved plan hash is well-formed when present."""
    plan = _read_json(pack_dir, "plan.json", failures)
    if plan is None:
        return
    digest = plan.get("approved_plan_sha256")
    if digest is None:
        return  # honestly recorded as absent; nothing to check
    if not isinstance(digest, str) or not _SHA256_RE.match(digest):
        failures.append(
            ProofFailure("plan.json", f"approved_plan_sha256 is not a SHA-256: {digest!r}")
        )


def verify_pack(pack_dir: Path) -> list[ProofFailure]:
    """Re-verify a proof pack from the pack directory alone.

    Never touches the original project: every check reads only files under
    ``pack_dir``. Returns the failures; empty means the pack re-verifies.
    """
    failures: list[ProofFailure] = []
    if not pack_dir.is_dir():
        return [ProofFailure(str(pack_dir), "not a directory")]
    # Phase 1 is the gate: content checks run only on an intact pack, so a
    # tampered file is named once instead of cascading into confusion.
    if _check_integrity(pack_dir, failures) is None:
        return failures
    events = _check_ledger(pack_dir, failures)
    if events is None:
        return failures
    _check_verdicts(pack_dir, events, failures)
    _check_brief(pack_dir, events, failures)
    _check_plan(pack_dir, failures)
    return failures
