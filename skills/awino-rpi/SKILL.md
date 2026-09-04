---
name: awino-rpi
description: Research then Plan then Implement for complex multi-file changes. Use for refactors, restructures, splitting a module into a package, migrations, feature additions, large upgrades, and documentation overhauls where jumping to code would drift
---

# A.W.I.N.O. RPI

Most agent failures on large changes are not capability failures — they are
**context failures**. The work spans more than can be safely held at once, so the
agent drifts. RPI trades speed for correctness by splitting understanding,
decision-making, and execution into separate sessions.

**One goal per session.** This is the load-bearing constraint. Research, plan, and
implement must not share a context window.

## When NOT to use this

A 1–2 file well-understood change. RPI on trivial work is `CEREMONY_OVERKILL` —
it is deliberately slow. Use it for: refactors, migrations, feature additions,
large upgrades, incident cleanup, docs overhauls.

## Output locations

```
thoughts/
  research/YYYY-MM-DD-HHmm-<topic>.md
  plans/YYYY-MM-DD-HHmm-<description>.md
```

Committed, not gitignored. These are the artifacts that make the work reviewable.

---

## Phase 1 — Research

**Job: document what exists. Nothing else.**

Forbidden in this phase: suggesting changes, critiquing code, proposing a design,
writing a plan. Violating this is `RESEARCH_CONTAMINATION` — an opinion recorded
as fact poisons the plan built on it.

Spawn three parallel subagents, each read-only (`Read, Grep, Glob`):

| Subagent | Job |
| --- | --- |
| `find_files` | locate every file relevant to the topic; return paths, no analysis |
| `analyze_code` | read those files fully; document how they actually work with file:line refs |
| `find_patterns` | find similar features/conventions elsewhere in the repo to imitate |

They run independently and report back. Do not orchestrate their internals.

Write `thoughts/research/YYYY-MM-DD-HHmm-<topic>.md`:

```markdown
# Research: <topic>

## Metadata
- date, branch, commit sha
- scope: what was and was not examined

## Where it lives
| Concern | File | Lines |
|---|---|---|

## How it works
Prose + file:line references. Descriptive only.

## Flow
Entry point through to effect.

## Existing conventions to imitate
Patterns found elsewhere in this repo.

## Open questions
Things the code does not answer. Do not guess.
```

**Missing-input protocol.** If research needs an input, category, or capability
that appears absent (an empty output directory, a category with no prior run,
a data file that does not exist), the required next step is **search for the
generator**, not annotate the absence:

1. Grep/glob for the script, CLI flag, or tool that would produce it (a parameter
   like `--technology-slug` on an existing scanner, a generator invoked for one
   category that plausibly accepts others).
2. Attempt `--help` or a dry run against that generator.
3. Only after confirming no generator exists — not merely that no prior output
   exists — may the input be reported as a real gap in "Open questions."

Absence of output for input X is evidence the pipeline has not been run on X,
not evidence it cannot run on X. Concluding infeasibility from an output
directory alone is `RESEARCH_CONTAMINATION` by omission: a false negative
recorded as fact.

**Then stop.** The human reviews research before planning. Course-correcting here
is cheap; correcting after implementation is not. If the topic was scoped wrong,
rerun research with a sharper topic — that is the system working, not a failure.

---

## Phase 2 — Plan (new session)

Read the research document first. Then, in order:

1. **Ask clarifying questions.** Full removal or deprecation? How should config
   cleanup behave? Where do the tests live? Do not guess where a question exists.
2. **Present design options.** Where several approaches are reasonable, lay them
   out with tradeoffs and let the human choose. Do not silently pick.
3. **Produce a phased plan.**

Write `thoughts/plans/YYYY-MM-DD-HHmm-<description>.md`:

```markdown
# Plan: <description>

## Source research
thoughts/research/...

## Decisions made
| Question | Answer | Rationale |

## Phase 1 — <name>
- [ ] Exact file path — exact change
- [ ] ...
**Automated success criteria:** <command that must pass>
**Manual verification:** <what a human checks>

## Phase 2 — <name>
...

## Out of scope
Explicit non-goals.
```

Requirements:
- **Explicit enough that someone else could execute it.** Implementation runs in
  a fresh session with no memory of this one. A plan that assumes context you hold
  now will fail then.
- Exact file paths. Code snippets for non-obvious edits.
- Every phase carries a real command as its success criterion.
- Checkboxes — implementation updates them in place, which is what lets a
  compacted or restarted session resume.

**Then stop.** Human reviews. If something is wrong, iterate the plan surgically:
research only what changed, patch the plan. Do not start over.

---

## Phase 3 — Implement (new session)

Read the plan **completely** before touching anything.

Then per phase, in order:
1. Execute the phase's items.
2. Run its automated success criterion. Paste the output.
3. Tick the checkboxes **in the plan file** as you go.
4. Only then move to the next phase.

Implementation should feel **mechanical and boring**. If it feels creative,
something upstream is missing — stop and iterate the plan instead of improvising.
Improvising here is `PLAN_DRIFT`.

Updating checkboxes in the file is not bookkeeping — it is the recovery
mechanism. When context fills and compacts, the plan file is how the next
session knows where it is.

### Optional: Ralph the implement phase

If a phase has a clean pass/fail gate and may need several attempts, hand it to
`awino:awino-ralph` instead of retrying inline. Fresh context per attempt
beats accumulating failed attempts in one window.

---

## Reporting

```markdown
## RPI Complete

| Phase | Duration | Output |
|---|---|---|
| Research | Nm | thoughts/research/... |
| Plan | Nm | thoughts/plans/... |
| Implement | Nm | N files, N phases |

### Verification
<pasted command output per phase>

### Unverified
<anything requiring manual checks>
```

## Failure Modes

| Mode | Definition |
| --- | --- |
| `RESEARCH_CONTAMINATION` | opinions or fixes recorded during research |
| `MISSING_INPUT_ANNOTATED_INSTEAD_OF_GENERATED` | absence of prior output reported as infeasibility without first searching for and running the generator |
| `PLAN_WITHOUT_RESEARCH` | planning on assumptions instead of documented reality |
| `UNDERSPECIFIED_PLAN` | plan assumes context the implementer will not have |
| `PLAN_DRIFT` | improvising during implement instead of iterating the plan |
| `PHASE_SKIP` | moving on before the success criterion passed |
| `SINGLE_SESSION_RPI` | all three phases in one context window |
| `CEREMONY_OVERKILL` | RPI on a change that did not need it |
| `SILENT_CHECKBOX` | plan file not updated, so recovery is impossible |

## Completion

Done when: research reviewed, plan approved, every phase's success criterion has
pasted passing output, and every checkbox in the plan is ticked or explicitly
deferred with a reason.

Grounding: chapters/9-mental-models/3-specs-as-source-code.md,
chapters/4-context/2-context-strategies.md, chapters/7-patterns/1-plan-build-review.md
