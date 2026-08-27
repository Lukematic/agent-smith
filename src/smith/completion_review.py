"""``gate review``: an independent completion-review gate, run before ``gate close``.

An agent that both does the work and certifies the work is done has no
independent check on either half. This module gives ``gate review`` four
things to do before a human's verdict is recorded, none of which trust the
agent's word:

1. Extract any acceptance criteria named in the objective or a linked Seed's
   description (a plain bullet/numbered-list heuristic, not NLP) and require
   each to be explicitly marked met or not-met.
2. Run the project's own verification commands (via ``Toolchain``) and record
   them as real ``Evidence`` through the existing ``Ledger.record`` mechanism,
   never a parallel verification system.
3. Reuse ``detect_scope_violations`` and ``detect_test_weakening`` from
   ``enforce`` against a git diff, rather than re-implementing either check.
4. Run ``tidy --dry-run`` and report findings without touching the
   filesystem, then classify them: clutter inside the run's declared
   ``file_scope`` blocks close, because it is part of what is being shipped;
   clutter anywhere else is informational only.

The result of all four is written as a single ``ProvenanceRecord`` via
``Ledger.record_provenance``, which ``gate close`` can then require exists.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from smith.enforce import (
    Evidence,
    Gate,
    Ledger,
    ProvenanceGateResult,
    ProvenanceRecord,
    Run,
    detect_scope_violations,
    detect_test_weakening,
)
from smith.paths import SmithPaths
from smith.tidy import Clutter, Finding, Tidier
from smith.toolchain import Toolchain

# Heuristic acceptance-criteria bullet markers: "- ", "* ", "1. ", "1) ".
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(\S.*)$")

# Gates a toolchain can actually verify by running a command.
TOOLCHAIN_GATES: tuple[tuple[str, Gate], ...] = (
    ("test", Gate.TESTED),
    ("lint", Gate.LINTED),
)


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One line-item pulled from the objective or a linked Seed's description."""

    text: str
    met: bool | None = None


def extract_acceptance_criteria(*texts: str) -> list[AcceptanceCriterion]:
    """Pull bullet/numbered lines out of one or more free-text sources.

    Returns an empty list when nothing looks like a checklist. That is not a
    failure: most objectives are a single sentence, and forcing this step to
    run against a sentence with no criteria would be a NOP dressed up as a
    check.
    """
    criteria: list[AcceptanceCriterion] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for line in text.splitlines():
            match = _BULLET_RE.match(line)
            if not match:
                continue
            item = match.group(1).strip()
            if item and item not in seen:
                seen.add(item)
                criteria.append(AcceptanceCriterion(text=item))
    return criteria


@dataclass(frozen=True)
class TidyClassification:
    """A tidy finding, classified against the run's declared file scope."""

    clutter: Clutter
    in_scope: bool

    @property
    def blocking(self) -> bool:
        """Only in-scope clutter blocks close; everything else is advisory."""
        return self.in_scope


def _normalise(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def classify_tidy_findings(
    findings: list[Clutter], file_scope: list[str], root: Path
) -> list[TidyClassification]:
    """Classify tidy findings by whether they fall inside the run's file scope.

    Reuses the same scope-matching semantics as
    ``enforce.detect_scope_violations``: an exact match, or a match under a
    scope entry that ends in ``*`` or ``/``. A finding is only "in scope" when
    the run actually declared a scope; an empty scope cannot make everything
    in the repository count as part of the deliverable.
    """
    if not file_scope:
        return [TidyClassification(item, in_scope=False) for item in findings]
    allowed = {_normalise(s) for s in file_scope}
    out: list[TidyClassification] = []
    for item in findings:
        try:
            rel = _normalise(str(item.path.relative_to(root)))
        except ValueError:
            rel = _normalise(str(item.path))
        in_scope = rel in allowed or any(
            rel == a.rstrip("*").rstrip("/") or rel.startswith(a.rstrip("*").rstrip("/") + "/")
            for a in allowed
            if a.endswith(("*", "/"))
        )
        out.append(TidyClassification(item, in_scope=in_scope))
    return out


@dataclass
class ReviewReport:
    """Everything ``gate review`` found, before the human verdict is recorded."""

    criteria: list[AcceptanceCriterion]
    toolchain_results: list[Evidence]
    scope_violations: list[str]
    test_weakening: list[str]
    tidy_findings: list[TidyClassification]
    changed_files: list[str]

    @property
    def blocking_tidy_findings(self) -> list[TidyClassification]:
        return [item for item in self.tidy_findings if item.blocking]

    @property
    def can_record(self) -> bool:
        """Whether review may proceed to recording a verdict.

        Test weakening and scope violations are refusals, not warnings: they
        are exactly what ``gate check`` already treats as blocking, and
        review must not be a softer path around the same finding. In-scope
        tidy clutter is also blocking, because it is part of what would ship.
        """
        return not (self.test_weakening or self.scope_violations or self.blocking_tidy_findings)


def diff_against(root: Path, diff_base: str) -> tuple[str, list[str]]:
    """Real ``git diff`` output and changed file names against ``diff_base``.

    Raises ``RuntimeError`` if git fails, so a failed diff is never silently
    read as "no changes" - the same distinction ``gate check`` already makes.
    """
    diff_result = subprocess.run(
        ["git", "diff", diff_base, "--"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(root),
        check=False,
    )
    names_result = subprocess.run(
        ["git", "diff", "--name-only", diff_base, "--"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(root),
        check=False,
    )
    if diff_result.returncode != 0 or names_result.returncode != 0:
        detail = (diff_result.stderr or names_result.stderr).strip()
        raise RuntimeError(f"could not diff against {diff_base!r}: {detail}")
    return diff_result.stdout, names_result.stdout.split()


def run_toolchain_gates(ledger: Ledger, run_id: str, root: Path) -> list[Evidence]:
    """Run every usable toolchain-detected gate and record it as real Evidence.

    Uses ``Ledger.record`` exactly as ``gate record`` does: the command is
    executed, its exit code is captured, and the result is appended to the
    run's evidence. Unusable gates (no detected test or lint command) are
    skipped rather than faked.
    """
    chain = Toolchain(root)
    results: list[Evidence] = []
    for name, gate in TOOLCHAIN_GATES:
        tool = chain.gate_command(name)
        if not tool.usable:
            continue
        results.append(ledger.record(run_id, gate, tool.command, cwd=root))
    return results


def review_run(
    ledger: Ledger,
    run: Run,
    root: Path,
    *,
    seed_description: str = "",
    diff_base: str | None = None,
    run_toolchain: bool = True,
) -> ReviewReport:
    """Run every independent check ``gate review`` performs, without recording
    a verdict. Recording the verdict is a separate, explicit step so a human
    (or an agent acting on a human's instruction) always states the verdict
    rather than one being inferred from the checks passing.
    """
    criteria = extract_acceptance_criteria(run.objective, seed_description)

    toolchain_results = run_toolchain_gates(ledger, run.run_id, root) if run_toolchain else []

    scope_violations: list[str] = []
    test_weakening: list[str] = []
    changed_files: list[str] = []
    if diff_base:
        diff, names = diff_against(root, diff_base)
        changed_files = names
        test_weakening = detect_test_weakening(diff)
        scope_violations = detect_scope_violations(names, run.file_scope)

    paths = _tidy_paths(root)
    tidier = Tidier(paths)
    raw_findings = [
        item
        for item in tidier.scan()
        if item.kind not in {Finding.EMPTY_DIR, Finding.ORPHANED_CACHE}
    ]
    tidy_findings = classify_tidy_findings(raw_findings, run.file_scope, root)

    return ReviewReport(
        criteria=criteria,
        toolchain_results=toolchain_results,
        scope_violations=scope_violations,
        test_weakening=test_weakening,
        tidy_findings=tidy_findings,
        changed_files=changed_files,
    )


def _tidy_paths(root: Path) -> SmithPaths:
    """A minimal ``SmithPaths``-shaped object rooted at the project, so tidy's
    scan runs against the project being reviewed, not against A.W.I.N.O.'s own
    installation."""
    return SmithPaths(root=root)


def build_provenance(report: ReviewReport) -> list[ProvenanceGateResult]:
    """Convert a review report's executed evidence into provenance gate tuples."""
    return [
        ProvenanceGateResult(gate=item.gate, command=item.command, exit_code=item.exit_code)
        for item in report.toolchain_results
    ]


def provenance_summary_for_seed(record: ProvenanceRecord) -> str:
    """A short summary of a ProvenanceRecord suitable for a Seed close reason."""
    return record.summary
