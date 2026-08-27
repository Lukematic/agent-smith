"""Second-pass rubric review for specs and plans, before implementation proceeds.

Adapted from obra/superpowers' document-review-system-design pattern: a spec
(``.smith/project.yaml`` from ``onboarding.py``) or a plan
(``thoughts/plans/*.md`` from ``skills/awino-rpi``) gets a fixed-rubric review
pass, separate from whoever wrote the document, before work on it begins.

This module does **not** spawn a real subagent. ``spawn.spawn_one`` runs a
headless agent CLI (``claude``/``goose``/``codex``) as a subprocess, which is
exactly the mechanism a Task-tool-driven orchestrator does not use and a CLI
subprocess test cannot exercise deterministically. The reviewer here is a
pluggable callable (``ReviewerFn``): the orchestrating agent that has a real
Task tool wires in a function that spawns the ``Role.REVIEWER`` subagent
built by ``build_reviewer_assignment`` and returns its verdict; the CLI's own
default reviewer (``score_document``) is a deterministic rubric scorer that
runs standalone so ``awino review-doc`` is usable without any agent CLI at
all. Tests inject a third kind of fake reviewer to drive the iteration caps.

The verdict is never invented. It comes from ``enforce.ReviewVerdict`` (reused
rather than duplicated) and is recorded through ``Ledger.attest`` -- the same
mechanism ``completion_review.gate_review`` uses for the ``Gate.REVIEWED``
attestation -- so a doc review is never a parallel, unaccounted-for system.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from smith.enforce import Gate, Ledger, LedgerError, ReviewVerdict
from smith.spawn import Assignment, Role

MAX_ITERATIONS = 5
MAX_SAME_DISAGREEMENT = 3

# The rubric is fixed by this seed's spec, not configurable per call.
SPEC_RUBRIC: tuple[str, ...] = (
    "Completeness",
    "Coverage",
    "Consistency",
    "Clarity",
    "YAGNI",
)
PLAN_RUBRIC: tuple[str, ...] = (*SPEC_RUBRIC, "Spec Alignment", "Task Decomposition", "Chunk Size")

# Heuristic placeholder/incompleteness markers a document should not still
# contain once it is ready for review. Case-insensitive whole-token matches.
_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "todo",
    "tbd",
    "fixme",
    "xxx",
    "lorem ipsum",
    "placeholder",
    "wip",
)
_PLACEHOLDER_RE = re.compile(
    "|".join(re.escape(term) for term in _PLACEHOLDER_MARKERS), re.IGNORECASE
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$", re.MULTILINE)


class RubricKind:
    """Which fixed rubric applies to a document."""

    SPEC = "spec"
    PLAN = "plan"

    @staticmethod
    def rubric(kind: str) -> tuple[str, ...]:
        if kind == RubricKind.PLAN:
            return PLAN_RUBRIC
        if kind == RubricKind.SPEC:
            return SPEC_RUBRIC
        raise ValueError(f"unknown rubric kind {kind!r}, expected 'spec' or 'plan'")


@dataclass(frozen=True)
class Issue:
    """One rubric finding against a document.

    ``category`` is one of the fixed rubric row names above. Category is what
    the same-disagreement cap tracks: the reviewer raising the identical
    category repeatedly, whether or not the exact wording changes, is the
    signal that a fix is not landing.
    """

    category: str
    detail: str


@dataclass(frozen=True)
class DocReviewResult:
    """One reviewer pass's outcome, independent of how it was iterated."""

    verdict: ReviewVerdict
    issues: tuple[Issue, ...]
    notes: str = ""

    @property
    def approved(self) -> bool:
        return self.verdict is ReviewVerdict.APPROVED


class ReviewerFn(Protocol):
    """A pluggable reviewer: given rendered rubric prompt text, return a verdict.

    The real implementation an orchestrating agent wires in spawns the
    ``Assignment`` from ``build_reviewer_assignment`` via its Task tool and
    parses the subagent's structured verdict. This CLI ships
    ``score_document`` as the default, which needs no subagent at all.
    """

    def __call__(self, document_text: str, rubric: tuple[str, ...]) -> DocReviewResult: ...


def build_reviewer_assignment(
    agent_id: str, document_path: Path, document_text: str, kind: str
) -> Assignment:
    """A ``Role.REVIEWER`` assignment scoped read-only to ``document_path``.

    This is what the orchestrating agent's Task tool call should spawn. The
    CLI never spawns it itself (see module docstring); building it is as far
    as a standalone process can responsibly go.
    """
    rubric = RubricKind.rubric(kind)
    table = "\n".join(f"| {row} | |" for row in rubric)
    objective = f"""Review the {kind} document at `{document_path}` against this fixed rubric.
For each row, decide whether the document satisfies it. Any unmet row is an
Issue Found and must be reported with its exact rubric category name and a
concrete detail, so re-review can tell whether the same issue recurs.

## Rubric

| Category | Verdict (met / not met + detail) |
| --- | --- |
{table}

## Document content

```
{document_text}
```

State a final verdict of exactly one of: approved, changes-requested, blocked.
"""
    return Assignment(
        agent_id=agent_id,
        role=Role.REVIEWER,
        objective=objective,
        file_scope=[],
        context_paths=[str(document_path)],
        verification="",
    )


def _sections(document_text: str) -> list[tuple[str, str, int]]:
    """Split a document into (heading, body, level) triples. One synthetic
    leading section holds any text before the first heading."""
    matches = list(_HEADING_RE.finditer(document_text))
    if not matches:
        return [("(document)", document_text, 0)]
    sections: list[tuple[str, str, int]] = []
    if matches[0].start() > 0:
        sections.append(("(preamble)", document_text[: matches[0].start()], 0))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document_text)
        level = len(match.group(1))
        sections.append((match.group(2).strip(), document_text[start:end], level))
    return sections


def score_document(document_text: str, _rubric: tuple[str, ...]) -> DocReviewResult:
    """Deterministic default reviewer: apply the fixed rubric with heuristics.

    This is real, inspectable logic, not a stand-in for a subagent: it flags
    placeholder markers (Completeness/Clarity), empty sections (Completeness),
    and duplicate headings (Consistency). It intentionally does not attempt
    the judgement-heavy rows (YAGNI, Spec Alignment, Task Decomposition,
    Chunk Size) with heuristics that would be theater; those stay unflagged
    here and are exactly what a real subagent reviewer adds on top.
    """
    issues: list[Issue] = []
    stripped = document_text.strip()
    if not stripped:
        issues.append(Issue("Completeness", "document is empty"))
        return DocReviewResult(ReviewVerdict.CHANGES_REQUESTED, tuple(issues))

    for match in _PLACEHOLDER_RE.finditer(document_text):
        line_no = document_text.count("\n", 0, match.start()) + 1
        term = match.group(0)
        issues.append(Issue("Completeness", f"placeholder marker '{term}' at line {line_no}"))

    seen_headings: Counter[str] = Counter()
    sections = _sections(document_text)
    for index, (heading, body, level) in enumerate(sections):
        normalised = heading.strip().lower()
        is_real_heading = heading not in {"(document)", "(preamble)"}
        if is_real_heading:
            seen_headings[normalised] += 1
        # A heading immediately followed by a deeper-level heading is a
        # container (e.g. "# Spec" over "## Goals"), not a leaf section, so
        # its own empty body is not a Completeness gap.
        next_level = sections[index + 1][2] if index + 1 < len(sections) else None
        is_container = next_level is not None and next_level > level
        if is_real_heading and not body.strip() and not is_container:
            issues.append(Issue("Completeness", f"section '{heading}' has no content"))

    for heading, count in seen_headings.items():
        if count > 1:
            issues.append(Issue("Consistency", f"heading '{heading}' repeated {count} times"))

    if "Clarity" not in {i.category for i in issues}:
        vague_terms = ("etc.", "somehow", "some way", "and so on")
        for term in vague_terms:
            if term in document_text.lower():
                issues.append(Issue("Clarity", f"vague phrasing '{term}' found"))
                break

    if not issues:
        return DocReviewResult(ReviewVerdict.APPROVED, ())
    return DocReviewResult(ReviewVerdict.CHANGES_REQUESTED, tuple(issues))


@dataclass
class IterationRecord:
    """One completed iteration of the review loop."""

    iteration: int
    result: DocReviewResult


@dataclass
class LoopOutcome:
    """The result of running the capped fix -> re-review loop to a stop.

    ``surfaced_to_human`` is set whenever a cap is hit; this is a deliberate
    stop for a human to look at, never a silent failure or an exception the
    caller has to guess the meaning of.
    """

    iterations: list[IterationRecord] = field(default_factory=list)
    stopped_reason: str = ""
    surfaced_to_human: bool = False

    @property
    def final(self) -> DocReviewResult | None:
        return self.iterations[-1].result if self.iterations else None

    @property
    def approved(self) -> bool:
        return self.final is not None and self.final.approved


def _same_disagreement_message(tried: int) -> str:
    """Mirrors healing.py's THREE_STRIKES user-facing wording exactly."""
    return f"THREE_STRIKES after {tried}. Stop and escalate rather than retrying."


def run_review_loop(
    document_text_fn,
    rubric: tuple[str, ...],
    reviewer: ReviewerFn,
    *,
    max_iterations: int = MAX_ITERATIONS,
    max_same_disagreement: int = MAX_SAME_DISAGREEMENT,
) -> LoopOutcome:
    """Run the capped fix -> re-review loop until Approved or a cap is hit.

    ``document_text_fn`` is called once per iteration to read the current
    document text, so a caller can re-read the file after each fix attempt
    (the loop itself does not fix anything; fixing is the orchestrator's job
    between iterations, which is why this takes a callable rather than one
    fixed string).

    Two independent caps apply:

    - ``max_iterations``: a hard ceiling on total review passes, regardless
      of content. Hitting it surfaces to the human rather than erroring.
    - ``max_same_disagreement``: if the reviewer raises the same rubric
      category as an Issue three iterations running, that specific
      disagreement is not resolving and also surfaces to the human, even if
      other categories are changing.
    """
    outcome = LoopOutcome()
    consecutive_category_counts: Counter[str] = Counter()
    previous_categories: set[str] = set()

    for iteration in range(1, max_iterations + 1):
        text = document_text_fn()
        result = reviewer(text, rubric)
        outcome.iterations.append(IterationRecord(iteration, result))

        if result.approved:
            outcome.stopped_reason = f"approved at iteration {iteration}"
            return outcome

        current_categories = {issue.category for issue in result.issues}
        for category in current_categories:
            if category in previous_categories:
                consecutive_category_counts[category] += 1
            else:
                consecutive_category_counts[category] = 1
        for category in list(consecutive_category_counts):
            if category not in current_categories:
                consecutive_category_counts[category] = 0
        previous_categories = current_categories

        for _category, streak in consecutive_category_counts.items():
            if streak >= max_same_disagreement:
                outcome.stopped_reason = _same_disagreement_message(streak)
                outcome.surfaced_to_human = True
                return outcome

    outcome.stopped_reason = (
        f"MAX_ITERATIONS_REACHED after {max_iterations} review iteration(s) "
        "without approval. Stop and escalate to a human rather than looping further."
    )
    outcome.surfaced_to_human = True
    return outcome


def attest_review(ledger: Ledger, run_id: str, outcome: LoopOutcome, document_path: Path) -> None:
    """Record the loop's final verdict via ``Ledger.attest``.

    Uses ``Gate.REVIEWED`` -- the existing gate for reviewer output -- rather
    than inventing a parallel gate or discarding the verdict silently. Raises
    ``LedgerError`` under the same conditions ``attest`` always does (e.g. an
    invalid plan, or an escape-hatch term in the note), which callers should
    let propagate exactly as every other ``attest`` call site does.
    """
    final = outcome.final
    if final is None:
        raise LedgerError("DOC_REVIEW_EMPTY: no review iteration ran, nothing to attest")
    note = (
        f"doc review {document_path} verdict={final.verdict} "
        f"iterations={len(outcome.iterations)} stopped={outcome.stopped_reason}"
    )
    ledger.attest(run_id, Gate.REVIEWED, note)
