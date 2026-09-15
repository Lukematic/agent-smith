# A.W.I.N.O. recovery specification and execution workplan

Date: 2026-09-15

## Goal and mission

The user starts a supported session or calls `awino best`, gives intent, edits a prefilled plan, and approves consequential decisions; A.W.I.N.O. handles routing, bounded execution, checks, documentation, and restart without requiring the user to remember individual workflow commands.

Success means actual, traceable behavior, not matching headers or keywords. Each claim needs a command, an observable result, and a state or artifact that proves it.

## Prior work already read and usable

- `specs/one-operator-spec.md` and `specs/partner-spec.md`: define the one-operator mission and partner posture.
- `docs/PHILOSOPHY.md`: constraints about scope, uncertainty, and not pretending.
- `thoughts/research/2026-09-15-awino-recovery-brief.md`: the failure taxonomy and the evidence behind this plan; read it before implementing.

## Decisions already made

- Proceed with the five-phase recovery scope.
- Keep `.awino` and retire `.smith` only after verified cutover.
- Support Claude Code, Kilo, and Roo, each with independent integration evidence.
- Use one durable, plan-bound controller.
- Preserve human planning edits and approval provenance.
- Retain the existing controller during recovery; do not add LangGraph in parallel.
- No destructive directory merge.
- No wholesale rewrite.
- No silent host-mode changes.
- No release during specification authoring.
- Publishing, commits, pushes, tags, and raising approved ceilings remain separately authorized.
- Total estimate: 10–17 focused engineering days, excluding access and review delays.
- Three failures of one gate must stop the phase; do not weaken assertions or raise ceilings.
- Missing live-host evidence means assisted/unverified status, not automatic parity.

## Stop and troubleshooting

Stop the current work when any of these occur: a required human decision, revoked or stale approval, missing permission, exhausted budget, failed verification without permitted attempts remaining, unavailable upstream source, or evidence that continuing would diverge from the goal. Report what is known, what is missing, and what would unblock progress.

Common failures:
- Tests fail repeatedly: inspect the failing behavior, reproduce it minimally, fix the code or the test's wrong assumption, and rerun. Never weaken assertions or raise ceilings to pass.
- Conflicting local changes: preserve both, record the conflict, and ask which should win.
- Missing credentials or host access: record the blocker and mark the affected work as unverified; do not substitute a simulation.
- State conflict on cutover or restart: refuse the operation, preserve existing data, and report the exact conflict.

## Autonomy and verification rules

- Execute only the approved phase with its current approval hash, file scope, verification command and budget.
- Any change to scope, budget, verifier, or phase requires a new approval.
- Verification needs both an exit code and an output or state assertion; a passing exit code alone is not proof.
- Negative cases need evidence of refusal or rejection, not absence of output.
- A budget that is exhausted is never a success; report the shortfall.
- Keep a visible work log of what was tried, what changed, and what remains.

## Definition of done per phase

Each phase is one independently reviewed change set with its own gate run. A phase is done when its regression command passes on the approved file scope, every listed pass criterion is demonstrated with a command and its observable output, and the work log records any deviations. Do not claim completion from partial evidence.

## Phase 1 — Startup and source/state identity

Current behavior that must change: identity confusion, missing or conflicting metadata, and destructive or silent cutover behavior.

Work:
- Capture baseline identities, local diffs, state inventory, and file hashes; preserve unique edits and missing-manifest differences.
- Add installation-aware metadata classification shared by `src/awino/health.py` and `tidy.py`; preserve `.in_use`, do not ignore arbitrary hidden files.
- Unify target/state resolution in `paths.py`, launcher scripts, `cli/project.py` and reporting; display actual usable tools rather than dictionary category names.
- Implement and test journaled `.smith` → retained `.awino` cutover and state conflict handling; update affected launcher/integration pointers last.
- Archive redundant `.smith` only after actual launcher, restart, and rollback checks; never discard unknown local changes.

Regression command:
`uv run --frozen pytest tests/test_health.py tests/test_tidy.py tests/test_best_cli.py tests/test_battery.py tests/test_claude_plugin.py tests/test_isolation.py tests/test_rename.py tests/test_recovery_cutover.py -o addopts= -q`

Pass criteria:
- Cache startup works with metadata intact; source strays still fail.
- Explicit target selection agrees across commands.
- Migrated data hashes match; interrupted migration resumes; conflicting state refuses; rollback restores the previous working invocation.

Human-observable: one startup clearly names source, target and pending work without irrelevant audit output.

Estimated: 1–2 days. Stop if preservation and rollback cannot be demonstrated.

## Phase 2 — One plan-bound durable controller

Current behavior that must change: fragmented routing, amnesia on restart, and status that reflects conversation rather than stored state.

Work:
- Share preflight, approval, review and closure services across loop and machine controllers; keep distinct entry points as explicit adapters.
- Persist per-plan state: plan revision, explicit scope, approval state, budgets, pending action IDs, verifier, pending approvals.
- Add normalized, idempotent controller events recording plan revision, scope, budget and action state.
- Persist knowledge receipts and have all status derive from stored facts, not memory; unify best/battery/claude/exam flows through this service.
- Retain current CLI/API surfaces via explicit adapter functions; record an event before mutating state.

Regression command:
`uv run --frozen pytest tests/test_loops.py tests/test_stepper.py tests/test_machine.py tests/test_session_contract.py tests/test_controller_closure.py tests/test_concurrency.py -o addopts= -q`

Pass criteria:
- Restart resumes from stored pending state; a second identical event submission applies once.
- Status reflects stored state not conversational memory; duplicate or interrupted runs never double-apply and conflicting concurrent edits refuse or converge to a single canonical result.
- Knowledge answers require a recorded receipt.

Human-observable: interrupting a session then restarting shows same pending work, same approval state and budget; every consequential action asks first.

Estimated: 3–4 days. Stop if duplicate-apply, double-charge, or silent divergence cannot be ruled out.

## Phase 3 — Shared planning and assignment contract

Current behavior that must change: spawn-time context drift, lost human edits, and approval that does not bind to what was approved.

Work:
- Add `src/awino/task_contract.py` defining the contract dataclass, with normalization, versioning, validation, approval-binding, staleness detection.
- Add `templates/task-contract.yaml` documenting one template with required vs optional fields, valid states and transitions.
- Add `tests/test_task_contract.py` covering normalization, invalid inputs, stale detection and approval binding.
- Carry the same approved contract revision through existing spawn/dispatch paths so subagents and CLI commands read one source; reject stale revisions when a plan has moved on.
- Add one planning brief type supporting prefilled editable drafts and returning edits with approval state.
- Preserve human edits and approval provenance; changes to scope/action/budget invalidate approval but preserve the plan revision history.

Regression command:
`uv run --frozen pytest tests/test_spawn.py tests/test_claude.py tests/test_claude_plugin.py tests/test_task_contract.py -o addopts= -q`

Pass criteria:
- A plan edited by the user keeps their wording and records who changed what.
- Approval records invalidate only for material scope changes.
- Every spawned worker references the current contract revision and the same prefilled brief type.

Human-observable: editing the prefilled plan then approving flows through without re-entry; changing scope visibly resets approval rather than silently continuing.

Estimated: 2–3 days. Stop if approval binding or stale-revision rejection cannot be demonstrated.

## Phase 4 — Real per-turn host activation

Current behavior that must change: host integrations that are declared but unproven, hook recursion risk, and parity claims without live evidence.

Work:
- Implement and prove separate adapters for Claude Code, Kilo, and Roo; each host keeps its own integration evidence.
- Trigger the same controller at session start, user turn, and tool-result boundaries.
- Prevent recursive hook execution.
- Run a real coding journey on each host: session start, intent, plan edit, approval, execution, restart.
- Missing host APIs block automatic-parity claims; record assisted mode where only partial activation is possible.

Regression command:
`uv run --frozen pytest tests/test_claude.py tests/test_claude_plugin.py tests/test_kilo.py tests/test_roo.py tests/test_hook_recursion.py tests/test_per_turn_activation.py -o addopts= -q`

Pass criteria:
- Each host adapter activates the same controller on a real session start and user turn without recursion.
- A deliberately unsupported host or missing API yields a clear assisted/unverified report, never a silent pass.
- Real journey evidence exists per host: recorded events, approvals, and restart recovery.

Human-observable: starting a session on any supported host shows the controller's pending work and asks for the consequential decisions; nothing runs unattended.

Estimated: 4–6 days including access and review time. Stop if live-host evidence cannot be produced; do not claim parity from test doubles.

## Phase 5 — Artifact behavior and release gate

Current behavior that must change: exams that pass on prose, artifacts that are untestable in isolation, and release gates that depend on the author's machine.

Work:
- Repair `exam.py` so failing subprocesses cannot pass through expected text alone; passing requires executable commands and verified outcomes.
- Build one capability manifest describing what the artifact provides and requires.
- Test clean source, wheel-only, and staged-plugin artifacts; prove a deliberately broken artifact is rejected.
- Keep publishing, commits, pushes, tags and releases behind separate authorization.

Regression command:
`uv run --frozen pytest tests/test_exam.py tests/test_manifest.py tests/test_artifact_matrix.py -o addopts= -q`

Pass criteria:
- A subprocess that fails cannot produce a passing exam even when its output contains expected text.
- Clean source, wheel-only, and staged-plugin artifacts each verify; a deliberately broken artifact is rejected with a specific reason.
- The capability manifest accurately describes the artifact under test.

Human-observable: running the release gate on a broken artifact says exactly what is broken and refuses; on a good artifact it lists verified capabilities.

Estimated: 2–3 days. Stop if a broken artifact can pass or a good artifact cannot be verified.

## Final layout and migration

- `.awino/` is the retained runtime: binaries, plugins, state, logs, metadata.
- `.smith/` is archived read-only after cutover is proven, then removed only with explicit approval.
- No file is deleted to make health green; `.in_use` and other host-owned markers are preserved.
- Launcher and integration pointers switch only after cutover verification passes.

## Appendix: reproduction brief (failures the spec is built on)

Windows 11, checkout C:\dev\.awino at commit 3d31b74, reported version 0.8.0:
- Two `best` failures on `.in_use` classification in the cached 0.8 test run.
- `health` misclassifies `.in_use` as stray/undocumented; `tidy` ignores unknown hidden files.
- Blocking `health` returns `healthy` in `_locate`.
- `exam` accepts expected text from a failing subprocess.
- Stance heuristics accept empty/marker-only answers.
- Knowledge accounting resets with a fresh store.
- Host plugin points at stale state on one machine.
- Approval flow asks at the wrong time.

## Appendix: context already gathered

- Baselines: `.smith` HEAD 08af22b and `.awino` HEAD 3d31b74, both declaring 0.8.0.
- `machine.py` and `stepper.py` exist; `ladder.py`, the portable reviewer flow, graph, `awino step`, and the `awino best` keeps-walking behavior also exist.
- The work is repair and verification, not re-derivation.

Current deliverable is this document, not immediate implementation. Each approved phase carries an approval hash, file scope, verifier, and budget.
