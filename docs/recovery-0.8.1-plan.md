# Recovery 0.8.1 execution plan

## Purpose

Turn the audited recovery branch into a working `main` release candidate that
delivers the one-door partnership: `awino best` (or a supported host turn)
identifies the project and mission, selects the right skill and stance,
follows an approved plan, executes within budgets, verifies, refuses
unsupported completion, and resumes after restart.

## Audience

The owner and the implementation agent executing the recovery. Reviewers use
the acceptance table to decide whether the candidate may be merged.

## Decisions recorded (2026-09-16 19:08)

| Decision | Effect on this plan |
| --- | --- |
| Whichever candidate passes acceptance becomes the working version on `main` | Merge is in scope after gates pass; no PR handoff needed |
| Switch the global launcher from `.smith` to `.awino`; retire `.smith` | Cutover step includes actual launcher rewrite, smoke and rollback |
| Refresh Claude plugin, Kilo, and agent files | Install refresh and harness pointer update are release steps |
| Presentation routing may use broader triggers | Router lexicon gains presentation vocabulary; false-positive risk accepted |
| Live host sessions are not available here | Hosts ship labeled `assisted`/`unverified`; owner tests after activation |
| `gh` unavailable; owner handles GitHub UI | Push and merge via git only |
| Build a presentation deliverable | Deck (Markdown/HTML) explaining the 0.8.1 system, generated through the presentation reference |

## Prerequisites

- Recovery checkout at `76604e2` or later in a clean working tree.
- `uv` synced with `--link-mode copy` on this OneDrive-backed machine.
- Owner available to run the first live session in Kilo after activation.

## Steps

Each step opens its own gate run, records its own evidence, and commits
separately. Stop and report after three failures of any single gate.

### Step 1 -- Approval and closure integrity

Files: `src/awino/controller.py`, `src/awino/stepper.py`,
`tests/test_controller_closure.py`, `tests/test_controller_integration.py`.

- `preflight` refuses when `approval_state` is not `approved`.
- `close_plan` refuses when `last_review` is `None` or not `ship`, or when no
  verification evidence is recorded for the plan.
- `_open` no longer grants plan-level approval from `--confirm-budget`; it
  records a `budget-confirmed` grant and requires a separate plan approval
  (`awino gate plan approve` or the machine's `--answer approve-plan`).

Expected: `pending_preflight` is non-empty; `closed_without_review` raises
`PlanNotClosable`; approved+verified+ship closes.

### Step 2 -- Plan-bound execution with charged budgets

Files: `src/awino/stepper.py`, `src/awino/loops.py`, `src/awino/machine.py`,
`tests/test_stepper.py`, `tests/test_loop_controller_integration.py`.

- `_work` charges `work_iterations` per floor via `charge_budget`; exhaustion
  yields `max-iterations`, never `verified`.
- `_open` seeds controller scope and verifier from the run; `_work` and
  `_review` read scope/verifier from the controller, not invocation flags.
- `STOP -> WORK` requires an approved controller state; a health stop cannot
  resume into work without re-passing `_locate`.

Expected: restart mid-work resumes with the same scope, verifier and remaining
budget; budget zero blocks before any floor opens.

### Step 3 -- Skill and stance receipts

Files: `src/awino/dispatch.py`, `src/awino/skill_receipts.py`,
`src/awino/stance_verify.py`, `src/awino/stepper.py`, tests alongside.

- Both dispatch render paths append the selected skill body and record a
  `skill_receipt` (selected / provided) with the content hash.
- `_execute --answer done` accepts an optional `--response <file>`; when given,
  the stance critic runs and records `stance: checked` bound to the response
  hash; when absent, records `stance: unverified`.
- Status snapshot exposes `skill_status` and `stance_status`.

Expected: no path can show `checked` without a recorded response hash.

### Step 4 -- Complete task contract (eight blocks) and examples

Files: `src/awino/task_contract.py`, `templates/task-contract.yaml`,
`skills/awino-rpi/references/task-contract-examples.md`,
`skills/awino-rpi/SKILL.md`, `skills/awino-author-agent/SKILL.md`,
`skills/awino-delegate/SKILL.md`, `tests/test_task_contract.py`.

- Brief sections become: Goal, Context, Instruction priority, Autonomy,
  Tools / delegation, Output, Verification, Stop condition, plus existing
  Files/Budgets/Verifier as structured fields.
- Missing or unknown sections fail closed; material edits invalidate approval;
  cosmetic edits keep it.
- Example reference carries the seven user-supplied task families with a
  positive and negative fixture each.

Expected: all eight fields survive draft -> edit -> approve -> spawn -> restart.

### Step 5 -- Unified status snapshot and header

Files: `src/awino/controller.py`, `src/awino/cli/project.py`,
`tests/test_header_evidence.py` (new).

- `awino header` prints
  `[A.W.I.N.O. | phase | loop | run | knowledge n/3 | skill | stance | host]`
  from stored state only; unknown fields print `unrecorded`.
- Knowledge count reads persisted receipts, not a per-process store.
- `awino best` prints this header first; the persona instructs the assistant
  to quote it rather than compose one.

Expected: a fabricated field cannot appear; restart shows identical header.

### Step 6 -- Presentation routing and deliverable

Files: `src/awino/skill_catalog.py` or router lexicon, `skills/awino-visualize/`,
`tests/test_dispatch_routing.py`, `docs/presentations/awino-0.8.1.md` (new).

- Add presentation vocabulary (opening, slides, deck, talk, pitch, persuade,
  memorable, SPIS) to the visualize route.
- Build the deliverable through the presentation reference: 90-second opening,
  slide-by-slide deck (one headline + one visual idea each), three idea cards,
  SPIS outline and value-ending close, explaining what 0.8.1 does and how it is
  verified. Speaker notes carry the evidence commands.

Expected: routing tests pass for short presentation requests; deck validates
with the skill validator; deck claims match recorded evidence.

### Step 7 -- Acceptance journeys

Files: `tests/integration/test_pairing_journey.py` (new),
`tests/test_best_cli.py`.

Journeys, each through the real CLI in a disposable project:
coding fix, research question, presentation request, plan revision after
approval, failed review, exhausted budget, and restart between EXECUTE and
VERIFY.

Expected: each journey ends in the specified state with expected refusals;
no journey writes before approval; restart loses nothing.

### Step 8 -- Cutover, launcher switch, and install refresh

Files: `src/awino/cutover.py`, `install.ps1`, `bin/awino.ps1`,
`tests/test_recovery_cutover.py`.

- `finalize` rewrites `~/.local/bin/awino.ps1` to the canonical `.awino`
  checkout, runs `awino --version` and `awino doctor --fast` through the new
  wrapper, and rolls back on failure.
- Fixture test proves switch, smoke and rollback with a fake home.
- Real cutover on this machine: `.smith` archived under `.awino/archive/`,
  wrapper switched, `awino install-refresh` refreshes Claude plugin, Kilo, and
  agent files; `awino skills-status` reports `CURRENT`.

Expected: `awino --version` from a new shell resolves to `.awino`; doctor passes.

### Step 9 -- Artifact matrix, README, version, merge

Files: `pyproject.toml`, `.claude-plugin/plugin.json`, `README.md`,
`.github/workflows/ci.yml`, `.github/workflows/publish.yml`,
`tests/test_artifact_matrix.py`, `src/awino/exam.py`.

- Exam drops HOME/USERPROFILE inheritance; installer state comes from the
  fixture; wheel and staged plugin run the exam, not only `--version`.
- README states candidate status, host evidence labels, presentation
  reference, and the eight-block brief.
- Version `0.8.1` in `pyproject.toml` and `plugin.json`; `uv lock` refreshed.
- Full gate: `just lint`, full pytest, validate, selftest, `release verify`,
  `awino exam`, artifact matrix.
- Merge `recovery/awino-0.8-harness-fix` into `main` with a merge commit, push
  `main`, tag `v0.8.1`, push tag. Publish plugin/wheel only after the owner's
  live Kilo test passes.

Expected: `main` at the merged commit; `git ls-remote` shows `v0.8.1`.

## Verification summary

| Requirement | Evidence command |
| --- | --- |
| Approval/closure integrity | `uv run --frozen pytest tests/test_controller_closure.py tests/test_controller_integration.py -o addopts= -q` |
| Plan-bound execution | `uv run --frozen pytest tests/test_stepper.py tests/test_loop_controller_integration.py -o addopts= -q` |
| Receipts and header | `uv run --frozen pytest tests/test_header_evidence.py tests/test_skill_receipts.py tests/test_stance_verify.py -o addopts= -q` |
| Eight-block contract | `uv run --frozen pytest tests/test_task_contract.py -o addopts= -q` |
| Journeys | `uv run --frozen pytest tests/integration/test_pairing_journey.py -o addopts= -q -rs` |
| Cutover | `uv run --frozen pytest tests/test_recovery_cutover.py -o addopts= -q` then `awino --version` in a new shell |
| Release gate | `just lint && uv run --frozen pytest -o addopts= -q && uv run --frozen awino validate skills agents && uv run --frozen awino validate --selftest && uv run --frozen awino release verify && uv run --frozen awino exam` |

## Blockers and how they are handled

| Blocker | Handling |
| --- | --- |
| No live Claude/Kilo/Roo session here | Ship hosts as `assisted`/`unverified`; owner runs the first live Kilo session after activation and reports |
| OneDrive hardlink failures | Always `uv sync --frozen --link-mode copy` |
| `gh` unavailable | Push/merge/tag via git; owner handles any GitHub UI |
| Three failures on one gate | Stop, record attempts in `RECOVERY_LOG.md`, report |
| Presentation false positives | Accepted by owner; negative routing tests keep chart-only requests on the chart path |

## Owner actions after Step 9

1. Open a new PowerShell: `awino --version` must show `awino 0.8.1` from `.awino`.
2. Open a new Kilo chat in a project: confirm the header appears and
   `awino best` runs without prompting.
3. In Claude Code: `/plugin marketplace update awino` then `/reload-plugins`.
4. Report any host where the automatic turn does not fire; that host stays
   `assisted` until fixed.

## Stop condition

Done when `main` holds the merged candidate tagged `v0.8.1`, the local launcher
resolves to `.awino`, install refresh reports `CURRENT`, the deck exists and
validates, and every row in the verification table has recorded passing output.
Publication of the plugin/wheel waits for the owner's live test.
