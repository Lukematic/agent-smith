# `src/smith`

The deterministic half of A.W.I.N.O. Anything a script does reliably does not
belong in a prompt, so it lives here instead.

## Contents

| Module | Responsibility |
| --- | --- |
| `cli/` | Every command, via typer, split by what it serves (see `cli/README.md`). The only package that prints. |
| `paths.py` | A.W.I.N.O. home vs project resolution, plus the wheel-bundle fallback. |
| `knowledge.py` | Fetch, cache, provenance, drift, routing, and the file budget. |
| `enforce.py` | The gate ledger. Completion is computed from recorded exit codes. |
| `models.py` | Mental models as decision functions: ladder, constraint, verifier, pit. |
| `mission.py` | Reads what a project is for, and marks weak inferences as guesses. |
| `health.py` | Project gates: toolchain, docs, structure, memory, skills, seeds. |
| `fix.py` | Safe auto-repair. Judgement calls are reported, never guessed. |
| `validate.py` | Artifact validation with PASS, WARN, SKIP, and FAIL. |
| `tidy.py` | Clutter detection and reversible archiving. |
| `toolchain.py` | Discovers what a project actually uses instead of dictating. |
| `harness.py` | Installs the persona per harness, rebuilding frontmatter for each. |
| `modes.py` | Kilo and Roo selectable modes, split by tool restriction. |
| `seeds.py` | Optional integration with the seeds issue tracker. |
| `fair.py` | Enforces that every meaningful directory is documented. |
| `machine.py` | Node enum + fixed edge table; the persisted program counter (`machine.json`). |
| `stepper.py` | One action per node; `step` (one square) and `run` (walk to the next human decision). |
| `ladder.py` | `choose()`: rung + verifier strength + scope -> direct/floor/ralph/graph/delegate. |
| `dispatch.py` | Deterministic routing (`decide`), preflight, and portable worker/reviewer floors. |
| `provision.py` | Plan/apply environment repairs with consent; `discover_verification`. |
| `heilmeier.py` | Eight-question living mission document; exams become verify commands. |
| `playbook.py` | session-start / task-close / session-end orders; intent memory. |
| `recall.py` | Token-overlap retrieval of relevant lessons. |
| `stance.py` | Conversational stance catalog, detection, and per-project default. |
| `guard.py` | Pre-push canonical-root and remote identity check (`awino push`). |
| `drift.py` | Symbol drift: identifiers removed from code but surviving in prose. |
| `exam.py` | Live FIRES/SILENT probes of every claimed capability. |
| `auto.py` | Bounded Seed driver over floors. |
| `spawn.py` | Assignments, roles, fresh-subprocess runners, independent `verify`. |
| `graph.py`, `loop.py` | Legacy executors superseded by floors; deletion tracked in Seed e860. |
| `skill_catalog.py` | Skill discovery and lexical routing scores. |
| `session_log.py`, `session_state.py` | Per-project session events (redacted, locked) and session binding. |
| `onboarding.py`, `project_guard.py`, `project_template.py` | Mission handshake, pre-tool guard, project scaffold. |
| `capability.py`, `completion_review.py`, `config_review.py`, `debugging.py`, `doc_review.py`, `healing.py`, `ownership.py`, `updater.py`, `watch.py` | Probes, independent review provenance, config audit, debug ledger, doc rubric, self-healing, install ownership, self-update, upstream watch. |
## Usage

Installed as the `awino` console script by `pyproject.toml`.

```bash
awino --help          # every command
awino context         # what A.W.I.N.O. thinks home and project are
awino doctor          # project health, refuses on failure
awino gate open ...   # start a gated run
```

Inside A.W.I.N.O.'s own repository, `just` wraps the common paths: `just check` runs
lint, tests, and validation as a single gate.

## Format

Python 3.12, fully typed, formatted and linted by ruff. Configuration lives in
`pyproject.toml` under `[tool.ruff]`.

Three conventions worth knowing before editing:

- **Only `cli/` prints.** Library modules return data structures so they stay
  testable. A module that prints cannot be asserted against.
- **Every verdict carries its reasoning.** `Result`, `Repair`, `Tool`, and the
  model verdicts all pair an outcome with the reason that produced it, so a plan
  can be argued with rather than merely obeyed.
- **Detection over assumption.** `harness.py` and `toolchain.py` read what is
  actually installed. Every path in them was verified against a real machine,
  because a wrong path fails silently and looks like success.

## Stability

Edit freely, but `just check` must pass before the work is complete:

```bash
just check    # ruff check, ruff format --check, pytest, awino validate, tidy-check
```

The test suite exists to prove the gates actually block, not merely that the code
runs. Never weaken a test to make it pass: the `tests_not_weakened` gate reads the
diff for deleted assertions and added skip markers.

`enforce.py`, `health.py`, and `harness.py` are load-bearing. A change that makes a
gate easier to satisfy, or a path easier to guess, needs a test proving it still
refuses the case it was built to catch.

---

This file is hand-written and carries no generated marker, so `awino fix` will
not overwrite it.
