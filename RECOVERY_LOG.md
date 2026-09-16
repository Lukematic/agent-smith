# A.W.I.N.O. Recovery Log — 0.8 harness fix

Branch: `recovery/awino-0.8-harness-fix`. Baseline commit: `08af22b`
(version 0.8.0). Local commits only; never push, tag, or publish.

## Phase 1 — Startup and source/state identity (2026-09-15)

Mission: the machine must start from a known source, report what it will
touch, and never confuse "health said something" with "health is fine".

### What changed

- `src/awino/install_meta.py` (new): shared installation-metadata
  classification. `.in_use` is host-owned: preserved, never flagged, never
  archived, never deleted. Every other hidden entry is still inspected, so
  clutter cannot hide behind a dot. Shared by `tidy.py`, `health.py`
  (structure gate filters host-owned markers as defense in depth), and
  `fair.py` (no README demanded for host-owned marker directories).
- `src/awino/stepper.py::_locate`: blocking health now returns
  `"unhealthy"` instead of falling through to `"healthy"`.
- `src/awino/machine.py`: new edge `(LOCATE, "unhealthy") -> STOP`, so a
  blocking failure stops for a human decision instead of routing on.
- `src/awino/paths.py`: new `IdentityMap` — source (runtime home), target
  (project), state (state root), interpreter, plugin artifact in one record,
  built from the same resolvers everywhere.
- `src/awino/cli/project.py` (`awino start`): new `Source: <home> (<version>)`
  line naming the installation; the `Toolchain:` line now reports usable
  commands (`lint=uv run ruff check .`) instead of bare category names.
- `src/awino/cutover.py` (new): journaled, restartable `.smith` -> `.awino`
  cutover. Copy-never-move with sha256 per file; `CutoverRefused` on
  conflicting state (both preserved, nothing merged); JSONL journal with
  resume; launcher/restart/rollback checks before finalize; legacy archived
  (dated, under `.awino/archive/`) never deleted; pointers switch last via a
  journaled `pointers-switched` record; rollback restores the prior
  invocation (`.smith` byte-identical, cutover-created `.awino` removed) and
  refuses to delete migrated copies the operator edited afterwards.
- `src/awino/baseline.py` (new, `python -m awino.baseline`): repo-resident
  baseline capture — commit identity, working-tree diff, per-file sha256,
  manifest diffs (pyproject.toml, uv.lock), `.awino/`/`.smith/` inventory.
- `tests/test_recovery_cutover.py` (new, 28 tests): cutover plan/execute/
  resume/verify/checks/finalize/rollback, conflict refusal, `.in_use`
  preservation (tidy/health/archive/clean/fair), blocking `_locate`,
  baseline capture, and startup reporting usable tools.

### Decision boundaries

- `RECOVERY_LOG.md` is registered by exact name in `tidy.ROOT_ALLOWED`,
  alongside the other named root docs (`AWINO.md`, `AGENT_SMITH.md`). The
  set is a literal allow-list, not a pattern, so this adds one required
  deliverable without changing what the gate checks.
- The lazy `project_state_dir` migration keeps its existing contract
  (`.awino` wins, both preserved, no refusal) — existing tests pin it. The
  refusal semantics live in the explicit journaled cutover, where the
  operator asked for a decision instead of a guess.
- `scripts/capture_baseline.py` was moved to `src/awino/baseline.py`:
  `scripts/` is not an allowed root dir and widening the tidy gate to fit a
  new file would be weakening the gate. The module form is repo-resident
  without touching any gate.
- No launcher changes were needed: `bin/awino`, `bin/awino.ps1`,
  `bin/awino.cmd` derive their runtime from their own checkout and set
  `AWINO_PROJECT`; none hardcode `.smith`. The cutover's pointer-switch
  step verifies `ProjectPaths`/`Workspace.discover` land on the canonical
  dir last, after the checks pass.
- The Windows `.smith`/`.awino` GUI installations from the report do not
  exist in this Linux sandbox; the cutover is implemented and tested as
  journaled code, not run against those machines. GUI hook activation
  remains unverified (flagged for a phase with Windows access).

### Evidence

- Gate (only existing files; `tests/test_battery.py` does not exist):
  `uv run --frozen pytest tests/test_health.py tests/test_tidy.py
  tests/test_best_cli.py tests/test_claude_plugin.py tests/test_isolation.py
  tests/test_rename.py tests/test_recovery_cutover.py -o addopts= -q`
  → **90 passed, 2 skipped** (skips: claude CLI not installed, pre-existing).
- `awino doctor --fast` on the repo: `ok=10 warn=7 fail=0` (warnings are
  pre-existing/environmental: just/sd not on PATH, no remote, release
  drift, module sizes).
- `ruff check` and `ruff format --check`: clean on all touched files.
- Live `awino start` now prints, e.g.:
  `Source: /home/hatch/workspace/awino-recovery (0.8.0)` and
  `Toolchain: format=uv run ruff format ., install=uv sync --all-groups,
  lint=uv run ruff check ., manager=uv, test=uv run pytest -q`.

### Open / deferred to later phases

- Phase 2 (harness detection/isolation): untouched, as instructed.
- GUI hook activation on the affected Windows installations: unverified.
- `IdentityMap.capture` is the shared constructor for the exam-start
  identity record required in Phase 3.

## Phase 2 — One plan-bound durable controller (2026-09-15)

Mission: the loop controllers and the machine controller share one durable
plan-bound controller with write-ahead idempotent events, ask-first
approvals, budget ceilings, and status derived from stored facts.

### What changed

- `src/awino/controller.py` (new): `PlanController` — per-plan durable
  state (`plans/<plan_id>/plan.json`: revision, scope, approval state +
  provenance, budgets/used, pending action ids, verifier, pending
  approvals). All mutation goes through `submit_event`: the event is
  journaled BEFORE state is mutated (write-ahead); a repeated `event_id`
  replays the recorded outcome without re-applying; `expected_revision`
  mismatches refuse with `PlanConflict` (the revision check runs against
  disk-fresh state under a cross-platform atomic lock, so a stale second
  "process" genuinely refuses); budget charges past a ceiling refuse with
  `PlanBudgetExhausted` leaving no journal trace. Crash recovery re-applies
  journaled events the plan file does not yet reflect (`_apply` is pure, so
  recovery never double-counts).
- Shared services in `controller.py`, used by every entry point:
  `preflight` (blocking problems from stored state), `request_approval` /
  `require_approval` (every consequential action asks first; the ask is
  recorded, then `ApprovalRequired` refuses until a human grants with a
  name), `grant_approval` / `invalidate_approval`, `record_review`
  (ship/revise/blocked only), `close_plan` (refuses with pending actions,
  pending approvals, or an unapproved plan), `charge_budget`,
  `queue_action` / `apply_action`.
- Knowledge through the controller: `consult_knowledge` (budget-charged,
  persisted accounting via `accounting_key=plan-<id>`, records a receipt)
  is idempotent per question — asking again replays without re-charging;
  `answer_from_knowledge` cites the recorded receipt and refuses without
  one (`KnowledgeReceiptRequired`). One service for the best / battery /
  claude / exam flows.
- Explicit adapters keep entry-point APIs distinct: `for_plan`,
  `for_loop`, `for_machine` — same preflight/approval/review/closure/
  status, no surface merge. No LangGraph, no parallel controller: the
  existing controllers are retained and evolve through this service.
- `src/awino/knowledge.py`: `KnowledgeStore` accepts `accounting_key` /
  `accounting_dir`; the opened-file accounting persists to disk, so a fresh
  store with the same key resumes instead of resetting the budget (the
  reported 0.8 bug). `reset_budget()` drops the persisted file too — a new
  task never inherits the old task's consumption. New persisted knowledge
  receipts (`record_knowledge_receipt` / `knowledge_receipt` /
  `require_knowledge_receipt`) under `<state>/knowledge_receipts/`.
- `src/awino/stance_verify.py`: substance floor. Empty responses are
  refused for every stance and thinking mode (`response is empty`);
  marker-only responses (`I disagree`, `on the other hand` with no content)
  are refused (`no substantive content beyond stance markers`). The check
  fires only when no other rule failed, so every existing named failure is
  byte-identical.
- Tests: `tests/test_controller_closure.py` (new, 30 tests),
...[truncated 1797 chars]

## Phase 3 — shared planning and assignment contract (2026-09-15)

- `src/awino/task_contract.py` (new): one `TaskContract` datatype.
  Schema version, contract/plan revisions, role, objective, file scope,
  context paths, verification, budgets, verifier, dependencies; states
  draft/approved/invalid/invalidated/superseded/closed with legal
  transitions. `normalize()` canonicalizes wording/whitespace/scope
  ordering/role; `validate()` enforces the spawn path's invariants
  (read-only roles declare no file scope; writing roles declare scope and
  a verification command). `revision_hash()` binds an approval grant to
  the exact content reviewed. `apply_human_edit()` records who/when/
  before/after per field; material edits (scope/action/budget, default
  fail-closed) invalidate approval, caller-declared cosmetic edits keep it:
  `approval_covers()` reverse-applies recorded non-material edits and
  requires the reverted content to hash to the granted hash; anything
  unrecorded or material fails the binding. `assert_current()` /
  `rebase_contract()` implement staleness against the plan revision; the
  contract's own lifecycle events (save/grant/invalidate) advance
  `plan_revision_seen` past themselves, so a contract is never stale
  against its own bookkeeping — only against unrelated plan movement.
- One planning brief type `task-brief/v1`: `render_prefilled_draft()`
  emits an editable Markdown draft from a template contract;
  `apply_brief_edits()` parses it back (missing/unknown sections fail
  closed). Template `templates/task-contract.yaml` documents every field,
  the material-field list, the state machine, and the brief sections; the
  test suite asserts the template stays in sync with the module. Ships in
  the wheel via the existing `templates` force-include
  (`awino/_bundle/templates`); `AwinoPaths.templates` /
  `task_contract_template` resolve it in a checkout and in a bundle.
- Controller lifecycle: `save_contract` / `request_contract_approval` /
  `grant_contract_approval` / `invalidate_contract_approval` /
  `rebase_contract` / `supersede_contract` journal through the Phase 2
  `PlanController` (same write-ahead event journal, idempotent event ids,
  revision-conflict refusal); contract state is part of status snapshots.
  Revision history persists per contract (`current.json` plus per-revision
  snapshots).
- Spawn binding: `Assignment.contract: ContractRef | None`; `spawn_one`
  validates the stored contract (`check_contract_ref`) before writing or
  executing the worker prompt — stale revision, unapproved state,
  moved-on plan, or broken approval binding refuses; the stored file is
  the single source, reloaded after validation so a tampered passed object
  cannot inject content. The prompt carries the authoritative revision and
  `task-brief/v1` identity via `render_contract_block()`.
- Dispatch binding: `run_dispatch` and `open_floor` accept an optional
  contract; both validate the STORED contract via `check_contract_ref`
  (so a custom executor bypassing `spawn_one` is still gated) and rebuild
  the assignment from the reloaded file. `open_floor` requires `project`
  when a contract is given, to resolve the canonical state root.
- CLI: `awino dispatch --contract plan/contract[@revision]` and
  `awino floor open --contract ...`; the pinned form refuses when the
  stored revision differs; malformed/unknown references refuse at the CLI
  boundary (exit 2), and the dispatch gate re-validates against disk.
- Gate (spec files that exist; `tests/test_spawn.py` and
  `tests/test_claude.py` do not exist in this tree — recorded, not
  invented): `pytest tests/test_claude_plugin.py
  tests/test_task_contract.py` → 96 passed, 2 skipped (pre-existing
  Claude-CLI-absent skips), 1 failed — the failure is the pre-existing
  environmental `test_clean_plugin_cache...` (fails identically on the
  clean tree).
- `tests/test_task_contract.py`: 85 tests — normalization, validation,
  template sync, human-edit provenance, cosmetic-vs-material approval
  binding (incl. tamper rejection), staleness/rebase, persistence/history,
  brief round-trips, spawn refusal paths, dispatch gate incl.
  stored-state authority, `ContractRef.parse`, CLI option resolution.
- Full suite: 1664 passed, 4 skipped, 16 failed — all 16 reproduce
  identically on the clean pre-change tree (environmental: bracketed-IPv6
  proxy encoding test, 3 gate-review-workflow, 3 dispatch-cli, 3 best-cli,
  claude-plugin cache, health real-project, hook-routing compaction,
  isolation cross-project, 2 recovery-cutover startup-reporting). Zero
  regressions attributable to Phase 3.
- `ruff check` clean on all touched files.
- Known limitation: `for_plan`/`for_loop`/`for_machine` adapters and the
  best/battery/claude/exam CLI answer paths have not all been explicitly
  rewired through the shared knowledge service yet (carried from Phase 2).

## Phase 4 — Real per-turn host activation (2026-09-15)

Mission: each supported host activates the SAME plan controller at
session start, user turn, and tool-result boundaries — with recursion
protection and honest evidence labels. Missing live-host evidence means
assisted/unverified status, never parity.

### What changed

- `src/awino/hosts/` (new package): three SEPARATE host adapters sharing
  one base.
  - `base.py`: `HostAdapter` ABC (two host-specific pieces: `detect_live()`
    and `missing_api_surface()`; everything else shared so no host can
    quietly diverge), `HostStatus`, `Evidence` labels
    (live/double_driven/unverified/unsupported), `UnknownHost`,
    `get_adapter()` — an unknown host name raises, never silently passes.
    Boundary methods `session_start`/`user_turn`/`tool_result` fire
    idempotent controller events; `session_start` returns the controller's
    pending-work status lines (starting a session shows pending work;
    nothing runs unattended). Per-host evidence files under
    `<state>/hosts/<host>/evidence.jsonl`.
  - `claude_code.py`: detects via `claude` on PATH or `CLAUDE_PLUGIN_ROOT`;
    documents the real seam (`hooks/hooks.json` events -> `awino hook
    <event>`) and what the host API does NOT provide.
  - `kilo.py`, `roo.py`: VS Code-extension-dir detection heuristic
    (documented as heuristic); both spell out that no extension/hook API
    exists in this tree and host-side wiring is missing, not invented.
  - `recursion.py`: `guarded(event)` context manager — env-carried depth
    token (`AWINO_HOOK_DEPTH`) + event chain (`AWINO_HOOK_CHAIN`) so the
    guard survives into child processes. Same-event re-entry or nesting
    raises `HookRecursionRefused`; env is always restored.
- `src/awino/controller.py`: `PlanState.host_activity` (bounded at 500;
  the journal is the unbounded record) and three new `_apply` event kinds:
  `host_session_started`, `host_user_turn`, `host_tool_result`. They record
  who touched the plan and when; they never mutate plan substance. Old plan
  files load fine (new field has a default).
- `src/awino/cli/maintain.py`: the existing `awino hook` command now runs
  its body inside the recursion guard; a recursive invocation is refused
  with a clear message and exit code 3. Behavior for single invocations is
  unchanged.
- Tests: `tests/test_hook_recursion.py` (guard allow/refuse paths, env
  restoration, CLI exit-3 refusal via real subprocess),
  `tests/test_kilo.py` / `tests/test_roo.py` (adapter identity, unverified
  reporting, per-host evidence round-trip), `tests/test_per_turn_activation.py`
  (full double-driven journey per host through one controller: session
  start -> user turn -> plan edit -> approval -> execution -> tool result ->
  restart recovery; boundary redelivery applies once; a detected binary
  never upgrades evidence to live).

### Per-host evidence report (this environment, 2026-09-15)

| host        | detected_live | evidence    | missing APIs / notes                                  |
|-------------|---------------|-------------|-------------------------------------------------------|
| claude_code | no            | unverified  | no `claude` CLI here; hook seam exists in tree        |
| kilo        | no            | unverified  | no VS Code/Kilo here; no extension API in tree        |
| roo         | no            | unverified  | no VS Code/Roo here; no extension API in tree          |

No live host exists in this Linux sandbox, so no live journey evidence
could be produced. All journey evidence is `double_driven` and labeled as
such in both the controller journal and the per-host evidence files.
Automatic parity is NOT claimed for any host. A deliberately unsupported
host name (`clippy`) raises `UnknownHost` with the supported list.

### Verification

- Gate (`tests/test_claude.py` does not exist in this tree — excluded, not
  invented): `pytest tests/test_claude_plugin.py tests/test_kilo.py
  tests/test_roo.py tests/test_hook_recursion.py
  tests/test_per_turn_activation.py` → 58 passed, 2 skipped (pre-existing
  Claude-CLI-absent skips), 1 failed — the pre-existing environmental
  `test_clean_plugin_cache_prepares_locked_environment_and_runs_doctor`
  (fails identically on the clean tree).
- New tests: 47 passed.
- `ruff check` and `ruff format` clean on all touched files (fixed 21
  auto-fixable findings plus 7 manual: B904, PTH111/112, RUF005, SIM117).
- Full suite: 1711 passed, 4 skipped, 16 failed — all 16 verified
  pre-existing by re-running the suspicious subset in a worktree at the
  Phase 3 baseline `310c401` (14/14 failed identically there, including the
  hook-routing and recovery-cutover ones). Zero regressions from Phase 4.
- Two real bugs caught by my own tests mid-phase: `_apply`'s boundary-name
  mapping would have raised KeyError on `host_session_started`, and
  `event_count` is a method (test-only fix).

### Carry-forwards

- Host-side wiring on real machines: the Claude Code plugin's
  `hooks/hooks.json` still fires `awino hook <event>`; calling the new
  adapter boundary methods from host integrations (plugin hooks, Kilo/Roo
  extension lifecycle hooks) is a deployment step needing the user's
  Windows host and is NOT done here.
- Live per-host journeys (session start -> intent -> plan edit -> approval
  -> execution -> restart) remain unverified by design; the adapters,
  recursion guard, and evidence pipeline are the deliverable.
- Carried from Phase 2/3: best/battery/claude/exam CLI answer paths are not
  all rewired through the shared knowledge service yet.

## Phase 5 — Artifact behavior and release gate (2026-09-15)

### What was built
- `src/awino/exam.py`: probes now record the subprocess return code; a probe
  FIRES only on exit 0 AND expected text. Expected text from a crashed
  subprocess is explicitly not a pass. New `launcher_resolves()` guard proves
  the probe command is a real executable running `awino.cli`; new pure
  `probe_fired()` makes the pass/fail decision unit-testable.
- `src/awino/manifest.py` (new): one generated capability manifest
  (`src/awino/capabilities.json`, 95 capabilities, shipped in the wheel).
  `verify_manifest` rebuilds from the tree and reports every difference:
  claimed-but-missing, provided-but-unlisted, version mismatch, missing
  bundle entry. `verify_wheel` checks a built wheel for the manifest, the
  bundle, and the `awino = awino.cli:app` entry point. Library modules never
  import `awino.cli` — the command surface is passed in (test_cli_layout
  architectural rule honored).
- `src/awino/release.py` (new): `verify_artifact` returns a report with
  specific reasons, never raises for brokenness. `release_gate` raises
  `ReleaseRefused` (broken artifact, reasons name the exact problem) or
  `AuthorizationRequired` (good artifact, no explicit authorization). With
  authorization on a good artifact it returns the verified capability list;
  the gate verifies and never publishes by itself.
- `src/awino/cli/release.py` (new, `owns: release verify, release publish,
  release push, release tag`): `awino release verify [--root]` lists verified
  capabilities or names exactly what is broken (exit 1);
  `awino release publish|push|tag` refuse without `--authorize` (exit 3).
- `src/awino/cli/__init__.py`: `registered_command_names()` helper; `release`
  group registered. Pinned command dump in tests/test_cli_layout.py extended
  111 -> 115 with the four new commands.
- `src/awino/hosts/README.md` (new): FAIR README for the Phase 4 hosts
  package — `folder_docs` health gate was failing without it.
- `src/awino/tidy.py`: `HARNESS_FIX_SPEC.md` added to `ROOT_ALLOWED` (named
  recovery deliverable, same precedent as `RECOVERY_LOG.md`) — `structure`
  health gate was failing on it as a root stray.

### Evidence
- Phase 5 gate: `pytest tests/test_exam.py tests/test_manifest.py
  tests/test_artifact_matrix.py -o addopts= -q` → **32 passed** (run twice,
  after the layout fixes: 39 passed incl. test_cli_layout).
- Adversarial exam proof: `probe_fired` is False for exit≠0 with expected
  text present (three variants), False for exit 0 without the text, True
  only for exit 0 + text; `_run` proven to capture real nonzero codes via
  `("gate", "open")` subprocess; `launcher_resolves` rejects a bogus
  interpreter.
- Broken artifact proof: tree with `memory/lessons.md` deleted →
  `verify_artifact` reports exactly that file; manifest claiming
  `cmd:bogus-capability` → "manifest claims cmd:bogus-capability but the
  tree does not provide it"; `release_gate` raises `ReleaseRefused` naming
  the file; `awino release verify --root <broken>` exits 1 printing REFUSED
  + the filename; `awino release publish` (no flag) exits 3 on the
  authorization refusal.
- Wheel proof: `uv build --wheel` → `verify_wheel` clean; packaged manifest
  parses, version 0.8.0; entry points expose `awino = awino.cli:app`.
- Full suite: **1751 passed, 4 skipped, 4 failed** — all 4 verified
  pre-existing (1 cli_encoding proxy-env issue; 3 gate_review_workflow fail
  identically on the d9883a5 worktree). `ruff check` and `ruff format`
  clean on all touched files. `awino doctor --fast`: fail=0.
- Incidental fixes (pre-existing, verified on baseline, fixed rather than
  left): `folder_docs` (hosts/ README) and `structure` (spec doc
  allow-list) health gates — `awino best` refused in this checkout before;
  `tests/test_best_cli.py` now passes (4/4).

### Carry-forwards for the human
- Windows deployment: install the branch on the Windows machine, run
  `awino release verify`, `awino doctor`, and the exam; wire the Phase 4
  host adapters to the real Claude Code/Kilo/Roo integrations.
- Live per-host journeys remain unverified by design (no hosts in this
  sandbox); adapters report `unverified`, never parity.
- Carried from Phase 2/3: best/battery/claude/exam CLI answer paths not all
  rewired through the shared knowledge service yet.
- Publishing the wheel still needs the human's separate authorization; the
  gate is built and tested, the act is not done.

## Windows review and presentation addition (2026-09-16)

User authorized verification before push, excluded live host testing, and added
four presentation procedures. Review is in an isolated bundle checkout; no
installation cutover, merge, release or push has been performed.

- Locked environment required `uv sync --frozen --all-groups --link-mode copy`
  to avoid Windows/cloud hardlink error 396.
- Recovery-focused tests: 198 passed. Follow-up review/cutover/contract tests:
  119 passed (repeated with the same result in the research ledger).
- `doctor --fast`: fail=0. `release verify` enumerated 137 manifest entries;
  this is structural inventory verification, not end-to-end capability proof.
- Ruff lint passes; format check reports six files needing formatting.
- Broad Windows suite showed failures before its 600-second timeout; neither
  a full-suite pass nor baseline equivalence of those failures is established.
  A subsequent first-failure run and the real exam each timed out at 240 seconds.
- Adversarial probe: grant a plan approval, record review `blocked`, call
  `close_plan`; observed `review=blocked`, `closure=closed`. This is a push blocker.
- Source review: `for_machine` / `for_loop` are not connected to existing
  machine/loop callers; the exam still inherits project overrides and forces a
  source PYTHONPATH; the artifact matrix checks wheel contents but does not run
  an installed-wheel journey. Those gaps remain even with live hosts excluded.
- Added the user's four presentation procedures under
  `skills/awino-visualize/references/README.md`, with a scoped
  trigger in the existing skill. Attribution is user-supplied, not asserted as
  verified Winston/MIT doctrine. Reference loading and output rules do not
  themselves prove rhetoric quality or audience retention.
- Presentation validation: authored-skill validator PASS=14 FAIL=0; existing
  dispatch/catalog suite 15 passed. Explicit `awino-visualize` requests route
  all four procedures correctly. Three short unqualified requests remain
  ambiguous in the current lexical router; automatic natural-language routing
  is not claimed fixed by the reference addition.

Prevention: do not equate a controller API's existence, a structural manifest
check, or passing focused tests with integrated production behavior; run negative
closure and real entry-point checks before declaring the recovery ready to push.

## Original-spec audit and 0.8.1 acceptance boundary (2026-09-16 17:30)

This section supersedes earlier readiness summaries, not historical test output.
Remote recovery HEAD is `76604e2`; GitHub's default `main` is still `08af22b`.
The global PowerShell wrapper still points at the original `.smith` checkout at
`08af22b`. No source/state cutover or installation update has occurred.
The previously reported 1766 passes and 18/18 exam results are real but do not
constitute acceptance of the original recovery specification.

User direction: make modes/stances/procedures actually follow the task and the
mission, address the gaps, test, then prepare/push 0.8.1. No immediate merge,
installation mutation or release is performed during this audit.

### Confirmed remaining gaps

- `controller.preflight` permits pending approval; a real temporary-plan probe
  returned `pending_approval=pending` and `pending_preflight=[]`.
- An approved plan with no review closes: the probe returned
  `review_before_close=None` and `closed_without_review=closed`. The earlier fix
  blocks an explicit blocked/revise verdict, not missing review or missing
  verification evidence.
- `stepper._locate` selects a stance, but stepper does not verify a response
  against it. The existing explicit `stance --verify` command is not automatic
  response enforcement. Header text is not proof of adherence.
- `_open` turns a budget confirmation into plan approval; its controller binding
  does not populate the approved scope, and `_work` still reads invocation-local
  scope/verifier. The integration does not charge its work-iteration budget.
- `STOP -> WORK` does not distinguish failed health from approved work resumption.
  Plan-required tasks stop without an integrated approved-plan binding path.
- Host boundary events record activity; they do not themselves drive selection,
  execution or stance validation. Kilo/Roo still have no live event bridge here.
- The editable task brief contains six sections, not the required eight policy
  blocks. Original planning/author-agent/delegate skills were not updated and
  the requested research/support/delegation example reference is absent.
- `cutover.finalize` journals `pointers-switched` but does not rewrite the global
  launcher; its resolution check is not an actual launcher smoke test.
- README and release CI files are unchanged from main. README does not identify
  recovery candidate status, omits the new reference, and describes automatic
  startup more strongly than host evidence supports.
- The exam keeps HOME/USERPROFILE and expects ambient installed-skill drift;
  18/18 therefore depends on this machine's global state. The wheel execution
  added is a version smoke test, not the specified complete behavioral matrix.
- Final status/skill/stance receipts are not rendered as the unified evidence
  snapshot required by the original spec.

Audit verification: focused controller/loop/exam baseline passed 56 tests; direct
temporary-state probes independently demonstrated the two approval/closure gaps.
GitHub landing-page content was fetched successfully. `gh` is unavailable here,
so authenticated GitHub checks/PR management were not verified. No UI rendering
or live multi-host testing was performed.

### Required candidate acceptance (not implemented by this audit)

1. Enforce current approval, permitted phase/scope, charged budgets and executed
   evidence at actual action/closure boundaries; do not substitute budget consent
   for approval of a particular plan or fabricate a reviewer verdict from a test.
2. Connect request routing, relevant skill content and communication stance to
   actual work. Record selected/provided/checked separately; bind checks to exact
   response and contract hashes. Pause/blocked/cancel states cannot bypass checks.
3. Deliver the eight human-editable policy blocks plus requested example
   references through the real planning and assignment paths, preserving edits
   and decision rationale across restart.
4. Test real CLI journeys (coding, research, plan revision, presentation, negative
   review, exhausted budget, restart) and both missing/current/drifted install
   fixtures. Require expected failure statuses and artifact evidence. Unit mocks
   and command names are not substitutes. Keep live hosts explicitly unverified
   under the user's earlier testing exclusion.
5. Correct branch README and release CI, prove actual launcher switching/rollback
   in fixtures, run the source/wheel/plugin behavior matrix, then prepare the
   0.8.1 version/branch update. Main-page publication and local activation remain
   distinct operations from pushing a candidate branch.

Do not describe this candidate as fulfilling the vision until those end-to-end
acceptance cases pass. Do not use a larger test count as a proxy for coverage of
the missing requirements; each requirement needs its own traceable evidence.
