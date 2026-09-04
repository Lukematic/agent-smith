# A.W.I.N.O. Dispatch Loop — Executable Plan

Status: **executed 2026-09-02..03 (S1-S10 closed).** Its in-process runner `run_dispatch` is superseded by floors and scheduled for deletion in one-operator-spec S5 (Seed e860); this file archives with it.

One sentence: build the elevator operator. The user describes a need in plain language;
A.W.I.N.O. matches it to a capability, dispatches a scoped fresh-context agent, waits,
independently verifies the result, routes to the next floor or reports blocked, and
records the whole trip in the ledger.

---

## 0. Why this exists

Every mechanical piece already works. Nothing chains them. That is the entire defect.

| Elevator role | Existing mechanism | State |
| --- | --- | --- |
| Passenger describes a need without knowing floor numbers | free-text request | — |
| Operator matches description to a floor | `SkillCatalog.recommend`, `src/smith/skill_catalog.py:96-109` | REAL, advisory only |
| Operator presses the button and stays behind | `spawn.spawn_one`, fresh subprocess | REAL |
| Operator waits | blocking subprocess with timeout | REAL |
| Passenger says "done" | `SpawnResult.claimed_complete` | REAL |
| Operator distrusts the claim and re-checks | `spawn.verify` → `SpawnResult.trustworthy` | REAL |
| Operator chooses the next floor | `graph.run_worker_reviewer_graph` routing | REAL |
| Trip is recorded | `Ledger.append_artifact`, `Ledger.checkpoint` | REAL |
| **The loop that chains all of the above** | none | **MISSING** |
| "Something is off with you, go to this floor first" | none | **MISSING** |

`awino skills --route` returns a recommendation and stops. A human must then decide,
load, and act. That is the "full toolbox that never opens" problem, stated mechanically.

---

## 1. Verified starting state

Probed 2026-09-02. Do not re-derive. Re-probe only when a step fails.

| Fact | Evidence |
| --- | --- |
| Deterministic scoring exists: `3 × name-token matches + description-token matches` | `skill_catalog.py:101` |
| Intent overrides exist for concrete failures vs vague complaints | `_intent_skill`, `skill_catalog.py:135-142` |
| 16 canonical skills discovered with project → global → bundled precedence | `SkillCatalog._discover` |
| Fresh subprocess spawning with unique invocation IDs | `spawn.spawn_one` |
| Nesting is refused via `AWINO_SPAWN_DEPTH` | `spawn.current_depth` |
| Reviewer read-only is mechanically enforceable on Claude only | `Runner.enforces_read_only` |
| Independent re-verification already implemented | `spawn.verify` |
| Bounded worker/reviewer routing already implemented | `src/smith/graph.py` |
| Attempt ceiling is 3 | `MAX_ATTEMPTS`, `src/smith/enforce.py` |
| Durable artifacts, checkpoints, provenance all exist | `src/smith/enforce.py` |
| `UserPromptSubmit` hook is declared and implemented | `hooks/hooks.json`; `cli.py:1131-1159` |
| Kilo install copies persona and skills, **not** hooks | `harness.py:370-420` |
| Persona and mode provable for Claude, Kilo, Roo | `~/.claude/agents/awino.md`; `~/.config/kilo/agents/awino.md`; `modes.py` `EDITORS["roo"]` |
| Persona location **not** provable for Cline or Codex | probe found `skills/` only, no agent directory |
| Seeds tracker present | `sd 0.5.15`, 36 open issues |

Decision recorded from this conversation: **option (c)** — Claude, Kilo, and Roo only.
Cline and Codex are deferred because an operator with no floor is worse than no
elevator.

---

## 2. What "guarantee" means here

Stated plainly so no one later claims more than was built.

| Guaranteed by a test | Not guaranteed |
| --- | --- |
| Given a request, routing returns a specific skill, and a wrong route fails the suite | That every phrasing a human invents routes correctly |
| A dispatched agent runs in a fresh subprocess with a distinct identity | That the agent inside it does good work |
| A completion claim is re-verified before acceptance | That the verification command covers every case |
| Preconditions are checked before dispatch | That every relevant precondition was thought of |
| Every trip is recorded in the ledger | That a human reads the record |
| In Claude Code, dispatch fires on every prompt via the installed hook | In Kilo/Roo, hooks are absent, so the persona must call `awino dispatch` |

The reliability improvement is structural: **one** compliance point instead of twenty.
Claiming full autonomy would be `UNGROUNDED_CAPABILITY`.

---

## 3. Order of work, and why

Dispatch first. Rationale: the dispatch loop is the experience the user asked for, and
it is testable against today's plumbing. The four plumbing phases in
`cross-harness-update-spec.md` become **Seeds 6–9**, executed after the operator exists,
because dispatch does not depend on them.

Exception: Seed 5 (`awino start`) moves earlier, because dispatch needs a health
precondition to answer "something is off with you, go to this floor first."

---

## 4. Seeds

Create these with `sd create` before any code. One Seed, one run, one commit.
Dependencies are strict; `sd dep add <blocked> <blocker>`.

| Seed | Title | Depends on |
| --- | --- | --- |
| S1 | Dispatch routing decision: deterministic match, confidence, ambiguity | — |
| S2 | Precondition gate: refuse or reroute when the project is unhealthy | S1 |
| S3 | Dispatch execution loop: spawn, wait, verify, route, record | S1, S2 |
| S4 | `awino dispatch` CLI with explicit budget confirmation | S3 |
| S5 | `awino start`: one startup command | — |
| S6 | Hash-verified skill propagation (`skills-status`, `install-refresh`) | — |
| S7 | Roo harness target; keep Cline/Codex deferred with recorded reason | S6 |
| S8 | Self-healing `awino update` | S5, S6, S7 |
| S9 | Persona and docs wiring so one instruction replaces twenty | S4, S5, S8 |
| S10 | Config review, issue remediation, final acceptance, push | S1–S9 |

---

## 5. Global rules for the executing agent

1. Seed order is strict. Do not start a Seed until its blockers are closed.
2. One `awino gate open` run per Seed; close it before the next.
3. TDD. Write the failing test, paste the failure, then implement. A test passing on
   first run is not evidence.
4. Never weaken a test. No deleted assertions, no added skips.
5. Windows is the reference platform. No POSIX-only commands.
6. Three strikes on one gate: stop, report attempts, escalate.
7. No guessed paths. Every third-party path traces to a pasted probe.
8. Hash, never grep, for propagation checks.
9. Do not touch: `enforce.py`, `graph.py`, `spawn.py`, `updater.py`,
   `config_review.py`, anything outside `.smith/`, or the marketplace plugin copy.
10. Commit per Seed. Push only in S10, and only after config review passes.

Commands, all from `.smith`:

```powershell
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run awino doctor --fast
```

---

## 6. S1 — Dispatch routing decision

**Touches:** `src/smith/dispatch.py` (new), `tests/test_dispatch_routing.py` (new).

Routing must be a pure function so it is testable without spawning anything.

- [x] **S1.1** Open the run:

  ```powershell
  uv run awino gate open code-change "Dispatch routing decision" --plan specs/dispatch-loop-spec.md --by awino --scope src/smith/dispatch.py --scope tests/test_dispatch_routing.py
  uv run awino gate plan approve --by "<human>" --reason "S1 approved"
  ```

- [x] **S1.2** Write failing tests:
  1. `"pytest is failing with a ValueError"` → `awino-debug`, confidence `high`.
  2. `"the agent keeps ignoring my instructions"` → `awino-triage`, confidence `high`.
  3. `"what is a harness"` → `awino-consult`.
  4. `"refactor the loader across twelve files"` → `awino-rpi`.
  5. `"three independent workstreams"` → `awino-delegate`.
  6. `"do the thing"` → confidence `none`, `question` populated, **no** skill chosen.
  7. Two skills within one point of each other → confidence `ambiguous`, both returned,
     `question` names the distinction.
  8. Routing is pure: calling it twice with the same input returns an identical result
     and performs no filesystem write.

- [x] **S1.3** Run tests. Paste failures.

- [x] **S1.4** Implement:

  ```python
  @dataclass(frozen=True)
  class DispatchDecision:
      request: str
      skill: Skill | None
      alternatives: tuple[Skill, ...]
      confidence: str          # high | ambiguous | none
      question: str | None
      rationale: str
  ```

  `decide(request, catalog) -> DispatchDecision`, reusing `SkillCatalog.recommend` and
  `_intent_skill`. No new scoring system.

**Exit gate**

| Check | Required |
| --- | --- |
| `uv run pytest tests/test_dispatch_routing.py -q` | 8 passed |
| `uv run pytest -q` | full suite passes |
| lint + format | clean |
| purity test | proves no filesystem writes |
| `uv run awino gate close` | exit 0 |

**Forward** when routing is deterministic and ambiguity produces a question rather than
a guess.
**Back** when a new scoring heuristic was invented instead of reusing the catalog.

---

## 7. S2 — Precondition gate

**Touches:** `src/smith/dispatch.py`, `tests/test_dispatch_preconditions.py` (new).

This is "something is off with you — go to this floor first," made mechanical.

- [x] **S2.1** Open the run. Approve.

- [x] **S2.2** Write failing tests:
  1. Health `REFUSED` → dispatch refuses, names the failing check, recommends the
     remedy. Nothing is spawned.
  2. A failing gate already recorded in the active run → reroute to `awino-debug`
     before the requested skill, with the reason stated.
  3. Pending human decision on a checkpoint → refuse and surface the decision.
  4. Healthy project with no active run → preconditions pass.
  5. Precondition evaluation is read-only.

- [x] **S2.3** Paste failures.

- [x] **S2.4** Implement `preflight(project, ledger) -> Preflight` with fields `ok`,
      `blockers`, `reroute_to`, `detail`. Compose `health.run_all(fast=True)` and
      `Ledger.inspect_current()`. Do not duplicate their logic.

**Exit gate**

| Check | Required |
| --- | --- |
| new tests | 5 passed |
| `uv run pytest -q` | passes |
| lint + format | clean |
| read-only proof | test asserts no writes |
| `gate close` | exit 0 |

**Forward** when an unhealthy project cannot be dispatched into.
**Back** when preflight mutates state or silently proceeds past a blocker.

---

## 8. S3 — Dispatch execution loop

**Touches:** `src/smith/dispatch.py`, `tests/test_dispatch_loop.py` (new).

The seven-step trip: match → confirm → dispatch → wait → verify → route → record.

- [x] **S3.1** Open the run. Approve.

- [x] **S3.2** Write failing tests, using an injected executor for determinism plus one
      real-subprocess test:
  1. Happy path: verified success → `COMPLETE`, artifacts recorded.
  2. Claim without verification → **not** accepted; outcome `UNVERIFIED`.
  3. Verification fails → reroute to remediation, second dispatch occurs, exact failure
     text is carried into it.
  4. `confidence == "none"` → outcome `QUESTION`, nothing spawned.
  5. Preflight blocker → outcome `BLOCKED`, nothing spawned.
  6. Iteration cap: at most `MAX_ATTEMPTS` floors, then `MAX_ITERATIONS`.
  7. Nested invocation (`AWINO_SPAWN_DEPTH >= 1`) → refused.
  8. Every trip appends `dispatch.route` artifacts and one terminal artifact.
  9. Each dispatched agent has a distinct `invocation_id`, asserted from persisted
     artifacts.
  10. Real subprocess test proving two dispatches produce two distinct invocation IDs.

- [x] **S3.3** Paste failures.

- [x] **S3.4** Implement `run_dispatch(...) -> DispatchResult` with outcomes
      `COMPLETE | UNVERIFIED | BLOCKED | QUESTION | MAX_ITERATIONS`. Reuse
      `spawn.spawn_one`, `spawn.verify`, `Ledger.append_artifact`,
      `Ledger.checkpoint`. Reviewer-style read-only dispatch requires
      `Runner.enforces_read_only`; otherwise refuse with a stated reason.

**Exit gate**

| Check | Required |
| --- | --- |
| new tests | 10 passed |
| `uv run pytest -q` | passes |
| lint + format | clean |
| artifact inspection | routes and terminal outcome persisted for every path |
| distinct identities | proven from persisted artifacts, not from the return value |
| `gate close` | exit 0 |

**Forward** when a completion claim alone can never produce `COMPLETE`.
**Back** when the loop accepts `claimed_complete` without `trustworthy`, or exceeds the
cap.

---

## 9. S4 — `awino dispatch` CLI

**Touches:** `src/smith/cli.py`, `tests/test_dispatch_cli.py` (new).

- [x] **S4.1** Open the run. Approve.

- [x] **S4.2** Write failing tests:
  1. Missing `--confirm-budget` → refuses, exit 2, nothing spawned.
  2. `--max-floors` outside `1..MAX_ATTEMPTS` → refuses, exit 2, no silent clamp.
  3. `--dry-run` → prints the routing decision and preflight verdict; spawns nothing.
  4. Ambiguous request → prints the single clarifying question, exit code documented.
  5. Output prints the request, matched skill, confidence, preflight verdict, each
     floor with its outcome, and a terminal line.
  6. Exit code is nonzero unless the outcome is `COMPLETE`.
  7. Runner without enforceable read-only → refuses with a stated reason.

- [x] **S4.3** Paste failures.

- [x] **S4.4** Implement:

  ```powershell
  awino dispatch "<request>" [--confirm-budget] [--max-floors N] [--dry-run] [--runner claude] [--run <id>]
  ```

  Print `BUDGET_CONFIRMED  floors=N  subprocesses<=2N` before executing. Never close a
  gate run; `gate close` remains the completion authority.

- [x] **S4.5** Live-verify and paste output:

  ```powershell
  uv run awino dispatch "i have a conceptual agentic engineering question about harnesses" --dry-run
  uv run awino dispatch "do the thing" --dry-run
  ```

**Exit gate**

| Check | Required |
| --- | --- |
| new tests | 7 passed |
| `uv run pytest -q` | passes |
| lint + format | clean |
| both live `--dry-run` calls | behave as specified, nothing spawned |
| `gate close` | exit 0 |

**Forward** when the CLI cannot spend budget without explicit confirmation.
**Back** when it clamps values silently or closes a run.

---

## 10. S5 — `awino start`

**Touches:** `src/smith/cli.py`, `tests/test_start_command.py` (new),
`docs/user-guide.md`.

- [x] **S5.1** Open the run. Approve.

- [x] **S5.2** Write failing tests asserting `start` prints all eight contract fields —
      `Project`, `Mission confidence`, `Toolchain`, `Tracker`, `Active run`,
      `Pending human decision`, `Next recommended action`, `Route skill` — plus:
  1. exits nonzero when health is `REFUSED`;
  2. read-only by default, proven by a before/after directory snapshot;
  3. `--fix` performs only mechanical repairs and reports the rest;
  4. never opens a ledger run;
  5. reports the gap without crashing when no `.smith/` exists.

- [x] **S5.3** Paste failures.

- [x] **S5.4** Implement by composing the internals behind `context`, `mission`,
      `doctor --fast`, `resume`, and `skills --route`. No duplicated logic.

- [x] **S5.5** Update `docs/user-guide.md`: `awino start` is the documented first
      command; the others are granular fallbacks.

**Exit gate**

| Check | Required |
| --- | --- |
| new tests | pass |
| `uv run pytest -q` | passes |
| lint + format | clean |
| `uv run awino start` | prints all eight fields |
| read-only default | proven by test |
| `gate close` | exit 0 |

**Forward** when `start` output alone is enough to begin work.
**Back** when it mutates state without `--fix`.

---

## 11. S6 — Hash-verified skill propagation

**Touches:** `src/smith/harness.py`, `src/smith/cli.py`,
`tests/test_skill_propagation.py` (new).

Fixes "I fix things and nothing happens."

**Do not grep.** The corrected skill text legitimately contains the string
`registry_build.ps1` inside a sentence stating the script no longer exists. A substring
check is `LINTER_FALSE_POSITIVE` (`memory/lessons.md:22`).

- [x] **S6.1** Open the run. Approve.

- [x] **S6.2** Write failing tests:
  1. `test_matching_copy_mentioning_retired_script_is_not_flagged` — the regression test
     for the false positive.
  2. Byte difference → `drifted`.
  3. Missing installed skill → `absent`.
  4. Refresh repairs installer-owned drift only; human-modified copy is reported,
     preserved byte-for-byte, and backed up before any write.
  5. Refresh is idempotent.

- [x] **S6.3** Paste failures.

- [x] **S6.4** Implement `SkillDrift`, `skill_drift(...)`, `refresh_skills(...)` in
      `harness.py` using `ownership.sha256_path`, `ownership.entry`,
      `ownership.unchanged`, `ownership.backup`, `ownership.record`.

- [x] **S6.5** Add `awino skills-status [--json]` and `awino install-refresh
      [--overwrite]`.

- [x] **S6.6** Live-verify and paste all three outputs:

  ```powershell
  uv run awino skills-status
  uv run awino install-refresh
  uv run awino skills-status
  ```

**Exit gate**

| Check | Required |
| --- | --- |
| new tests | 5 passed |
| `uv run pytest -q` | passes |
| lint + format | clean |
| second `skills-status` | zero drifted installer-owned copies |
| human-modified fixture | unchanged, reported not overwritten |
| `gate close` | exit 0 |

**Forward** when drift is hash-measured and refresh is idempotent.
**Back** when a human-edited file is overwritten. Restore from `.awino-backups`.

---

## 12. S7 — Roo harness target; Cline and Codex deferred

**Touches:** `src/smith/harness.py`, `tests/test_harness_surfaces.py` (new),
`docs/install.md`.

- [x] **S7.1** Open the run. Approve.

- [x] **S7.2** Re-confirm and paste:

  ```powershell
  Get-ChildItem "$env:USERPROFILE\.roo\skills" -Force | Select-Object -First 3 Name
  Test-Path "$env:USERPROFILE\.config\kilo\agents\awino.md"
  Test-Path "$env:USERPROFILE\.claude\agents\awino.md"
  ```

- [x] **S7.3** Write failing tests:
  1. Roo skills install to `~/.roo/skills/<name>/SKILL.md`.
  2. Roo mode support still resolves through `modes.py`.
  3. `install-status` reports Cline and Codex as `DEFERRED` with the reason
     "persona location not proven", not as failures.
  4. Nothing is written to `~/.cline` or `~/.codex`.

- [x] **S7.4** Implement `Harness.ROO` with the probed root. Do **not** add Cline or
      Codex members; record the deferral in `docs/install.md` with the probe evidence.

- [x] **S7.5** Live-install and paste output:

  ```powershell
  uv run awino install --harness roo --scope global --force
  uv run awino install-status
  uv run awino skills-status
  ```

**Exit gate**

| Check | Required |
| --- | --- |
| new tests | 4 passed |
| `uv run pytest -q` | passes |
| lint + format | clean |
| on-disk check | `~/.roo/skills/awino-consult/SKILL.md` exists |
| `~/.cline`, `~/.codex` | untouched |
| `gate close` | exit 0 |

**Forward** when Roo is installed from probe-verified paths and deferrals are explicit.
**Back** when any path was assumed.

---

## 13. S8 — Self-healing `awino update`

**Touches:** `src/smith/cli.py`, `tests/test_update_selfheal.py` (new).

- [x] **S8.1** Open the run. Approve.

- [x] **S8.2** Write failing tests:
  1. `update` ensures project state: a `.smith/` lacking `run/` gains it.
  2. Refreshes **detected** harnesses only; an absent harness stays absent.
  3. Preserves project-specific state: `project.yaml`, `memory/`, `run/` byte-identical
     before and after. This is the git-rebase behavior the user described.
  4. Idempotent: a second consecutive `update` reports zero changes.
  5. Prints one summary block ending with the active version.

- [x] **S8.3** Paste failures.

- [x] **S8.4** Implement inside `update_command`, after the existing version branch:
      `ensure_state()` → `refresh_skills` for detected targets → `doctor --fast` → one
      summary. Call `updater.snapshot`/`restore`; do not modify `updater.py`.

- [x] **S8.5** Live-verify and paste:

  ```powershell
  uv run awino update
  uv run awino update
  uv run awino doctor --fast
  ```

**Exit gate**

| Check | Required |
| --- | --- |
| new tests | 5 passed |
| `uv run pytest -q` | passes |
| lint + format | clean |
| second `update` | no further changes |
| project `memory/` hash | identical before and after |
| absent harness | still absent |
| `gate close` | exit 0 |

**Forward** when `update` is idempotent and provably non-destructive.
**Back** when it installs into an unrequested harness or alters project state. Restore
from the printed `BACKUP`.

---

## 14. S9 — Wire the operator in: one instruction, not twenty

**Touches:** `agents/awino.md`, `AWINO.md`, `src/smith/modes.py`,
`tests/test_dispatch_wiring.py` (new), `docs/agent-guide.md`.

- [x] **S9.1** Open the run. Approve.

- [x] **S9.2** Write failing tests:
  1. The persona's startup section names exactly `awino start` as the first command.
  2. The persona instructs `awino dispatch` for any actionable request, replacing the
     multi-command routing list.
  3. Generated Kilo and Roo modes contain the same two instructions
     (assert against `modes.py` output, not a hand-edited file).
  4. Docs state honestly that Claude Code can fire dispatch from the installed
     `UserPromptSubmit` hook, while Kilo and Roo depend on the persona calling it.

- [x] **S9.3** Paste failures.

- [x] **S9.4** Edit the persona, constitution, and mode generator so the routing table
      collapses into `awino dispatch`. Keep the failure-mode and honesty sections
      intact.

- [x] **S9.5** Propagate and verify:

  ```powershell
  uv run awino install --force
  uv run awino install-mode --force
  uv run awino skills-status
  uv run awino mode-status
  ```

**Exit gate**

| Check | Required |
| --- | --- |
| new tests | 4 passed |
| `uv run pytest -q` | passes |
| lint + format | clean |
| `skills-status` | zero drift after propagation |
| `mode-status` | modes present and current for Kilo and Roo |
| `gate close` | exit 0 |

**Forward** when one instruction replaces the twenty-row routing table.
**Back** when any doc claims dispatch fires automatically in Kilo or Roo.

---

## 15. S10 — Config review, remediation, acceptance, push

**Status before S10 starts (2026-09-03):** S1–S9 closed, each with a ledger run whose
five gates executed (planned attested; tested, linted, tests_not_weakened,
scope_respected executed). Full suite: 726 passed, 1 skipped. Commits `d97d169`
through `34b6902` on `main`, not yet pushed.

### Execution log — what actually happened, including deviations

| Seed | Run | Commit | Deviations from the written step |
| --- | --- | --- | --- |
| S1 | `20260902-220504-85ac67` | `d97d169` | Test phrases in §6.2 ("what is a harness", "do the thing") did not route as the spec assumed. Verified against the real catalog and rewritten to phrases whose scores were measured before being asserted. Rule 7 applied to test inputs, not only paths. |
| S2 | `20260902-221951-8da930` | `47f899b` | None. |
| S3 | `20260902-223058-cc061f` | `5752c6e` | `verified is None` produces `UNVERIFIED`; `False` reroutes; `True` completes. Spec §8.2 listed `UNVERIFIED` and reroute as separate tests without naming the three-state rule; the code now names it explicitly. |
| S4 | `20260902-224743-43d82a` | `1a46091` | Tests caught that `--dry-run` bypassed `--max-floors` validation. Validation moved ahead of the dry-run short-circuit. |
| S5 | `20260902-230355-16664a` | `cd2fa34` | `_ledger()` calls `ensure_state()` and would have written `.smith/.gitignore`. `start` constructs `Ledger` directly to stay read-only. `--fix` runs `fix.fix_scaffold` only; there is no `Fixer` class. |
| S6 | `20260902-232241-8b9793` | `a1ff943` | `refresh_skills` also installs `absent` skills, not only `drifted`. Live run filled the empty Goose target. Live `skills-status` showed every prior copy already `current`, confirming the earlier grep-based "stale" finding was a false positive. |
| S7 | `20260902-233742-290d06` | `1bbf8ab` | Added `Harness.installs_persona_file` so Roo is skills-only; agent selection stays with `modes.py`. A guessed `~/.roo/agents/` path was written and then removed before commit. Cline and Codex are not `Harness` members, so `install-status` cannot mark them `DEFERRED`; the deferral is recorded in `docs/install.md` instead. |
| S8 | `20260902-235358-ea7b02` | `170232f` | Self-healing applies to the standalone-clone path only. The Claude-plugin path reinstalls via `claude plugin update` and needs a restart, so refreshing there would read pre-restart files. This machine is a plugin install; the standalone path was live-verified against a disposable clone with `AWINO_HOME` and `USERPROFILE` overridden. |
| S9 | `20260903-001232-c760e1` | `34b6902` | Adding CLI examples to the shared `role` text broke `test_modes_without_command_do_not_advertise_executable_cli_commands`: read-only modes must not advertise commands. Moved into the primary mode's `custom_instructions` only. |

### Process defect found while closing S9

A chained `&&` command that recorded four gates, closed the Seed, and closed the run
returned **no output and recorded nothing**; the run still showed `satisfied=1
missing=4`. Nothing failed loudly. Re-running each `gate record` individually
succeeded on the first attempt. Cause not determined (likely the shell swallowing a
nonzero intermediate exit inside the `&&` chain). Consequence: **verify `gate status`
after any chained gate command; never infer closure from a silent chain.** Recorded
as a lesson in S10.

### S10 steps

**Touches:** whatever the review names, within the declared scope only.

- [x] **S10.1** Open a `refactor` run for the review pass. Approve.

- [x] **S10.2** Run the config review and paste output:

  ```powershell
  uv run awino review-config
  uv run awino validate skills agents -v
  uv run awino validate --selftest
  uv run awino tidy --dry-run
  ```

- [x] **S10.3** Fix every finding inside scope. Findings outside scope are filed as new
      Seeds, not silently fixed.

- [x] **S10.4** Full acceptance in this project:

  ```powershell
  uv run pytest -q
  uv run ruff check src tests
  uv run ruff format --check src tests
  uv run awino doctor --fast
  uv run awino install-status
  uv run awino skills-status
  uv run awino start
  uv run awino dispatch "i have a conceptual agentic engineering question about harnesses" --dry-run
  ```

- [x] **S10.5** Second-project acceptance, for example `treads-pipeline`:

  ```powershell
  uv run awino start
  uv run awino update
  uv run awino skills-status
  uv run awino doctor --fast
  ```

- [x] **S10.6** Independent review, then close:

  ```powershell
  uv run awino gate review --verdict approved --by "<distinct reviewer>" --risks "<residual risk>"
  uv run awino gate close
  ```

- [x] **S10.7** Commit per Seed, then push. Never stage `config_review.py`.

**Acceptance — all must hold**

- [x] every command above exits zero;
- [x] `awino dispatch` completes a real trip with two distinct invocation IDs in the
      ledger;
- [x] `skills-status` reports zero drifted installer-owned copies;
- [x] `install-status` lists Roo; Cline and Codex are absent from `Harness` by design and their deferral is recorded in `docs/install.md` (S7 deviation);
- [x] the second project's `.smith/memory/` is byte-identical before and after;
- [x] the second project gained no `knowledge/` directory;
- [x] `git -C .smith status --short` shows `config_review.py` unchanged from its
      pre-existing state;
- [x] no file outside `.smith/` was modified;
- [x] a dated lesson was appended to `memory/lessons.md`.

---

## 16. How each stated problem is proven fixed

| User's problem | Seed | The test that proves it |
| --- | --- | --- |
| "It has the tools but never uses them" | S3, S4, S9 | A dispatch trip runs match → spawn → verify → route → record end to end, asserted from persisted artifacts |
| "I fix things and nothing happens" | S6 | Modify an installed copy → drift reported → refresh → drift zero, hash-based |
| "Works the same in any tool" | S7, S9 | Roo installs to a probed path; mode generator output asserted; Cline/Codex explicitly deferred |
| "Pull in what's missing, keep project things" | S8 | Byte-compare `project.yaml`, `memory/`, `run/` before and after |
| "Startup was never delivered" | S5 | Eight-field contract asserted; read-only default proven |
| "Talk plainly, act underneath" | S1, S2, S4 | Plain-language request routes deterministically; ambiguity yields one question; unhealthy project reroutes |

---

## 17. Explicitly out of scope

- Autonomous scheduling, cron, or background triggers.
- Dispatch reviewers on runners without enforceable read-only.
- Cline and Codex persona installation, until a path is proven.
- Republishing the marketplace plugin copy.
- `~/.roo/rules/` and `~/.roo/commands/` integration.
- Any change to `enforce.py`, `graph.py`, `spawn.py`, `updater.py`,
  `config_review.py`.
- Pushing before S10 passes.

---

## 18. Approval

- [ ] Human approved this plan.
- [ ] Approver: ____________________  Date: ____________

If approval is deferred, resume with:

```powershell
uv run awino gate plan status
```
