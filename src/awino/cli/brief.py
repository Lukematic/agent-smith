"""owns: brief

The stakeholder brief: mission, deliverables, decisions, beyond-the-Honda,
and gaps. Compiled from EXISTING state only -- the mission catechism, the
loop ledger trail, decisions.md, pairing briefs and plan artifacts, and
buddy's own audit findings. No new data collection: no subprocesses, no
git, no network. Read-only: it creates nothing, not even its own output
file.

Plain language, stakeholder-ready. Where the state cannot answer, the brief
says so ("no outcome verdict recorded yet") rather than inventing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from awino import heilmeier, loops, working_memory
from awino.cli import _echo, _workspace, app
from awino.enforce import Ledger, LoopEvent
from awino.paths import Workspace

# ── criterion judgments ──────────────────────────────────────────────────
#
# `awino loop close` records one outcome_verdict event per loop with a
# "verdict: yes|partial|no" detail plus "criteria_met: ...",
# "criteria_unmet: ...", "criteria_unjudgeable: ..." lists judged against the
# mission's success criteria at close time. The brief aggregates those per
# criterion: the latest verdict that mentions a criterion decides its
# status; a criterion no verdict ever mentioned is unjudgeable.

_CRITERION_LABEL_RE = re.compile(
    r"(?i)(?:^|;\s*)(criteria_met|criteria_unmet|criteria_unjudgeable)\s*:"
)
_LABEL_TO_STATUS = {
    "criteria_met": "met",
    "criteria_unmet": "unmet",
    "criteria_unjudgeable": "unjudgeable",
}
_NO_CRITERIA_NOTE = "no success criteria on file"
_NONE_PLACEHOLDER = "(none)"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _verdict_criteria(detail: str | None) -> dict[str, list[str]]:
    """criteria lists from one outcome_verdict detail, keyed met/unmet/unjudgeable."""
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


@dataclass(frozen=True)
class CriterionJudgment:
    criterion: str
    status: str  # "met" | "unmet" | "unjudgeable"
    judged_at: str | None
    loop_id: str | None


def _judge_criteria(
    criteria: list[str], events: list[LoopEvent]
) -> list[CriterionJudgment]:
    """Per-criterion met/unmet/unjudgeable from the outcome verdict trail.

    The latest verdict mentioning a criterion wins; criteria no verdict ever
    mentioned stay unjudgeable. Judgments recorded against older criterion
    text that no longer matches a live criterion are ignored -- the mission
    is living, so the brief judges live criteria only.
    """
    index = {_norm(criterion): criterion for criterion in criteria}
    judgments = {
        criterion: CriterionJudgment(criterion, "unjudgeable", None, None)
        for criterion in criteria
    }
    for event in events:  # loops.jsonl is oldest-first; later wins
        if event.kind != "outcome_verdict":
            continue
        parsed = _verdict_criteria(event.detail)
        for status in ("met", "unmet", "unjudgeable"):
            for item in parsed[status]:
                criterion = index.get(_norm(item))
                if criterion is not None:
                    judgments[criterion] = CriterionJudgment(
                        criterion, status, event.at, event.loop_id
                    )
    return [judgments[criterion] for criterion in criteria]


# ── deliverables ─────────────────────────────────────────────────────────
#
# What was built: every loop that closed (loop_closed or outcome_verdict),
# plus direct runs that closed. The proof is the ledger trail itself: event
# kinds with timestamps, validated artifact paths, the verdict's
# criteria counts. Test outputs are the criteria judgments -- the commands
# the mission wired were run at close time, and the verdict records how
# each claim fared.

_TASK_IN_STARTED = re.compile(r"(?m)^task:\s*(.+?)\s*$")
_VERDICT_IN_DETAIL = re.compile(r"(?:^|;)\s*verdict\s*:\s*(yes|partial|no)\b", re.I)
_VALIDATED_ARTIFACT_RE = re.compile(r"passed validation:\s*(.+?)\s*$", re.I)


@dataclass
class Deliverable:
    title: str
    kind: str  # "loop" | "run"
    ref: str
    verdict: str | None  # "yes" | "partial" | "no" | None
    proof: list[str] = field(default_factory=list)


def _deliverables_from_loops(events: list[LoopEvent]) -> list[Deliverable]:
    by_loop: dict[str, list[LoopEvent]] = {}
    for event in events:
        by_loop.setdefault(event.loop_id, []).append(event)
    out: list[Deliverable] = []
    for loop_id in sorted(by_loop):
        trail = by_loop[loop_id]
        kinds = {event.kind for event in trail}
        if not ({"loop_closed", "outcome_verdict"} & kinds):
            continue  # unfinished work is not a deliverable
        title = loop_id
        for event in trail:
            if event.kind == "loop_started":
                match = _TASK_IN_STARTED.search(event.detail or "")
                if match:
                    title = match.group(1)
                    break
        verdict: str | None = None
        met = unmet = 0
        for event in trail:
            if event.kind != "outcome_verdict":
                continue
            match = _VERDICT_IN_DETAIL.search(event.detail or "")
            if match:
                verdict = match.group(1).lower()
            parsed = _verdict_criteria(event.detail)
            met, unmet = len(parsed["met"]), len(parsed["unmet"])
        proof = [
            f"loops.jsonl: {len(trail)} events "
            f"({', '.join(sorted(kinds))})"
        ]
        validated = []
        for event in trail:
            if event.kind != "artifact_validated":
                continue
            match = _VALIDATED_ARTIFACT_RE.search(event.detail or "")
            if match and match.group(1) not in validated:
                validated.append(match.group(1))
        for artifact in validated:
            proof.append(f"validated artifact: {artifact}")
        if verdict is not None:
            proof.append(
                f"outcome verdict '{verdict}': {met} criteria met, "
                f"{unmet} unmet"
            )
        else:
            proof.append("no outcome verdict recorded for this loop")
        out.append(
            Deliverable(
                title=title, kind="loop", ref=loop_id, verdict=verdict, proof=proof
            )
        )
    return out


def _deliverables_from_runs(ledger: Ledger) -> list[Deliverable]:
    """Closed direct runs: work that never went through a loop driver."""
    out: list[Deliverable] = []
    base = ledger.base
    if not base.is_dir():
        return out
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not (child / "run.json").is_file():
            continue
        try:
            run = ledger.load(child.name)
        except Exception:
            continue
        if run.closed_at is None or run.loop != "direct":
            continue
        evidence = 0
        evidence_path = child / "evidence.jsonl"
        if evidence_path.is_file():
            evidence = sum(
                1
                for line in evidence_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        out.append(
            Deliverable(
                title=run.objective or child.name,
                kind="run",
                ref=child.name,
                verdict=None,
                proof=[
                    f"run ledger: closed {run.closed_at}",
                    f"run ledger: {evidence} evidence entries",
                ],
            )
        )
    return out


# ── decisions + the steel-man ────────────────────────────────────────────
#
# Each decision comes from decisions.md with its why. The steel-man -- the
# opposing case, named -- comes from the loop's pairing brief: candidate
# approaches with trade-offs and effort markers. The losing case is every
# candidate approach the decision does not name as chosen; the plan's
# decisions section (which must trace each approach choice to a pairing
# question) is the second witness for what was chosen.

_TRADEOFF_LINE_RE = re.compile(r"(?i)trade-?off|pro:|con:")


@dataclass
class DecisionBrief:
    id: str
    decision: str
    why: str
    opposing: str | None  # named losing case, or None when unrecorded
    opposing_effort: str | None
    opposing_note: str | None  # the losing approach's own trade-off lines
    loop_id: str | None


def _loop_contexts(
    state_root: Path, project_root: Path, events: list[LoopEvent]
) -> dict[str, dict]:
    """loop_id -> {"approaches": [(name, effort, role)], "plan_text": str}.

    A diagnostic survives the state it diagnoses: unreadable state files,
    unknown loop kinds, and missing artifacts yield an empty context, never
    an exception.
    """
    drivers = {
        "rpi": loops.RpiDriver,
        "ralph": loops.RalphDriver,
        "delegate": loops.DelegateDriver,
    }
    loop_ids = sorted({event.loop_id for event in events})
    contexts: dict[str, dict] = {}
    for loop_id in loop_ids:
        state_path = state_root / "loops" / f"{loop_id}.json"
        if not state_path.is_file():
            continue
        try:
            state = loops.LoopState.from_dict(
                json.loads(state_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError):
            continue
        try:
            kind = loops.kind_of(loop_id)
        except loops.LoopError:
            continue
        driver_cls = drivers.get(kind)
        if driver_cls is None:
            continue
        driver = driver_cls(
            project_root=project_root,
            loops_dir=state_root / "loops",
            skill_md=None,
            ledger=None,
        )
        approaches = driver.pairing_approaches(state)
        plan_text = ""
        plan_rel = getattr(state, "plan_artifact", "")
        if plan_rel:
            plan_path = project_root / plan_rel
            if plan_path.is_file():
                try:
                    plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    plan_text = ""
        bodies: dict[str, str] = {}
        pairing_rel = getattr(state, "pairing_artifact", "")
        if pairing_rel:
            pairing_path = project_root / pairing_rel
            if pairing_path.is_file():
                try:
                    brief_text = pairing_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    bodies = dict(
                        loops._split_approach_blocks(
                            loops._section_text(
                                brief_text, ("candidate approaches",)
                            )
                        )
                    )
                except OSError:
                    bodies = {}
        contexts[loop_id] = {
            "approaches": approaches,
            "plan_text": plan_text,
            "bodies": bodies,
        }
    return contexts


def _decision_loop_id(key: str | None, contexts: dict[str, dict]) -> str | None:
    """The loop a decision belongs to, from its "loop_id:qid" key."""
    if not key:
        return None
    for loop_id in contexts:
        if key == loop_id or key.startswith(loop_id + ":"):
            return loop_id
    return None


def _tradeoff_lines(body: str) -> str | None:
    lines = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and _TRADEOFF_LINE_RE.search(line)
    ]
    return "; ".join(lines[:2]) if lines else None


def _decision_briefs(
    state_root: Path, project_root: Path, events: list[LoopEvent]
) -> tuple[list[DecisionBrief], list[str]]:
    """Decisions with whys and the steel-man losing case, plus gap notes."""
    decisions = working_memory.Decisions(state_root)
    contexts = _loop_contexts(state_root, project_root, events)
    briefs: list[DecisionBrief] = []
    gaps: list[str] = []
    for entry in decisions.entries():
        if entry.superseded_by is not None:
            continue
        why = entry.why if entry.why_recorded else "(no why recorded)"
        loop_id = _decision_loop_id(entry.key, contexts)
        opposing: str | None = None
        opposing_effort: str | None = None
        opposing_note: str | None = None
        if loop_id is not None:
            ctx = contexts[loop_id]
            approaches: list[tuple[str, str, str]] = ctx["approaches"]
            if approaches:
                text = f"{entry.decision}\n{entry.why}".lower()
                plan_text = ctx["plan_text"].lower()
                losing = [
                    (name, effort, role)
                    for name, effort, role in approaches
                    if name.lower() not in text and name.lower() not in plan_text
                ]
                if losing:
                    name, effort, _role = losing[0]
                    opposing = name
                    opposing_effort = effort
                    opposing_note = _tradeoff_lines(ctx["bodies"].get(name, ""))
            else:
                gaps.append(
                    f"decision {entry.id}: the opposing case was not recorded "
                    f"(no candidate approaches in loop {loop_id}'s pairing brief)"
                )
        else:
            gaps.append(
                f"decision {entry.id}: the opposing case was not recorded "
                "(no pairing brief for this decision)"
            )
        briefs.append(
            DecisionBrief(
                id=entry.id,
                decision=entry.decision,
                why=why,
                opposing=opposing,
                opposing_effort=opposing_effort,
                opposing_note=opposing_note,
                loop_id=loop_id,
            )
        )
    return briefs, gaps


# ── beyond the Honda ─────────────────────────────────────────────────────
#
# What else could be delivered: the pairing briefs' unchosen approaches,
# each with its level-of-effort marker, labeled by source; plus buddy's
# forward-looking audit suggestions (also labeled), whose effort is honestly
# unestimated rather than invented.

_BEYOND_EFFORT_UNKNOWN = "(effort not estimated)"


@dataclass(frozen=True)
class BeyondItem:
    title: str
    effort: str
    source: str


def _beyond_honda(
    state_root: Path,
    project_root: Path,
    events: list[LoopEvent],
    workspace: Workspace,
) -> list[BeyondItem]:
    from awino.cli import buddy as _buddy  # lazy: same pattern as cli/project.py

    items: list[BeyondItem] = []
    contexts = _loop_contexts(state_root, project_root, events)
    for loop_id in sorted(contexts):
        ctx = contexts[loop_id]
        approaches: list[tuple[str, str, str]] = ctx["approaches"]
        if not approaches:
            continue
        plan_text = ctx["plan_text"].lower()
        for name, effort, _role in approaches:
            if name.lower() in plan_text:
                continue  # chosen: the plan's decisions section names it
            source = f"pair-planning brief (loop {loop_id})"
            title = name if plan_text else f"{name} (proposed; no plan decision yet)"
            items.append(
                BeyondItem(
                    title=title,
                    effort=effort if effort else "unstated",
                    source=source,
                )
            )
    # Buddy's forward-looking suggestions: the audit's own pending work,
    # recomputed from the same existing state (no new data collection).
    ledger = Ledger(state_root)
    trail = ledger.loop_events()
    decisions = working_memory.Decisions(state_root)
    for entry in decisions.why_less():
        items.append(
            BeyondItem(
                title=f"record the why for decision {entry.id} "
                f"'{entry.decision[:60]}'",
                effort=_BEYOND_EFFORT_UNKNOWN,
                source="buddy audit",
            )
        )
    for _event, _key, label in _buddy._unrecorded_decisions(trail, decisions):
        items.append(
            BeyondItem(
                title=f"record the decision made in '{label}' in decisions.md",
                effort=_BEYOND_EFFORT_UNKNOWN,
                source="buddy audit",
            )
        )
    checklist = working_memory.Checklist(state_root)
    stale = _buddy._stale_checklist_prompt(checklist)
    if stale is not None:
        items.append(
            BeyondItem(
                title=stale,
                effort=_BEYOND_EFFORT_UNKNOWN,
                source="buddy audit",
            )
        )
    for finding in _buddy._receipt_findings(workspace, ledger):
        items.append(
            BeyondItem(
                title=f"re-run the '{finding.phase}' skill step for loop "
                f"{finding.loop_id} ({finding.problem})",
                effort=_BEYOND_EFFORT_UNKNOWN,
                source="buddy audit",
            )
        )
    return items


# ── the brief ────────────────────────────────────────────────────────────


_VERDICT_WORD = {"yes": "accomplished", "partial": "partially accomplished", "no": "not accomplished"}


def _compile_brief(state_root: Path, project_root: Path, workspace: Workspace) -> list[str]:
    """Render the five-section stakeholder brief as plain-text lines."""
    lines: list[str] = []
    today = datetime.now(UTC).date().isoformat()
    lines.append(f"BRIEF  {project_root.name}  {today}")
    lines.append(
        "Compiled from existing state: heilmeier.json, loops.jsonl, "
        "decisions.md, pairing briefs. No new data collected."
    )
    lines.append("")
    gaps: list[str] = []
    ledger = Ledger(state_root)
    events = ledger.loop_events()

    # 1. Mission: objective + success criteria + whether they were met.
    lines.append("MISSION")
    cat = heilmeier.load(state_root)
    objective = (cat.answers.get("objective") or "").strip()
    if not objective:
        lines.append("  no mission on file")
        gaps.append("no mission on file -- set one with "
                    '`awino mission --set "objective=<one sentence>"`')
    else:
        lines.append(f"  Objective: {objective}")
    criteria = heilmeier.success_criteria(cat)
    if not criteria:
        lines.append("  no success criteria on file")
        gaps.append("no success criteria on file -- wire them with "
                    '`awino mission --set "exams=<claim> -> <verify command>"`')
    else:
        lines.append("  Success criteria, judged by outcome verdicts:")
        verdicts = [e for e in events if e.kind == "outcome_verdict"]
        if not verdicts:
            lines.append("    no outcome verdict recorded yet")
            gaps.append("no outcome verdict recorded yet -- close loops with "
                        "`awino loop close --verdict yes|partial|no` so the "
                        "mission can be judged")
            for criterion in criteria:
                lines.append(f"    [unjudgeable] {criterion}")
        else:
            for judgment in _judge_criteria(criteria, events):
                at = f" (judged {judgment.judged_at}, loop {judgment.loop_id})" if judgment.judged_at else " (never judged)"
                lines.append(f"    [{judgment.status}] {judgment.criterion}{at}")
                if judgment.status == "unjudgeable":
                    gaps.append(
                        f"criterion never judged by a verdict: '{judgment.criterion}'"
                    )
    lines.append("")

    # 2. Deliverables: what was built, with the proof.
    lines.append("DELIVERABLES")
    deliverables = _deliverables_from_loops(events) + _deliverables_from_runs(ledger)
    if not deliverables:
        lines.append("  nothing completed on file (no closed loops or runs)")
        gaps.append("no completed work on file -- deliverables appear here "
                    "once loops close")
    for item in deliverables:
        lines.append(f"  - {item.title} ({item.kind} {item.ref})")
        if item.verdict is not None:
            lines.append(f"    outcome: {_VERDICT_WORD[item.verdict]}")
        for proof in item.proof:
            lines.append(f"    proof: {proof}")
    lines.append("")

    # 3. Decisions: each with its why, plus the steel-man losing case.
    lines.append("DECISIONS")
    briefs, decision_gaps = _decision_briefs(state_root, project_root, events)
    gaps.extend(decision_gaps)
    if not briefs:
        lines.append("  no decisions recorded")
        gaps.append("no decisions recorded in decisions.md")
    for brief in briefs:
        lines.append(f"  - {brief.id}: {brief.decision}")
        lines.append(f"    why: {brief.why}")
        if brief.opposing is not None:
            steel = (
                f"    the case against: '{brief.opposing}' "
                f"(effort: {brief.opposing_effort or 'unstated'}) was "
                "considered and set aside"
            )
            if brief.opposing_note:
                steel += f" -- {brief.opposing_note}"
            lines.append(steel)
    lines.append("")

    # 4. Beyond the Honda: what else could be delivered, with effort.
    lines.append("BEYOND THE HONDA")
    beyond = _beyond_honda(state_root, project_root, events, workspace)
    if not beyond:
        lines.append("  nothing else proposed (no unchosen approaches, no pending audit items)")
    else:
        by_source: dict[str, list[BeyondItem]] = {}
        for item in beyond:
            by_source.setdefault(item.source, []).append(item)
        for source in sorted(by_source):
            lines.append(f"  from {source}:")
            for item in by_source[source]:
                lines.append(f"    - {item.title} (effort: {item.effort})")
    lines.append("")

    # 5. Gaps: everything this brief could not answer, in one place.
    lines.append("GAPS -- what this brief could not answer")
    if not gaps:
        lines.append("  none: every section drew on recorded state")
    for gap in gaps:
        lines.append(f"  - {gap}")
    return lines


@app.command("brief")
def brief_command() -> None:
    """Compile the stakeholder brief from existing project state."""
    workspace = _workspace()
    for line in _compile_brief(workspace.state_root, workspace.project.root, workspace):
        _echo(line)
