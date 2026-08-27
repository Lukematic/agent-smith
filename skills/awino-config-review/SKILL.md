---
name: awino-config-review
description: Audit project configuration such as pyproject.toml, uv.lock, Makefile/Justfile, CI workflows, harness config, .env files, and kilo.json permissions for drift, conflicts, and unsafe defaults. Use for config, setup, CI, or permission audit requests. Read-only, never mutates files.
allowed-tools: Read, Glob, Grep, Bash
---

# A.W.I.N.O. Config Review

Use this skill whenever someone asks to audit, review, or sanity-check a
project's configuration surface: pyproject/uv setup, task runners, CI
pipelines, editor/harness config, `.env` handling, README-vs-command drift, or
`kilo.json` permission blocks. It is the deterministic counterpart to
skimming these files by eye: every finding is cited, nothing is guessed, and
nothing is changed.

**This skill never mutates project files.** It only reads. If a finding
implies a fix, the fix is left to the human or a separate authoring skill.

## Instructions

### Step 1: Run the audit

```bash
awino config-review --json
```

Human-readable output (no `--json`) prints one line per finding with
severity, category, file:line citation, message, and — where a finding can be
independently verified — a suggested command.

### Step 2: Read the summary, not just the count

The exit code is nonzero only when at least one `error`-severity finding
exists. `warn` and `info` findings are visible but do not block; they are
signal, not a gate.

### Step 3: Respect the project's existing tooling choice

If the project uses Just, do not propose adopting Make, and vice versa. The
one cross-runner check this skill performs is a genuine conflict: the same
task name defined with different bodies in both a Makefile and a Justfile.
That is a real hazard independent of preference, because whichever file a
human happens to run first silently wins.

### Step 4: Verify, do not assume

Every finding that references a specific command (`suggested_command`) is
designed to be run directly by a human to confirm the finding independently.
Paste its output before acting on the finding.

## What it checks

| Area | Detector |
| --- | --- |
| `pyproject.toml` | parses, has `requires-python`, has at least one `[tool.*]` section |
| `uv.lock` | present when `[tool.uv]` is declared; not older than `pyproject.toml` |
| `Makefile` / `Justfile` | duplicate target names within one file; the same task name with a different body across both files |
| `.github/workflows/*.yml` | missing lint step, missing test step, hardcoded-secret-shaped lines |
| `AGENTS.md` / `CLAUDE.md`, `.kilo/agent`, `.kilo/modes`, `.claude/agents` | conflicting file pairs and duplicate persona/mode names with different content |
| `.env` / `.env.example` | presence, and whether `.env` is listed in `.gitignore`; **values are never read** |
| README vs task runner | task-runner recipes not mentioned anywhere in `README.md` |
| `kilo.json` | parses; flags overly broad `"*": "allow"` permission patterns |

## Failure Modes

| Mode | Guard |
| --- | --- |
| `VAGUE_FINDING` | every finding cites `file:line` or file existence, never a general impression |
| `SILENT_MUTATION` | this skill has no write path; findings-only, always |
| `TOOLING_PREFERENCE_IMPOSED` | Just vs Make is never flagged as wrong; only genuine cross-file conflicts are |
| `SECRET_VALUE_LOGGED` | `.env` values are never read or echoed, only presence and gitignore status |

## Completion

Done when `awino config-review --json` has run against the target project and
every reported finding has been read and, where a `suggested_command` exists,
independently reproduced before acting on it.

Grounding: `chapters/6-harnesses/6-security-permissions-trust.md`.
