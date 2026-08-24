# Enforcement

How A.W.I.N.O. stops agents from ignoring instructions.

---

## The problem, stated precisely

You wrote `verification-before-completion` into a skill. The agent read it, agreed
with it, and then three tool calls later said "done, tests pass" without running
them.

This is not disobedience and it is not a model defect. It is **instruction
dilution**: every line in a prompt competes with every other line for influence,
and a long prompt with many rules weakens each rule. A probabilistic system
following an instruction on every turn in every context is not something you can
assume.

The reflex fix is to state it louder: bold text, capital letters, "CRITICAL", "you
MUST". That is a `PROMPT_PATCH`, and it fails for the same structural reason the
original failed.

## The fix, stated precisely

**Move the obligation out of the prompt and into a mechanism the agent cannot
route around.**

```
Before:  agent decides it is done, then says so
After:   agent asks a gate whether it may be done, and the gate computes an answer
```

The agent names the command. **A.W.I.N.O. runs it and observes the exit code.** That
asymmetry is the whole design. A model can write "all tests passed"; it cannot
produce a zero exit code from a failing suite.

---

## Anatomy

### 1. A run has a contract it did not choose

```bash
awino gate open code-change "add retry to the fetch client" --scope src/smith/knowledge.py
```

```
gates required before close:
  [ ] planned
  [ ] tested
  [ ] linted
  [ ] tests_not_weakened
  [ ] scope_respected
```

The task class determines the gates, from a table in `src/smith/enforce.py`. The
agent cannot negotiate them because it never selects them. The table is data, so
it can be diffed, reviewed, and unit tested, unlike a paragraph of prose.

| Task class | Gates |
| --- | --- |
| `question` | none |
| `research` | researched |
| `code-change` | planned, tested, linted, tests_not_weakened, scope_respected |
| `bugfix` | researched, tested, linted, tests_not_weakened, lesson_recorded |
| `refactor` | code-change gates plus reviewed |
| `authoring` | planned, validated, lesson_recorded |

`bugfix` requires `researched` on purpose: a bugfix that never reproduced the bug
did not diagnose it, it guessed.

### 2. Evidence is executed, not described

```bash
awino gate record tested --cmd "uv run pytest"
```

```
PASS  tested  exit=0  1546ms  attempt=2
command: uv run pytest
  | 71 passed in 3.35s
```

Recorded per attempt into `.smith/run/<id>/evidence.jsonl`: the command, the real
exit code, a hash of the output, the first 4000 characters, and the duration.

Gates with no command use `--attest`, and attestations are reported separately so
weak proof stays visible:

```bash
awino gate record planned --attest "docs/plans/2026-08-21-retry.md"
```

```
NOTE  attested rather than executed: planned, tests_not_weakened
```

### 3. Two checks that do not trust the agent at all

```bash
awino gate check --diff-base HEAD
```

**Test weakening.** Parses the diff for deleted assertions, added skip markers,
and removed test functions, in test files only. "Never modify a test to make it
pass" is the most-ignored instruction in agent work because obeying it is
invisible and breaking it is fast. This reads the diff instead of asking.

**Scope violation.** Reconciles `git diff --name-only` against the declared
`--scope`. An agent that wandered into files it never claimed gets caught by
arithmetic, not by conscience.

### 4. Closing is a verdict, not an announcement

```bash
awino gate close
```

```
REFUSED  GATE_FAILING tested recorded a nonzero exit code.
You may not report this work as complete.
```

`adjudicate()` reads the ledger and computes whether the run may close. There is
no code path where the agent's opinion contributes.

### 5. Three strikes stops the spiral

A gate that has failed three times returns `THREE_STRIKES` and refuses further
retries. The instruction "do not retry more than three times" requires the agent
to count its own failures across a context that may have compacted. The counter
does not forget.

---

## Why each instruction became a mechanism

| Instruction that decayed | Mechanism that replaced it |
| --- | --- |
| always run the tests before saying done | exit code recorded by A.W.I.N.O. |
| never weaken a test to make it pass | diff parsed for deleted asserts and added skips |
| stay inside your assigned files | changed files reconciled against declared scope |
| stop after three attempts | attempt counter blocks the gate |
| load the relevant skill | `awino gate skill <name>` makes usage auditable |
| keep the repo clean | `awino doctor` fails on stray files and unlinked docs |
| keep the environment working | `awino doctor` runs `uv sync --frozen` |
| record what you learned | `lesson_recorded` gate, format-checked by doctor |

---

## The project gates the project

`awino doctor` applies the same philosophy to the repository:

```bash
awino doctor --fast
```

```
  OK    uv_env         uv present and environment synced against the lockfile
  OK    justfile       20 recipes, all required ones present
  FAIL  docs           README links to missing file(s): docs/enforcement.md
  FAIL  artifacts      1 of 11 invalid
REFUSED  2 gate(s) failing: docs, artifacts
```

Those two failures are real, from the run that produced this document. The doctor
caught a broken README link and a skill whose Completion section lacked
verification wording, in work that had just been declared finished. That is the
argument for the whole approach in one output block.

Checks: uv synced, python pinned, justfile complete, pyproject configures ruff and
pytest, no stray root files, no duplicated content, every doc linked from the
README, every skill valid, exactly one worklist, lessons well-formed, registry
parseable, plugin installed.

Wire the verdict into a run:

```bash
awino doctor --record
```

---

## Honest limits

- **Attestations are trust.** `--attest` records a claim with no execution behind
  it. They are labelled, and gates that matter should use `--cmd`.
- **A gate is only as good as its command.** `--cmd "echo ok"` satisfies `tested`.
  The ledger records that the command was `echo ok`, so review catches it, but the
  mechanism does not judge command quality.
- **Scope and weakening checks need git.** Without `--diff-base` they skip rather
  than fail, and skipping is visible in the output.
- **This does not make an agent smart.** It makes an agent unable to claim
  finished work it has not done. Those are different problems.

---

## Reading order

1. `src/smith/enforce.py` for the contract table and `adjudicate()`
2. `tests/test_enforce.py` for the refusal cases, which is where the guarantees live
3. `src/smith/health.py` for the project-level gates

Grounding: chapters/6-harnesses/5-harness-engineering.md,
chapters/11-agent-readiness/2-failure-modes.md, chapters/7-patterns/1-plan-build-review.md
