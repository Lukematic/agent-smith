# `src/smith`

The deterministic half of Agent Smith. Anything a script does reliably does not
belong in a prompt, so it lives here instead.

## Contents

| Module | Responsibility |
| --- | --- |
| `cli.py` | Every command, via typer. The only module that prints. |
| `paths.py` | Smith home vs project resolution, plus the wheel-bundle fallback. |
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

## Usage

Installed as the `smith` console script by `pyproject.toml`.

```bash
smith --help          # every command
smith context         # what Smith thinks home and project are
smith doctor          # project health, refuses on failure
smith gate open ...   # start a gated run
```

Inside Smith's own repository, `just` wraps the common paths: `just check` runs
lint, tests, and validation as a single gate.

## Format

Python 3.12, fully typed, formatted and linted by ruff. Configuration lives in
`pyproject.toml` under `[tool.ruff]`.

Three conventions worth knowing before editing:

- **Only `cli.py` prints.** Library modules return data structures so they stay
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
just check    # ruff check, ruff format --check, pytest, smith validate, tidy-check
```

The test suite exists to prove the gates actually block, not merely that the code
runs. Never weaken a test to make it pass: the `tests_not_weakened` gate reads the
diff for deleted assertions and added skip markers.

`enforce.py`, `health.py`, and `harness.py` are load-bearing. A change that makes a
gate easier to satisfy, or a path easier to guess, needs a test proving it still
refuses the case it was built to catch.

---

This file is hand-written and carries no `smith:generated` marker, so
`smith fix` will not overwrite it.
