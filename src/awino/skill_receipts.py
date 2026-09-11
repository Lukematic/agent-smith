"""owns: awino loop skill receipts

Skill receipts: skill usage as a gated, checkable step.

A receipt is a small JSON file in the project state dir
(``.awino/receipts/<loop-id>--<phase>--<skill>.json``) attesting "this
artifact was produced under skill <name> for these inputs". The honest
write point is the loop driver: it writes (or refreshes) the receipt when
the phase's artifact validates -- a model hand-writing a receipt file
proves nothing by itself. What makes the receipt a require-point is the
phase-gate validator in ``awino.loops``: before advancing FROM a phase,
every required skill must have a receipt whose ``inputs_hash`` matches the
phase's current actual inputs and whose ``output_artifact`` exists and
passes that artifact's own validation.

Exactly what goes into ``inputs_hash`` (SHA-256 over JSON with sorted
keys, UTF-8) -- exactly the phase's consumed inputs, nothing else:

- ``artifact_path``: the repo-relative path of the phase's output artifact
- ``criteria_hash``: the mission's objective + success criteria hash
  (``awino.heilmeier.criteria_hash``), live at validation time
- ``seed_id``: the loop's linked seed id, or "" when the loop has none

Cross-phase and cross-loop replay need no extra namespacing in the hash:
the artifact path already differs per phase, and receipts live at
``<loop-id>--<phase>--<skill>.json`` per loop.

``version`` is the first 12 hex chars of the SHA-256 of the skill's
``SKILL.md``: skill frontmatter carries no version field, so the content
hash pins exactly which skill text the phase ran under.

``artifact_hash`` is the SHA-256 of the output artifact's bytes at the
moment validation passed. The advancement gate uses it so a byte-identical
artifact does not need its validator re-run: some validators (e.g. the
ralph retry phase) record progress as a side effect and are not idempotent,
so re-running them on an unchanged artifact would wrongly fail. When the
artifact changed after the receipt was written, the gate re-runs the
artifact's own validator and reports its problems.

Receipt status per phase, as shown on the checklist item:

- ``received``: a receipt exists and validates
- ``missing``: no receipt on file
- ``invalid``: a receipt file exists but fails -- malformed JSON, stale
  inputs_hash, missing output artifact, or an artifact that fails its own
  validation

This module is storage, hashing, and parsing; the drivers, the CLI, the
checklist, and buddy own the hooks that call it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from awino import loops
    from awino.enforce import LoopEvent

RECEIPTS_DIRNAME = "receipts"

#: The receipt statuses the checklist shows per phase.
RECEIPT_STATUSES = ("received", "missing", "invalid")


@dataclass(frozen=True)
class SkillReceipt:
    """One attestation: the phase's artifact was produced under a skill."""

    skill: str
    version: str
    phase: str
    inputs_hash: str
    output_artifact: str  # repo-relative path
    timestamp: str  # ISO-8601
    # SHA-256 of the output artifact's bytes when validation passed. Lets the
    # advancement gate accept a byte-identical artifact without re-running a
    # validator that is not idempotent. None on receipts written before this
    # field existed (or hand-written ones): the gate then re-runs validation.
    artifact_hash: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SkillReceipt:
        return cls(**data)


def inputs_hash(
    *,
    artifact_path: str,
    criteria_hash: str,
    seed_id: str | None,
) -> str:
    """Bind a receipt to exactly the inputs the phase consumed.

    See the module docstring for the full field list.
    """
    payload = {
        "artifact_path": artifact_path,
        "criteria_hash": criteria_hash or "",
        "seed_id": seed_id or "",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def skill_version(skills_dir: Path, skill: str) -> str:
    """Pin which skill text the phase ran under.

    Skill frontmatter has no version field, so the version is the content
    hash of the skill's ``SKILL.md`` (first 12 hex chars). "unknown" when
    the skill document is unreadable -- the pairing-brief validator rejects
    unknown skills before a receipt is ever written for one, so this only
    fires on a skill deleted mid-loop.
    """
    doc = skills_dir / skill / "SKILL.md"
    try:
        content = doc.read_bytes()
    except OSError:
        return "unknown"
    return hashlib.sha256(content).hexdigest()[:12]


def receipt_path(state_root: Path, loop_id: str, phase: str, skill: str) -> Path:
    """Where a receipt lives: the project state dir, never the repo tree."""
    return state_root / RECEIPTS_DIRNAME / f"{loop_id}--{phase}--{skill}.json"


def file_sha256(path: Path) -> str | None:
    """SHA-256 of a file's bytes. None when the file is unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def write_receipt(
    state_root: Path,
    *,
    loop_id: str,
    skill: str,
    version: str,
    phase: str,
    inputs_hash: str,
    output_artifact: str,
    artifact_hash: str | None = None,
) -> SkillReceipt:
    """Write (or overwrite) one receipt. Returns what was written."""
    receipt = SkillReceipt(
        skill=skill,
        version=version,
        phase=phase,
        inputs_hash=inputs_hash,
        output_artifact=output_artifact,
        timestamp=datetime.now(UTC).isoformat(),
        artifact_hash=artifact_hash,
    )
    path = receipt_path(state_root, loop_id, phase, skill)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    return receipt


def read_receipt(state_root: Path, *, loop_id: str, phase: str, skill: str) -> SkillReceipt | None:
    """Read one receipt. None when absent or unreadable: a corrupt receipt
    parses to no receipt -- the gate reports it as malformed (invalid),
    never as valid. Use receipt_exists() to tell missing from malformed."""
    path = receipt_path(state_root, loop_id, phase, skill)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return SkillReceipt.from_dict(data)
    except TypeError:
        return None


def receipt_exists(state_root: Path, *, loop_id: str, phase: str, skill: str) -> bool:
    """The receipt file exists on disk, whether or not it parses."""
    return receipt_path(state_root, loop_id, phase, skill).is_file()


# ── the plan's per-phase required-skills declaration ─────────────────────────
# The pairing brief carries a "## Required skills" section:
#
#   ## Required skills
#
#   - research: awino-rpi
#   - pair-plan: awino-rpi
#   - plan: awino-rpi
#
# One "- <phase>: <skill>[, <skill>...]" item per phase. Only skills that
# exist under skills/ are honest; the driver rejects anything else.

_REQUIRED_SKILL_ITEM_RE = re.compile(r"^\s*[-*+]\s*([\w-]+)\s*:\s*(.+?)\s*$")


def parse_required_skills(section_text: str) -> dict[str, list[str]]:
    """Parse a Required-skills section into phase -> [skill, ...].

    Takes the already-extracted section text (the driver cuts it out of the
    brief with the same heading logic as every other section). Repeated
    items for one phase accumulate; comma-separated skills split.
    """
    declared: dict[str, list[str]] = {}
    for line in section_text.splitlines():
        match = _REQUIRED_SKILL_ITEM_RE.match(line)
        if not match:
            continue
        phase = match.group(1)
        names = [name.strip() for name in match.group(2).split(",")]
        names = [name for name in names if name]
        if names:
            declared.setdefault(phase, []).extend(names)
    return declared


@dataclass(frozen=True)
class ReceiptFinding:
    """One completed phase lacking a valid skill receipt (buddy's audit)."""

    loop_id: str
    loop_kind: str
    phase: str
    skill: str
    status: str  # "missing" | "invalid"
    problem: str  # the human-readable why


def find_receipt_problems(
    driver: loops.LoopDriver,
    state: loops.LoopState,
    events: list[LoopEvent],
) -> list[ReceiptFinding]:
    """Completed phases without a valid receipt for each required skill.

    A phase counts as completed when its artifact validated and the loop has
    moved on (the loop's current phase, or "done", excludes it; a
    phase_reentered after the validation un-completes it). Pure: it reads
    state, events, and receipt files, and writes nothing -- buddy's report,
    buddy's --fix, and the tests all share it.
    """
    loop_id = state.id
    validated: dict[str, bool] = {}
    for event in events:
        if event.loop_id != loop_id:
            continue
        if event.kind == "artifact_validated":
            validated[event.phase] = True
        elif event.kind == "phase_reentered":
            validated[event.phase] = False
    findings: list[ReceiptFinding] = []
    for phase in driver.phase_order:
        if not validated.get(phase):
            continue
        if phase == state.phase:
            continue  # still in flight, not completed
        for skill in driver.required_skills(state, phase):
            problem = driver.validate_receipt(state, phase, skill)
            if problem is None:
                continue
            status = "missing" if problem.startswith("no skill receipt") else "invalid"
            findings.append(
                ReceiptFinding(
                    loop_id=loop_id,
                    loop_kind=driver.loop_kind,
                    phase=phase,
                    skill=skill,
                    status=status,
                    problem=problem,
                )
            )
    return findings
