# Cross-Harness Self-Healing Update — Executable Plan

> **Superseded 2026-09-02 by `dispatch-loop-spec.md`.** Its four phases were absorbed
> as Seeds S5–S8 there (S5 `awino start`, S6 hash-verified propagation, S7 Roo target
> with Cline/Codex deferred, S8 self-healing `update`) and executed under that plan.
> Kept for the verified-starting-state table and the false-positive analysis, which
> remain accurate. Do not open runs against this file.

Status: **superseded**. No implementation runs are opened against this document.

Author this plan's changes only inside `.smith/`. Every phase is independently
verifiable and independently revertible.

---

## 0. The five problems this plan actually fixes

State each fix against the user's own words, so success is checkable rather than
interpreted.

| # | The user's problem, verbatim intent | Root cause found on disk | The fix | Phase |
| --- | --- | --- | --- | --- |
| P1 | "I fix things and nothing happens" | Skills install as **copies**, and nothing measures whether an installed copy still matches source. Drift is invisible, so a fix cannot be proven to have reached the tool in use. | `awino skills-status` measures per-file SHA-256 drift; `awino install-refresh` repairs installer-owned drift only. | 1 |
| P2 | "type `awino update` in any CLI / Codex / Cline / Roo / Claude Code" | `Harness` enum covers only Claude, Agents, Kilo, Cursor, Copilot. Cline, Roo, and Codex are installed on this machine but are not install targets. | Add three probed harness targets writing to locations verified on disk. | 2 |
| P3 | "and then pull in what it's missing … like git rebase, hold project things aside, update, add them back" | `updater.snapshot`/`restore` already preserve project state, but `update` never refreshes harness copies and never ensures the project scaffold. | `update` gains: ensure project state → refresh **detected** harnesses → health check → one summary. | 3 |
| P4 | "I asked for a startup … never delivered" | No single startup command exists. Startup is persona prose, so it is followed inconsistently. | New `awino start`: one command producing the full startup contract. | 4 |
| P5 | "it has all the tools but is not empowered / not using them" | Guarantees live in prose (`PROSE_CANNOT_ENFORCE_PROSE`, `memory/lessons.md:33`). In Kilo, lifecycle hooks are not even loaded. | Phases 3–4 convert two prose guarantees into commands that cannot be half-followed. | 3, 4 |

Out of band but recorded: making *every* guarantee mechanical is a larger program.
This plan converts the two highest-value ones and states the rest honestly.

---

## 1. Verified starting state

Probed 2026-09-02 on this machine. **Do not re-derive these. Re-probe only if a step
fails.**

### 1.1 Architecture facts

| Fact | Evidence |
| --- | --- |
| Project `.smith/` holding only `memory/` + `run/` is **correct** | `src/smith/paths.py:5-12`; `ProjectPaths.ensure_state` creates exactly `smith_dir`, `run/`, `memory/` |
| Per-project knowledge copies are the forbidden failure | `KNOWLEDGE_FORK`, `memory/lessons.md:19` |
| Knowledge home is resolved by walking up for `knowledge/` | `HOME_MARKERS = ("plugin.json", "knowledge")`, `src/smith/paths.py:21` |
| `awino update` already snapshots and restores project state | `src/smith/updater.py:31-103` |
| `awino update` never refreshes harnesses, never scaffolds state | `src/smith/cli.py:404-462` |
| Ownership tracking with SHA-256 already exists | `src/smith/ownership.py:11-103` (`sha256_path`, `entry`, `unchanged`, `backup`, `safe_write`) |
| `modes.py` already supports Roo project modes | `EDITORS["roo"] = ("Roo Code", "rooveterinaryinc.roo-cline", ".roomodes")`, `src/smith/modes.py:37-42` |
| Graph reviewer is Claude-only and capped at 3 rounds | `Runner.enforces_read_only`, `MAX_ATTEMPTS` |
| No `awino start` exists | absent from `awino --help` |

### 1.2 The false positive that must not be repeated

The source skills were already corrected to call the real CLI. A substring search for
`registry_build.ps1` still matches, because the corrected text says:

> `awino drift` is the diff (the former `.smith\scripts\registry_build.ps1` no longer
> exists — this replaced it).

The installed Kilo copy contains `awino drift` **twice** and is current.

**Therefore: propagation must be verified by content hash, never by substring.** A
grep-based check is `LINTER_FALSE_POSITIVE` (`memory/lessons.md:22`) and fails the
phase.

Residual real defect at source, confirmed by probe:

| File | State |
| --- | --- |
| `skills/awino-consult/SKILL.md` | clean |
| `skills/awino-self-update/SKILL.md` | mentions retired `.ps1` names inside explanatory prose only |
| `skills/awino-bootstrap/SKILL.md` | mentions retired `.ps1` names inside explanatory prose only |

No further source edit is required for correctness. Phase 1 is about *propagation
measurement*, not re-editing prose.

### 1.3 Probed third-party locations (Phase 2 inputs)

| Tool | Installed | Skills location proven on disk | Persona location |
| --- | --- | --- | --- |
| Cline | `saoudrizwan.claude-dev-4.1.17` | `~/.cline/skills/<name>/SKILL.md` — verified: `~/.cline/skills/agent-rag/SKILL.md` exists | **Not proven.** No persona/agent directory found. Treat as skills-only. |
| Roo | `rooveterinaryinc.roo-cline-3.54.0` | `~/.roo/skills/<name>/SKILL.md` — verified: `~/.roo/skills/agent-rag/SKILL.md` exists | Modes already handled by `modes.py` via `.roomodes`; no separate persona dir proven. Skills-only plus existing mode support. |
| Codex | `~/.codex/` present | `~/.codex/skills/<name>/SKILL.md` — verified: `~/.codex/skills/agent-rag/SKILL.md` exists; `~/.codex/skills/.system/` is reserved | **Not proven.** `config.toml` contains no `skill` key. Treat as skills-only. |

Additional probe results:

- `~/.roo/` also has `rules/` and `commands/`; **out of scope** for this plan.
- VS Code `globalStorage` for Cline and Roo contains only `cache`, `settings`, `state`,
  `tasks`, `checkpoints`, `puppeteer` — **no skills directory**. Do not install there.
- Zero `awino-*` skills currently exist in any of the three tools.
- No `awino` binary is on PATH; `claude` resolves to `%APPDATA%\npm\claude.ps1`.

**Rule for Phase 2: install skills only into the three verified `~/.<tool>/skills/`
paths. Do not invent a persona path for any tool whose persona location is unproven.**

---

## 2. Scope

### 2.1 Files this plan may create or modify

| Path | Phase | Change |
| --- | --- | --- |
| `src/smith/harness.py` | 1, 2 | add drift/refresh functions; add three harness members |
| `src/smith/cli.py` | 1, 2, 3, 4 | add `skills-status`, `install-refresh`, `start`; extend `update` |
| `tests/test_skill_propagation.py` | 1 | new |
| `tests/test_harness_surfaces.py` | 2 | new |
| `tests/test_update_selfheal.py` | 3 | new |
| `tests/test_start_command.py` | 4 | new |
| `docs/install.md` | 2 | document the three new targets and skills-only caveats |
| `docs/user-guide.md` | 4 | make `awino start` the documented first command |
| `specs/cross-harness-update-spec.md` | — | this file; tick checkboxes only |

### 2.2 Files that must not be touched

| Path | Reason |
| --- | --- |
| `src/smith/config_review.py` | pre-existing unrelated modification |
| `src/smith/enforce.py` | ledger authority, out of scope |
| `src/smith/graph.py`, `src/smith/spawn.py` | shipped and independently reviewed |
| `src/smith/updater.py` | snapshot/restore already correct; call it, do not change it |
| `~/.claude/plugins/marketplaces/awino/**` | separately published artifact; refreshed by publish, never edited in place |
| anything outside `.smith/` | `FILE_SCOPE_VIOLATION` |
| any path in another project | out of scope for this plan |

### 2.3 Pre-existing uncommitted work

`git -C .smith status --short` before this plan:

```text
 M memory/lessons.md                    # STALE_SCRIPT_REFERENCE lesson — keep
 M skills/awino-bootstrap/SKILL.md      # CLI-reference fix — keep
 M skills/awino-consult/SKILL.md        # CLI-reference fix — keep
 M skills/awino-self-update/SKILL.md    # CLI-reference fix — keep
 M src/smith/config_review.py           # UNRELATED — do not stage, do not commit
```

---

## 3. Global rules for the executing agent

1. **Phase order is strict.** Do not begin phase N+1 until phase N's exit gate passed
   with pasted command output.
2. **One ledger run per phase.** Open it, satisfy its gates, close it.
3. **TDD.** Write the failing test, paste the failure, then implement. A test that
   passes on first run is not evidence.
4. **Never weaken a test.** No deleted assertions, no added skips. `tests_not_weakened`
   reads the diff.
5. **Windows is the reference platform.** No POSIX-only commands in code or tests.
6. **Three strikes.** Three failures on one gate: stop, report the attempts, escalate.
7. **No guessed paths.** Every third-party path must trace to a probe in §1.3 or a new
   probe whose output is pasted into the run. A guessed path is `UNGROUNDED_CLAIM`.
8. **Hash, not grep,** for any propagation or drift check.
9. **No commit or push** unless the human explicitly asks.
10. **Report honestly.** If a tool cannot be verified, mark it deferred with a reason
    and continue with the rest rather than fabricating support.

All commands run from `.smith`:

```powershell
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run awino doctor --fast
```

---

## 4. Phase 1 — Make propagation measurable (fixes P1)

**Goal:** any installed skill copy that no longer matches source is detected and
repaired, and a human-edited copy is never silently overwritten.

### 4.1 Steps

- [ ] **1.1** Open the run:

  ```powershell
  uv run awino gate open code-change "Hash-verified skill propagation across installed harnesses" --plan specs/cross-harness-update-spec.md --by awino --scope src/smith/harness.py --scope src/smith/cli.py --scope tests/test_skill_propagation.py
  uv run awino gate plan approve --by "<human>" --reason "phase 1 approved"
  ```

- [ ] **1.2** Write `tests/test_skill_propagation.py` with five failing tests:
  1. `test_matching_copy_is_not_reported_even_when_it_mentions_a_retired_script` — copy
     equals source and contains the literal text `registry_build.ps1`; result must be
     **no drift**. This is the regression test for the false positive.
  2. `test_byte_difference_is_reported_as_drift`
  3. `test_missing_installed_skill_is_reported_as_absent`
  4. `test_refresh_repairs_installer_owned_drift_only` — human-modified copy is reported
     and left byte-identical; a backup is written before any overwrite.
  5. `test_refresh_is_idempotent` — second refresh reports zero changes.

- [ ] **1.3** Run the tests. Paste the failure output.

- [ ] **1.4** Implement in `src/smith/harness.py`:
  - `@dataclass(frozen=True) SkillDrift` with fields `skill`, `path`, `state`
    (`current` | `drifted` | `absent` | `human-modified`);
  - `skill_drift(smith_home, target) -> list[SkillDrift]` using
    `ownership.sha256_path` and `ownership.entry`/`ownership.unchanged`;
  - `refresh_skills(smith_home, target, *, overwrite=False) -> list[Action]` that
    re-copies only `drifted` installer-owned entries, calls `ownership.backup` first,
    and re-records the hash via `ownership.record`.

- [ ] **1.5** Add to `src/smith/cli.py`:
  - `awino skills-status [--json]` — per-target counts and per-skill states;
  - `awino install-refresh [--overwrite]` — apply repairs, print one line per action,
    exit nonzero if any action failed.

- [ ] **1.6** Verify on the real machine and paste all three outputs:

  ```powershell
  uv run awino skills-status
  uv run awino install-refresh
  uv run awino skills-status
  ```

### 4.2 Exit gate — all must hold

| Check | Required result |
| --- | --- |
| `uv run pytest tests/test_skill_propagation.py -q` | 5 passed |
| `uv run pytest -q` | full suite passes |
| `uv run ruff check src tests` | clean |
| `uv run ruff format --check src tests` | clean |
| second `skills-status` | zero `drifted` for installer-owned copies |
| human-modified fixture | unchanged bytes, reported not overwritten |
| `uv run awino doctor --fast` | `fail=0` |
| `uv run awino gate close` | exit 0 |

### 4.3 Movement

- **Forward** when drift is hash-measured, refresh is idempotent, and preservation of
  human edits is proven by a passing test.
- **Back** when any check uses substring matching, or a human-edited file is
  overwritten. Restore from the `.awino-backups` path, revert the phase, report.

---

## 5. Phase 2 — Add Cline, Roo, Codex (fixes P2)

**Goal:** `awino install` reaches the three tools that are installed on this machine,
writing only to probe-verified locations.

### 5.1 Steps

- [ ] **2.1** Open a run scoped to `src/smith/harness.py`,
      `tests/test_harness_surfaces.py`, `docs/install.md`. Approve the plan.

- [ ] **2.2** Re-confirm the three skills paths and paste output into the run:

  ```powershell
  Get-ChildItem "$env:USERPROFILE\.cline\skills" -Force | Select-Object -First 3 Name
  Get-ChildItem "$env:USERPROFILE\.roo\skills"   -Force | Select-Object -First 3 Name
  Get-ChildItem "$env:USERPROFILE\.codex\skills" -Force | Select-Object -First 3 Name
  ```

- [ ] **2.3** Write failing tests, one per tool, asserting:
  - `global_root` equals the probed `~/.<tool>` path;
  - `supports_skills` is `True`;
  - skills install to `<root>/skills/<skill-name>/SKILL.md`;
  - persona install is **skipped with an explicit reason**, because no persona path is
    proven;
  - `~/.codex/skills/.system/` is never written.

- [ ] **2.4** Implement `Harness.CLINE`, `Harness.ROO`, `Harness.CODEX` with:
  - `global_root` → `Path.home() / ".cline" | ".roo" | ".codex"`;
  - `project_root` → `project / ".cline" | ".roo" | ".codex"`;
  - `supports_skills` → include all three;
  - persona handling → skills-only; `install` records `SKIPPED  persona location not
    proven for <tool>`.

- [ ] **2.5** Update `docs/install.md`: the three probed paths, the skills-only
      limitation, and the reason (persona location unproven, not unsupported).

- [ ] **2.6** Live-install and paste output:

  ```powershell
  uv run awino install --harness cline --scope global --force
  uv run awino install --harness roo   --scope global --force
  uv run awino install --harness codex --scope global --force
  uv run awino install-status
  uv run awino skills-status
  ```

### 5.2 Exit gate

| Check | Required result |
| --- | --- |
| per-tool tests | pass, each asserting a probed path |
| `uv run pytest -q` | full suite passes |
| lint + format | clean |
| `install-status` | lists Cline, Roo, Codex |
| on-disk check | `~/.cline/skills/awino-consult/SKILL.md` and equivalents exist for Roo and Codex |
| `~/.codex/skills/.system/` | untouched |
| `skills-status` | zero drift after install |
| `doctor --fast` | `fail=0` |
| `gate close` | exit 0 |

### 5.3 Movement

- **Forward** when all three installs land in probe-verified paths and `skills-status`
  reports them current.
- **Back** when any path was assumed, or a tool's real read location cannot be
  confirmed. Mark that tool `DEFERRED` in the run with the failed probe, revert only
  that tool, continue with the others.

---

## 6. Phase 3 — Make `update` self-healing (fixes P3, part of P5)

**Goal:** one `awino update` leaves the machine consistent: project scaffold present,
every already-installed harness current, project-specific state preserved.

### 6.1 Steps

- [ ] **3.1** Open a run scoped to `src/smith/cli.py`,
      `tests/test_update_selfheal.py`. Approve the plan.

- [ ] **3.2** Write failing tests:
  1. `test_update_ensures_project_state` — a project whose `.smith/` lacks `run/` has it
     afterward.
  2. `test_update_refreshes_only_detected_harnesses` — a present harness with drift is
     refreshed; an **absent** harness is still absent afterward.
  3. `test_update_preserves_project_specific_state` — `project.yaml`, `memory/`, and
     `run/` are byte-identical before and after. This is the "git rebase" behavior.
  4. `test_update_is_idempotent` — a second consecutive `update` reports zero changes.
  5. `test_update_prints_single_summary_ending_with_version`.

- [ ] **3.3** Paste the failures.

- [ ] **3.4** Implement inside `update_command`, after the existing version branch, in
      this order: `ensure_state()` → `refresh_skills` for **detected targets only** →
      `doctor --fast` → one summary block ending with the active version. Call
      `updater.snapshot`/`restore`; do not modify `updater.py`.

- [ ] **3.5** Live-verify and paste output:

  ```powershell
  uv run awino update
  uv run awino update
  uv run awino doctor --fast
  ```

### 6.2 Exit gate

| Check | Required result |
| --- | --- |
| five new tests | pass |
| `uv run pytest -q` | full suite passes |
| lint + format | clean |
| second consecutive `update` | reports no further changes |
| absent harness | still absent |
| project `memory/` hash | identical before and after |
| `doctor --fast` | `fail=0` |
| `gate close` | exit 0 |

### 6.3 Movement

- **Forward** when `update` is idempotent and provably non-destructive.
- **Back** when it installs into an unrequested harness or alters project state.
  Restore from the printed `BACKUP` path, revert, report.

---

## 7. Phase 4 — `awino start` (fixes P4, part of P5)

**Goal:** one command produces the entire startup contract, so startup cannot be
partially followed.

### 7.1 Steps

- [ ] **4.1** Open a run scoped to `src/smith/cli.py`,
      `tests/test_start_command.py`, `docs/user-guide.md`. Approve the plan.

- [ ] **4.2** Write failing tests asserting `awino start` prints all eight contract
      fields — `Project`, `Mission confidence`, `Toolchain`, `Tracker`, `Active run`,
      `Pending human decision`, `Next recommended action`, `Route skill` — plus:
  - exits nonzero when health is `REFUSED`;
  - is **read-only** by default (no file writes; proven by comparing a directory
    snapshot before and after);
  - with `--fix`, performs only mechanical repairs and reports the rest;
  - never opens a ledger run;
  - reports the gap instead of crashing when no `.smith/` exists.

- [ ] **4.3** Paste the failures.

- [ ] **4.4** Implement `start` by composing existing internals used by `context`,
      `mission`, `doctor --fast`, `resume`, and `skills --route`. Do not duplicate their
      logic.

- [ ] **4.5** Update `docs/user-guide.md` so `awino start` is the documented first
      command, with `context`/`mission`/`doctor` described as the granular fallbacks.

- [ ] **4.6** Live-verify and paste output:

  ```powershell
  uv run awino start
  ```

### 7.2 Exit gate

| Check | Required result |
| --- | --- |
| new tests | pass |
| `uv run pytest -q` | full suite passes |
| lint + format | clean |
| `awino start` here | prints all eight fields |
| read-only default | proven by test |
| no-`.smith` directory | reports gap, exit code documented, no crash |
| `gate close` | exit 0 |

### 7.3 Movement

- **Forward** when `start` output alone is sufficient to begin work.
- **Back** when it mutates state without `--fix` or duplicates existing logic.

---

## 8. Final acceptance

Run from `.smith` and paste every output:

```powershell
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run awino doctor --fast
uv run awino install-status
uv run awino skills-status
uv run awino start
```

Then in a second project, for example `treads-pipeline`:

```powershell
uv run awino start
uv run awino update
uv run awino skills-status
uv run awino doctor --fast
```

Accepted only when **all** of the following hold:

- [ ] every command above exits zero;
- [ ] `install-status` lists Cline, Roo, and Codex;
- [ ] `skills-status` reports zero drifted installer-owned copies;
- [ ] the second project's `.smith/memory/` is byte-identical before and after;
- [ ] the second project gained no `knowledge/` directory (no `KNOWLEDGE_FORK`);
- [ ] `git -C .smith status --short` shows no modification to `config_review.py`
      beyond its pre-existing state;
- [ ] no file outside `.smith/` was modified;
- [ ] a dated lesson was appended to `memory/lessons.md` recording what was learned.

---

## 9. Explicitly out of scope

- Autonomous scheduling, cron, or background triggers. A.W.I.N.O. remains
  session-reactive; claiming otherwise is `UNGROUNDED_CAPABILITY`.
- Graph review on runners other than Claude.
- Republishing the marketplace plugin copy.
- `~/.roo/rules/` and `~/.roo/commands/` integration.
- Any change to `enforce.py`, `graph.py`, `spawn.py`, `updater.py`, or
  `config_review.py`.
- Committing or pushing without an explicit request.

## 10. Approval

- [ ] Human approved this plan.
- [ ] Approver: ____________________  Date: ____________

Resume command if approval is deferred:

```powershell
uv run awino gate plan status
```
