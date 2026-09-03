"""The Heilmeier catechism as a living mission document.

`awino onboard` asks what the mission is. Heilmeier asks whether it deserves to
exist: no-jargon objective, limits of current practice, what is new, who cares,
risks, cost, time, and the exams that prove success. This module keeps those
eight answers as a document that fills itself as much as it honestly can from
what the project already knows, asks for the rest one gap at a time, and wires
the exams to real verification commands so a mission's "final exam" is a
command the ledger can run - not a sentence.

Each question carries the stance the conversation should take while answering
it: first-principles for Q1-Q4 (forced clarity), steel-man for risks (the
strongest case against the plan is where "calculated" comes from in calculated
risk), advisor for the rest.

insights() is the part that elucidates: it cross-references the answers and
surfaces what they imply - a risk with no exam covering it, an exam with no
command, jargon in the objective, open work not tied to any exam, and the
research questions those gaps suggest. Derived, labeled, never asserted as fact.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Question:
    key: str
    text: str
    stance: str


QUESTIONS: tuple[Question, ...] = (
    Question("objective", "What are you trying to do? No jargon.", "first-principles"),
    Question(
        "today",
        "How is it done today, and what are the limits of current practice?",
        "first-principles",
    ),
    Question(
        "new_approach", "What is new in your approach and why will it succeed?", "first-principles"
    ),
    Question(
        "who_cares", "Who cares? If you succeed, what difference will it make?", "first-principles"
    ),
    Question("risks", "What are the risks?", "steel-man"),
    Question("cost", "How much will it cost?", "advisor"),
    Question("duration", "How long will it take?", "advisor"),
    Question(
        "exams",
        "What are the mid-term and final exams? One per line; 'claim -> command' wires a verify.",
        "advisor",
    ),
)

_JARGON = re.compile(
    r"\b(llm|orchestrat\w*|harness|agentic|pipeline|framework|leverage|synerg\w*|paradigm|rag|mcp)\b",
    re.I,
)
_EXAM_ARROW = re.compile(r"^(.*?)\s*->\s*(.+)$")


@dataclass
class Catechism:
    answers: dict[str, str]
    source: dict[str, str] = field(default_factory=dict)

    def next_gap(self) -> Question | None:
        for q in QUESTIONS:
            if not self.answers.get(q.key, "").strip():
                return q
        return None

    def _exam_lines(self) -> list[str]:
        return [ln.strip() for ln in self.answers.get("exams", "").splitlines() if ln.strip()]

    def exam_commands(self) -> list[str]:
        return [m.group(2).strip() for ln in self._exam_lines() if (m := _EXAM_ARROW.match(ln))]

    def exams_without_commands(self) -> list[str]:
        return [ln for ln in self._exam_lines() if not _EXAM_ARROW.match(ln)]


def _path(state_root: Path) -> Path:
    return state_root / "heilmeier.json"


def _mission_from_yaml(state_root: Path) -> str | None:
    cfg = state_root / "project.yaml"
    if not cfg.is_file():
        return None
    m = re.search(r"^mission:\s*(.+)$", cfg.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else None


def load(state_root: Path) -> Catechism:
    """Explicit answers first; then prefill honestly-derivable gaps, labeled."""
    stored: dict = {}
    if _path(state_root).is_file():
        stored = json.loads(_path(state_root).read_text(encoding="utf-8"))
    cat = Catechism(dict(stored.get("answers", {})), dict(stored.get("source", {})))
    if not cat.answers.get("objective"):
        mission = _mission_from_yaml(state_root)
        if mission:
            cat.answers["objective"] = mission
            cat.source["objective"] = "project.yaml"
    return cat


def save(state_root: Path, cat: Catechism) -> Path:
    _path(state_root).parent.mkdir(parents=True, exist_ok=True)
    _path(state_root).write_text(
        json.dumps({"answers": cat.answers, "source": cat.source}, indent=2), encoding="utf-8"
    )
    return _path(state_root)


def insights(cat: Catechism, *, open_seeds: list[str]) -> list[str]:
    """What the answers imply when cross-referenced. Derived, not asserted."""
    out: list[str] = []
    obj = cat.answers.get("objective", "")
    if obj and (hits := sorted({m.lower() for m in _JARGON.findall(obj)})):
        out.append(f"Objective still contains jargon ({', '.join(hits)}); Q1 asks for none.")

    risks = [r.strip() for r in cat.answers.get("risks", "").splitlines() if r.strip()]
    exams_text = cat.answers.get("exams", "").lower()
    for risk in risks:
        words = re.findall(r"[a-z]{4,}", risk.lower())
        if not any(w in exams_text for w in words):
            out.append(f"Risk with no exam covering it: '{risk}'.")
            out.append(
                f"RESEARCH  how would you detect early that '{risk}' is happening? That detector is a missing mid-term exam."
            )

    for exam in cat.exams_without_commands():
        out.append(f"Exam without a command (cannot be gated): '{exam}'.")

    if cat.answers.get("cost") and not re.search(
        r"mid[- ]?term|midterm|week|sprint|phase", exams_text
    ):
        out.append(
            "Cost is stated but no mid-term exam exists; nothing checks the spend before the end."
        )

    if open_seeds:
        untied = [
            s
            for s in open_seeds
            if not any(w in exams_text for w in re.findall(r"[a-z]{5,}", s.lower()))
        ]
        if untied:
            out.append(
                f"{len(untied)} open seed(s) not tied to any exam - work that no exam would notice finishing."
            )

    if cat.answers.get("who_cares") and cat.answers.get("new_approach"):
        out.append(
            "ALTERNATE  steel-man check: name the simplest existing tool that already gives 'who cares' 80% of the difference; if you cannot, Q3 is strong."
        )
    return out


def render(state_root: Path, cat: Catechism, *, open_seeds: list[str]) -> Path:
    lines = ["# Mission - Heilmeier catechism (living document)", ""]
    for q in QUESTIONS:
        ans = cat.answers.get(q.key, "").strip()
        src = cat.source.get(q.key)
        tag = f"  _(from {src})_" if src and src != "human" else ""
        lines += [f"## {q.text}{tag}", "", ans if ans else "(unanswered)", ""]
    lines += ["## Derived insights", ""]
    found = insights(cat, open_seeds=open_seeds)
    lines += [f"- {i}" for i in found] or ["- none yet"]
    cmds = cat.exam_commands()
    if cmds:
        lines += ["", "## Exam commands (gate-ready)", ""] + [f"- `{c}`" for c in cmds]
    path = state_root / "MISSION.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
