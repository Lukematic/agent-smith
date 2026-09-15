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
