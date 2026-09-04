"""owns: gate open, gate plan approve, gate plan hold, gate plan reject, gate plan status, gate score, gate checkpoint, gate decide, gate record, gate record-completeness, gate check, gate review, gate close, gate block, gate pause, gate status, gate skill, gate contracts, debug begin, debug evidence, debug hypothesize, debug authorize-fix, debug attempt, debug verify

The run ledger surface: completion is earned, not asserted. Every command here
reads or writes the project's ledger through the shared helpers in ``smith.cli``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import typer

from smith import (
    completion_review,
    debugging,
    onboarding,
    playbook,
    seeds,
    session_state,
)
from smith.cli import (
    _debug_session,
    _echo,
    _ledger,
    _ledger_error,
    _require_valid_plan,
    _resolve_run,
    _skill_catalog,
    _validate_issue,
    _workspace,
    debug_app,
    gate_app,
    gate_plan_app,
)
from smith.enforce import (
    CONTRACTS,
    Gate,
    Ledger,
    LedgerError,
    PlanDecision,
    ReviewVerdict,
    TaskClass,
    adjudicate,
    detect_scope_violations,
    detect_test_weakening,
    score_run,
)


def _start_linked_issue(ledger: Ledger, run_id: str) -> None:
    run = ledger.load(run_id)
    if run.issue_id is None or run.issue_started_at is not None:
        return
    result = seeds.Seeds(_workspace().project.root).start(run.issue_id)
    if not result.ok:
        raise LedgerError(f"could not start issue {run.issue_id}: {result.detail}")
    run.issue_started_at = datetime.now(UTC).isoformat()
    ledger.save(run)


@debug_app.command("begin")
def debug_begin(
    symptom: str = typer.Argument(..., help="Observed bug, error, or failing test"),
    scope: list[str] = typer.Option(None, "--scope", help="Production path this fix may edit"),
    issue: str = typer.Option(None, "--issue", help="Open Seeds issue served by this debug run"),
    actor: str = typer.Option("agent", "--actor"),
) -> None:
    """Open a bugfix run in the reproduce phase."""
    ledger = _ledger()
    linked_issue = _validate_issue(issue) if issue else None
    run = ledger.open(
        TaskClass.BUGFIX,
        symptom,
        file_scope=scope or [],
        issue_id=linked_issue.id if linked_issue else None,
    )
    debugging.DebugSession.begin(ledger, run.run_id, symptom, actor)
    _echo(f"DEBUG_BEGIN  {run.run_id}  phase={debugging.DebugPhase.REPRODUCE}")


@debug_app.command("evidence")
def debug_evidence(
    kind: str = typer.Argument(...),
    detail: str = typer.Argument(...),
    actor: str = typer.Option("agent", "--actor"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    session.add_evidence(kind, detail, actor)
    _echo(f"DEBUG_EVIDENCE  phase={session.phase}")


@debug_app.command("hypothesize")
def debug_hypothesize(
    statement: str = typer.Argument(...),
    actor: str = typer.Option("agent", "--actor"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    try:
        session.add_hypothesis(statement, actor)
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"DEBUG_HYPOTHESIS  phase={session.phase}")


@debug_app.command("authorize-fix")
def debug_authorize_fix(
    by: str = typer.Option(..., "--by", help="Person or agent authorizing the evidence-backed fix"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    try:
        session.authorize_fix(by)
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"DEBUG_FIX_AUTHORIZED  phase={session.phase}")


@debug_app.command("attempt")
def debug_attempt(
    approach: str = typer.Argument(...),
    output: str = typer.Argument(...),
    succeeded: bool = typer.Option(False, "--succeeded/--failed"),
    actor: str = typer.Option("agent", "--actor"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    try:
        session.record_attempt(approach, output, succeeded=succeeded, actor=actor)
    except ValueError as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    _echo(f"DEBUG_ATTEMPT  assessment={session.assessment}")


@debug_app.command("verify")
def debug_verify(
    command: str = typer.Option(..., "--cmd", help="Regression command to execute and record"),
    actor: str = typer.Option("agent", "--actor"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    session = _debug_session(run_id)
    try:
        evidence = session.ledger.record(
            session.run_id, Gate.TESTED, command, _workspace().project.root
        )
        session.verify(
            command,
            evidence.output_head,
            succeeded=evidence.passed,
            actor=actor,
        )
    except (LedgerError, ValueError) as exc:
        _echo(f"REFUSED  {exc}")
        raise typer.Exit(1) from None
    verdict = "DEBUG_VERIFIED" if evidence.passed else "DEBUG_VERIFY_FAILED"
    _echo(f"{verdict}  phase={session.phase}  assessment={session.assessment}")
    _echo(evidence.output_head)
    if not evidence.passed:
        raise typer.Exit(1)


@gate_app.command("open")
def gate_open(
    task_class: TaskClass = typer.Argument(..., help="What kind of work this is"),
    objective: str = typer.Argument(..., help="One sentence describing the goal"),
    scope: list[str] = typer.Option(None, "--scope", help="File this run may write. Repeatable."),
    also: list[Gate] = typer.Option(
        None, "--also", help="Extra gate beyond the contract. Repeatable."
    ),
    plan: Path = typer.Option(None, "--plan", help="Plan file reviewed before execution."),
    issue: str = typer.Option(None, "--issue", help="Open Seeds issue served by this run."),
    by: str = typer.Option(
        "",
        "--by",
        help="Who/what is opening this run. Recorded so a later review can be checked for independence.",
    ),
) -> None:
    """Open a run and print the gates it must satisfy before it can close."""
    required = list(CONTRACTS[task_class]) + list(also or [])
    if Gate.PLANNED in required and plan is None:
        _echo("PLAN_REQUIRED  schema v2 runs containing the planned gate require --plan")
        raise typer.Exit(2)
    plan_path = None
    if plan is not None:
        plan_path = plan if plan.is_absolute() else _workspace().project.root / plan
        plan_path = plan_path.resolve()
        if not plan_path.is_file():
            _echo(f"PLAN_NOT_FOUND  {plan_path}")
            raise typer.Exit(2)
    intent = onboarding.load(_workspace().project.root)
    if intent and intent.source == "confirmed" and intent.workflow.issue_required:
        if not issue:
            _echo("ISSUE_REQUIRED  this project's workflow requires an issue ID")
            raise typer.Exit(2)
        if (
            intent.workflow.issue_pattern
            and re.fullmatch(intent.workflow.issue_pattern, issue) is None
        ):
            _echo(f"ISSUE_FORMAT  {issue!r} does not match {intent.workflow.issue_pattern!r}")
            raise typer.Exit(2)
    linked_issue = _validate_issue(issue) if issue else None
    ledger = _ledger()
    run = ledger.open(
        task_class,
        objective,
        file_scope=list(scope or []),
        extra_gates=list(also or []),
        plan_path=plan_path,
        issue_id=linked_issue.id if linked_issue else None,
        opened_by=by,
    )
    if intent and intent.bootstrap:
        ledger.append_artifact(
            run.run_id,
            "bootstrap",
            intent.bootstrap.confirmed_by or "unknown",
            asdict(intent.bootstrap),
        )
    if intent and intent.source == "confirmed":
        try:
            session_state.bind_run(
                _workspace().state_root,
                run.run_id,
                enforce_one_task=intent.workflow.one_task_per_session,
            )
        except RuntimeError as exc:
            _echo(f"NEW_SESSION_REQUIRED  {exc}")
            raise typer.Exit(2) from exc
    _echo(f"RUN {run.run_id}  class={run.task_class}")
    _echo(f"objective: {run.objective}")
    if run.file_scope:
        _echo(f"scope: {', '.join(run.file_scope)}")
    if run.plan_path:
        _echo(f"plan: {run.plan_path}")
    if run.issue_id:
        _echo(f"issue: {run.issue_id}")
    if not run.required:
        _echo("gates: none for this class")
        return
    _echo("gates required before close:")
    for gate in run.required:
        _echo(f"  [ ] {gate}")
    _echo("")
    _echo('Record each with: awino gate record <gate> --cmd "<command>"')


def _plan_decision(decision: PlanDecision, run_id: str | None, by: str, reason: str) -> None:
    resolved = _resolve_run(run_id)
    try:
        item = _ledger().decide_plan(resolved, decision, by, reason)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"PLAN_{decision.upper()}  {item.plan_path}")
    _echo(f"sha256: {item.plan_sha256}")
    _echo(f"scope: {', '.join(item.approved_scope) or '(none)'}")


@gate_plan_app.command("approve")
def gate_plan_approve(
    by: str = typer.Option(..., "--by", help="Person approving the plan."),
    reason: str = typer.Option("", "--reason"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Approve the exact current plan bytes and file scope."""
    _plan_decision(PlanDecision.APPROVED, run_id, by, reason)


@gate_plan_app.command("hold")
def gate_plan_hold(
    by: str = typer.Option(..., "--by"),
    reason: str = typer.Option("", "--reason"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Hold a plan until its concerns are resolved."""
    _plan_decision(PlanDecision.HELD, run_id, by, reason)


@gate_plan_app.command("reject")
def gate_plan_reject(
    by: str = typer.Option(..., "--by"),
    reason: str = typer.Option("", "--reason"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Reject the current plan."""
    _plan_decision(PlanDecision.REJECTED, run_id, by, reason)


@gate_plan_app.command("status")
def gate_plan_status(run_id: str = typer.Option(None, "--run")) -> None:
    """Show whether the current plan approval remains valid."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.load(resolved)
        problems = ledger.validate_plan(resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    latest = run.plan_decisions[-1] if run.plan_decisions else None
    _echo(f"PLAN  {run.plan_path or '(none)'}")
    _echo(f"decision: {latest.decision if latest else 'none'}")
    _echo("VALID" if not problems else f"INVALID  {'; '.join(problems)}")


@gate_app.command("score")
def gate_score(run_id: str = typer.Option(None, "--run")) -> None:
    """Print an advisory score computed only from recorded ledger evidence."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    run = ledger.load(resolved)
    evidence = ledger.evidence(resolved)
    verdict = adjudicate(run, evidence)
    score = score_run(run, evidence, verdict)
    _echo(f"SCORE  {score.total}  grade={score.grade}")
    if not score.items:
        _echo("  no scoreable evidence recorded")
        return
    for item in score.items:
        _echo(f"  {item.points:+4d}  {item.name:<25} {item.evidence}")
    _echo("NOTE   advisory only; gate close remains the completion authority")


@gate_app.command("checkpoint")
def gate_checkpoint(
    phase: str = typer.Option(..., "--phase"),
    summary: str = typer.Option(..., "--summary"),
    next_action: str = typer.Option(..., "--next"),
    pending: str = typer.Option(None, "--pending", help="Decision requiring human input."),
    option: list[str] = typer.Option(None, "--option", help="Allowed decision. Repeatable."),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Persist resumable continuation state for a run."""
    resolved = _resolve_run(run_id)
    try:
        item = _ledger().checkpoint(
            resolved,
            phase,
            summary,
            next_action,
            pending_decision=pending,
            options=list(option or []),
        )
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"CHECKPOINT  {item.checkpoint_id} phase={item.phase}")
    if item.pending_decision:
        _echo(f"pending: {item.pending_decision}")
        _echo(f"options: {', '.join(item.options)}")


@gate_app.command("decide")
def gate_decide(
    selection: str = typer.Argument(..., help="One declared checkpoint option."),
    by: str = typer.Option(..., "--by"),
    run_id: str = typer.Option(None, "--run"),
) -> None:
    """Resolve the current checkpoint decision."""
    resolved = _resolve_run(run_id)
    try:
        item = _ledger().resolve_checkpoint(resolved, selection, by)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"DECIDED  {item.checkpoint_id}  {selection}  by={by}")


@gate_app.command("record")
def gate_record(
    gate: Gate = typer.Argument(..., help="Which gate this evidence satisfies"),
    cmd: str = typer.Option(None, "--cmd", help="Command to execute and record"),
    attest: str = typer.Option(None, "--attest", help="Record a non-command gate with a note"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Execute a command and record its real exit code against a gate."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    if bool(cmd) == bool(attest):
        _echo("Provide exactly one of --cmd or --attest")
        raise typer.Exit(2)

    try:
        _require_valid_plan(ledger, resolved)
        if attest:
            item = ledger.attest(resolved, gate, attest)
        else:
            _start_linked_issue(ledger, resolved)
            item = ledger.record(resolved, gate, cmd, cwd=_workspace().project.root)
    except LedgerError as exc:
        _ledger_error(exc)

    if attest:
        _echo(f"ATTESTED  {gate}  {attest}")
        _echo("Attestations are weaker than executed evidence and are reported as such.")
        return

    verdict = "PASS" if item.passed else "FAIL"
    _echo(f"{verdict}  {gate}  exit={item.exit_code}  {item.duration_ms}ms  attempt={item.attempt}")
    _echo(f"command: {item.command}")
    tail = item.output_head.strip().splitlines()[-12:]
    for line in tail:
        _echo(f"  | {line}")
    if not item.passed:
        _echo("")
        _echo(
            f"Gate not satisfied. Fix the cause, then record again (attempt {item.attempt + 1} of 3)."
        )
        raise typer.Exit(1)


@gate_app.command("record-completeness")
def gate_record_completeness(
    achieved: int = typer.Option(..., "--achieved", help="Units actually produced"),
    stated: int = typer.Option(..., "--stated", help="Units the objective claims"),
    unit: str = typer.Option("unit(s)", "--unit", help="What is being counted"),
    accept_reduced_scope: bool = typer.Option(
        False, "--accept-reduced-scope", help="A human explicitly accepts achieved < stated"
    ),
    by: str = typer.Option(None, "--by", help="Who accepted the reduced scope"),
    reason: str = typer.Option(None, "--reason", help="Why the reduced scope is acceptable"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Record achieved-vs-stated counts. close() refuses if achieved < stated.

    A partial deliverable labeled complete is a failed task, not a partial
    success. This exists so that gap cannot be closed by report alone -
    only by a human explicitly recording acceptance of the reduced scope.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        record = ledger.record_completeness(
            resolved,
            achieved=achieved,
            stated=stated,
            unit=unit,
            accept_reduced_scope=accept_reduced_scope,
            accepted_by=by,
            accepted_reason=reason,
        )
    except LedgerError as exc:
        _ledger_error(exc)

    if record.satisfied:
        if record.accepted_reduced_scope:
            _echo(
                f"COMPLETENESS_ACCEPTED  {record.achieved}/{record.stated} {record.unit}  "
                f"reduced scope accepted by {record.accepted_by}: {record.accepted_reason}"
            )
        else:
            _echo(f"COMPLETENESS_MET  {record.achieved}/{record.stated} {record.unit}")
    else:
        _echo(f"DELIVERABLE_INCOMPLETE  {record.achieved}/{record.stated} {record.unit} achieved")
        _echo(
            "A partial deliverable is a failed task, not a partial success. "
            "gate close will refuse until either the remaining units are produced "
            "or a human records --accept-reduced-scope with --by and --reason."
        )
        raise typer.Exit(1)


@gate_app.command("check")
def gate_check(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
    diff_base: str = typer.Option(
        None, "--diff-base", help="Git ref to diff against for independent checks"
    ),
) -> None:
    """Run the independent checks that do not trust the agent's word."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.load(resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    root = _workspace().project.root
    problems = 0

    if diff_base:
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
            # git failed (no repo, bad ref, etc). An empty diff from a failed
            # command is not evidence of "no weakening" — reporting it as such
            # would silently pass a check that never actually ran.
            problems += 1
            _echo(
                "GIT_DIFF_FAILED  could not diff against "
                f"{diff_base!r}: {(diff_result.stderr or names_result.stderr).strip()}"
            )
            _echo(
                "  Not a git repo, or the ref does not exist. Use --attest instead of --diff-base."
            )
        else:
            diff = diff_result.stdout
            names = names_result.stdout.split()
            weakened = detect_test_weakening(diff)
            if weakened:
                problems += 1
                _echo(f"TESTS_WEAKENED  {len(weakened)} finding(s):")
                for finding in weakened:
                    _echo(f"  ! {finding}")
                _echo("  Fix the code, never the test.")
            else:
                ledger.attest(
                    resolved, Gate.TESTS_NOT_WEAKENED, f"diff vs {diff_base} shows no weakening"
                )
                _echo("TESTS_NOT_WEAKENED  ok")

            violations = detect_scope_violations(names, run.file_scope)
            if violations:
                problems += 1
                _echo(f"SCOPE_VIOLATION  {len(violations)} file(s) outside declared scope:")
                for path in violations:
                    _echo(f"  ! {path}")
            elif run.file_scope:
                ledger.attest(
                    resolved, Gate.SCOPE_RESPECTED, f"{len(names)} changed files all within scope"
                )
                _echo("SCOPE_RESPECTED  ok")
    else:
        _echo("SKIP  pass --diff-base to enable weakening and scope checks")

    if problems:
        raise typer.Exit(1)


@gate_app.command("review")
def gate_review(
    verdict: ReviewVerdict = typer.Option(
        ..., "--verdict", help="approved, changes-requested, or blocked"
    ),
    risks: str = typer.Option(None, "--risks", help="Remaining risk(s), free text. Optional."),
    diff_base: str = typer.Option(
        None, "--diff-base", help="Git ref to diff against for scope/weakening checks"
    ),
    skip_toolchain: bool = typer.Option(
        False, "--skip-toolchain", help="Skip running detected test/lint commands"
    ),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
    by: str = typer.Option(
        ...,
        "--by",
        help="Who/what is recording this review. Must differ from the run's --by; a run cannot be verified by the actor who opened it.",
    ),
) -> None:
    """Independent completion review, run before ``gate close``.

    Extracts acceptance criteria from the objective and linked Seed (if any),
    runs the project's own test/lint commands as real evidence, reuses the
    existing scope and test-weakening checks against a diff, and runs
    ``tidy --dry-run`` read-only. The result is recorded as a ProvenanceRecord;
    task classes that require the REVIEWED gate will not close without one.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    root = _workspace().project.root
    try:
        run = ledger.load(resolved)
        _require_valid_plan(ledger, resolved)
    except LedgerError as exc:
        _ledger_error(exc)

    seed_description = ""
    if run.issue_id:
        tracker = seeds.Seeds(root)
        state, _reason = tracker.state()
        if state.usable:
            issue = tracker.show(run.issue_id)
            if issue is not None:
                seed_description = issue.description

    try:
        report = completion_review.review_run(
            ledger,
            run,
            root,
            seed_description=seed_description,
            diff_base=diff_base,
            run_toolchain=not skip_toolchain,
        )
    except RuntimeError as exc:
        _echo(f"REFUSED  GIT_DIFF_FAILED  {exc}")
        raise typer.Exit(1) from exc

    if report.criteria:
        _echo(f"ACCEPTANCE CRITERIA  {len(report.criteria)} found in objective/Seed description")
        for item in report.criteria:
            _echo(f"  - {item.text}")
    else:
        _echo("ACCEPTANCE CRITERIA  none found, skipped")

    if report.toolchain_results:
        _echo("")
        _echo("TOOLCHAIN VERIFICATION")
        for item in report.toolchain_results:
            mark = "PASS" if item.passed else "FAIL"
            _echo(f"  {mark}  {item.gate:<10} exit={item.exit_code}  {item.command}")

    if diff_base:
        _echo("")
        if report.test_weakening:
            _echo(f"TESTS_WEAKENED  {len(report.test_weakening)} finding(s):")
            for finding in report.test_weakening:
                _echo(f"  ! {finding}")
        else:
            _echo("TESTS_NOT_WEAKENED  ok")
            ledger.attest(
                resolved,
                Gate.TESTS_NOT_WEAKENED,
                f"gate review: diff vs {diff_base} shows no weakening",
            )
        if report.scope_violations:
            _echo(
                f"SCOPE_VIOLATION  {len(report.scope_violations)} file(s) outside declared scope:"
            )
            for path in report.scope_violations:
                _echo(f"  ! {path}")
        elif run.file_scope:
            _echo("SCOPE_RESPECTED  ok")
            ledger.attest(
                resolved,
                Gate.SCOPE_RESPECTED,
                f"gate review: {len(report.changed_files)} changed files all within scope",
            )

    _echo("")
    if report.tidy_findings:
        _echo(f"TIDY FINDINGS  {len(report.tidy_findings)} (dry-run, nothing changed)")
        for item in report.tidy_findings:
            rel = (
                item.clutter.path.relative_to(root)
                if root in item.clutter.path.parents
                else item.clutter.path
            )
            level = "BLOCKING" if item.blocking else "WARN"
            _echo(f"  {level:<8} {item.clutter.kind:<18} {rel}  ({item.clutter.detail})")
    else:
        _echo("TIDY FINDINGS  none")

    if not report.can_record:
        _echo("")
        reasons = []
        if report.test_weakening:
            reasons.append("test weakening detected")
        if report.scope_violations:
            reasons.append("scope violation detected")
        if report.blocking_tidy_findings:
            reasons.append(f"{len(report.blocking_tidy_findings)} in-scope clutter finding(s)")
        _echo(f"REFUSED  REVIEW_BLOCKED  {'; '.join(reasons)}")
        raise typer.Exit(1)

    gate_results = completion_review.build_provenance(report)
    try:
        record = ledger.record_provenance(
            resolved,
            verdict=verdict,
            gate_results=gate_results,
            changed_files=report.changed_files,
            verified_by=by,
            risks=risks,
        )
    except LedgerError as exc:
        _ledger_error(exc)
    ledger.attest(resolved, Gate.REVIEWED, f"gate review verdict={verdict} by={by}")

    _echo("")
    _echo(f"REVIEWED  verdict={record.verdict}  by={record.verified_by}")
    _echo(f"  changed_files={len(record.changed_files)}  risks={record.risks or 'none'}")
    if verdict != ReviewVerdict.APPROVED:
        _echo(f"NOTE  verdict is {verdict}; gate close will still require every gate to pass.")


@gate_app.command("close")
def gate_close(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Attempt to close a run. Refuses unless every gate is satisfied by evidence."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.load(resolved)
        _require_valid_plan(ledger, resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    if Gate.REVIEWED in run.required and run.provenance is None:
        _echo("")
        _echo("REFUSED  REVIEW_REQUIRED: run 'gate review' first")
        raise typer.Exit(1)
    verdict = adjudicate(run, ledger.evidence(resolved))

    _echo(f"RUN {verdict.run_id}  class={verdict.task_class}")
    _echo(f"OBJECTIVE (stated at open): {run.objective}")
    for gate in run.required:
        if gate in verdict.satisfied:
            mark = "attested" if gate in verdict.attested_only else "executed"
            _echo(f"  [x] {gate}  ({mark})")
        elif gate in verdict.failing:
            _echo(f"  [!] {gate}  FAILING")
        else:
            _echo(f"  [ ] {gate}  no evidence")

    if run.completeness is not None:
        c = run.completeness
        mark = "x" if c.satisfied else "!"
        _echo(f"  [{mark}] completeness  {c.achieved}/{c.stated} {c.unit}")

    if not verdict.can_close:
        _echo("")
        _echo(f"REFUSED  {verdict.blocked_reason}")
        _echo("You may not report this work as complete.")
        raise typer.Exit(1)

    if run.issue_id:
        tracker = seeds.Seeds(_workspace().project.root)
        state, reason = tracker.state()
        if not state.usable:
            _echo("")
            _echo(f"REFUSED  SEED_UNAVAILABLE  linked issue {run.issue_id} but {reason}")
            _echo("Run 'awino work-close' first, or resolve the tracker, then retry close.")
            raise typer.Exit(1)
        issue = tracker.show(run.issue_id)
        if issue is None or issue.open:
            _echo("")
            status = issue.status if issue is not None else "not found"
            _echo(
                f"REFUSED  SEED_NOT_CLOSED  linked issue {run.issue_id} is still {status}. "
                "Gate evidence is satisfied, but a Seed left open is a lie that survives "
                "in git history."
            )
            _echo(f"Run 'awino work-close' first to close {run.issue_id}, then retry close.")
            raise typer.Exit(1)

    ledger.mark_complete(resolved)
    run = ledger.load(resolved)
    _echo("")
    _echo(f"COMPLETE  {len(verdict.satisfied)} gate(s) satisfied")
    _echo(
        f"Restate the human's exact ask and confirm the pasted evidence above answers it, "
        f'not an adjacent one: "{run.objective}"'
    )
    if verdict.attested_only:
        _echo(f"NOTE  attested rather than executed: {', '.join(verdict.attested_only)}")
    # Playbook: task-close order fires here, so the walkthrough and grill are
    # offered every time work lands rather than when someone remembers.
    try:
        workspace = _workspace()
        tracker = seeds.Seeds(workspace.project.root)
        open_titles = [i.title for i in tracker.list_open()] if tracker.state()[0].usable else []
        _echo("")
        for line in playbook.run_event(
            "task-close",
            workspace.state_root,
            workspace.project.root,
            ledger=ledger,
            open_seeds=open_titles,
        ):
            _echo(line)
    except Exception as exc:  # playbook is advisory; never turn a closed run into a failure
        _echo(f"NOTE  task-close playbook skipped: {exc}")


@gate_app.command("block")
def gate_block(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Mark a run BLOCKED. Requires a recorded failing gate and a checkpoint

    with an unresolved pending decision (record one first with
    'gate checkpoint --pending ... --option ...'). This does not create the
    decision point itself - it only certifies that both halves of a genuine
    block already exist, so a run cannot be waved to BLOCKED with only a
    failure or only a question.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.mark_blocked(resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"BLOCKED  {run.run_id}")
    pending = next(
        (
            item
            for item in reversed(run.checkpoints)
            if item.pending_decision is not None and item.selected_decision is None
        ),
        None,
    )
    if pending is not None:
        _echo(f"pending: {pending.pending_decision}")
        _echo(f"options: {', '.join(pending.options)}")


@gate_app.command("pause")
def gate_pause(
    by: str = typer.Option(..., "--by", help="The human pausing this run. Required."),
    reason: str = typer.Option(..., "--reason", help="Why this run is being paused."),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Explicitly pause a run. A human decision, never an automatic side effect.

    This is a distinct command on purpose: no other command may set PAUSED as
    a side effect, and this one refuses without a named human.
    """
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.pause(resolved, by, reason)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"PAUSED  {run.run_id}  by={by}")
    _echo(f"reason: {reason}")


@gate_app.command("status")
def gate_status(
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
) -> None:
    """Show gate progress for a run without attempting to close it."""
    resolved = _resolve_run(run_id)
    ledger = _ledger()
    try:
        run = ledger.load(resolved)
    except LedgerError as exc:
        _ledger_error(exc)
    evidence = ledger.evidence(resolved)
    verdict = adjudicate(run, evidence)
    _echo(f"RUN {run.run_id}  class={run.task_class}  objective: {run.objective}")
    _echo(f"terminal_state: {run.terminal_state or 'active'}")
    _echo(f"skills loaded: {', '.join(run.skills_loaded) or 'none recorded'}")
    _echo(
        f"satisfied={len(verdict.satisfied)} missing={len(verdict.missing)} failing={len(verdict.failing)}"
    )
    for item in evidence:
        flag = "ok " if item.passed else "FAIL"
        _echo(
            f"  {flag} {item.gate:<20} attempt={item.attempt} exit={item.exit_code}  {item.command[:60]}"
        )
    if not verdict.can_close:
        _echo(f"BLOCKED  {verdict.blocked_reason}")


@gate_app.command("skill")
def gate_skill(
    name: str = typer.Argument(..., help="Canonical skill name or legacy alias"),
    run_id: str = typer.Option(None, "--run", help="Run id, defaults to current"),
    state: str = typer.Option("loaded", "--state", help="Truthful state: loaded or used"),
    reason: str = typer.Option("", "--reason", help="Why the skill was loaded or used"),
) -> None:
    """Validate and persist truthful skill loading or usage evidence."""
    if state not in {"loaded", "used"}:
        _echo("REFUSED  --state must be loaded or used")
        raise typer.Exit(2)
    resolution = _skill_catalog().resolve(name)
    if resolution is None:
        _echo(f"REFUSED  unknown skill {name!r}; inspect canonical names with: awino skills")
        raise typer.Exit(2)
    if resolution.deprecated_alias:
        typer.echo(
            f"DEPRECATED_SKILL_ALIAS  {name} -> {resolution.skill.name}",
            err=True,
        )
    resolved = _resolve_run(run_id)
    try:
        run = _ledger().note_skill(resolved, resolution.skill.name, state=state, reason=reason)
    except LedgerError as exc:
        _ledger_error(exc)
    _echo(f"SKILL_{state.upper()}  {resolution.skill.name}  (total {len(run.skills_loaded)})")


@gate_app.command("contracts")
def gate_contracts() -> None:
    """Show which gates each task class requires."""
    for task_class, gates in CONTRACTS.items():
        _echo(f"{task_class}:")
        for gate in gates or ():
            _echo(f"  - {gate}")
        if not gates:
            _echo("  (none)")
