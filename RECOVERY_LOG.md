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
