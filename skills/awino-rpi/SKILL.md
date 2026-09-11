---
name: awino-rpi
description: Research, pair-planning, Plan, then Implement for complex multi-file changes. Use for refactors, restructures, splitting a module into a package, migrations, feature additions, large upgrades, and documentation overhauls where jumping to code would drift
---

# A.W.I.N.O. RPI

Most agent failures on large changes are not capability failures — they are
**context failures**. The work spans more than can be safely held at once, so the
agent drifts. RPI trades speed for correctness by splitting understanding,
pairing, decision-making, and execution into separate sessions.

**One goal per session.** This is the load-bearing constraint. Research,
pair-planning, plan, and implement must not share a context window.

## When NOT to use this

A 1–2 file well-understood change. RPI on trivial work is `CEREMONY_OVERKILL` —
it is deliberately slow. Use it for: refactors, migrations, feature additions,
large upgrades, incident cleanup, docs overhauls.

## Output locations

```
thoughts/
  research/YYYY-MM-DD-HHmm-<topic>.md
  pairing/YYYY-MM-DD-HHmm-<topic>.md
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

## Problem breakdown
The problem decomposed into its distinct sub-problems. First principles:
state the problem in pieces before touching any angle on the whole.

## Assumptions challenged
Each assumption the work started with, named explicitly, and what the code
actually showed about it. An assumption left unnamed is not an assumption
challenged — the driver checks that the section names them.

## Angles considered
At least two different angles on the problem (other repos' conventions, the
inverse framing, what the code does not do). When stuck, reframe the problem
from a different angle; this section is where the reframes are recorded.

## Open questions
Things the code does not answer. Do not guess.
```

The three first-principles sections are required **before** any proposed
solution: the document breaks the problem down, challenges its assumptions,
and records the angles considered first. The driver rejects an artifact that
jumps to a solution (a "Solution"/"Proposal" heading) without them, naming
the missing part.

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

## Phase 2 — Pair-planning

**Job: plan together and adjust. The agent decomposes and proposes; the human decides; the ledger captures why.**

Read the research document first. Then write `thoughts/pairing/YYYY-MM-DD-HHmm-<topic>.md`:

```markdown
# Pairing brief: <topic>

## Sub-problems
The distinct pieces of the work, one per line.

## Candidate approaches
Two or three options, each as a `###` subheading, each with its trade-offs
spelled out. Mark trade-offs explicitly — the word "trade-off" (or "pro:" /
"con:") must appear under every approach. Mark the level of effort explicitly
too — an `effort: <estimate>` line under every approach, so the human can
compare what each costs. The driver checks for the markers, not the insight.

**Honda first:** exactly one approach is marked as the `default
recommendation` — the Honda, the one that delivers exactly what was asked,
no more. The other approaches stay labeled recommendations: options with
effort estimates, never the plan. Never build the Bugatti unasked.

### A: <name>
What it is. trade-off: what it costs and what it buys.
effort: <rough estimate, e.g. hours or days>

### B: <name>
**Default recommendation.** What it is. trade-off: what it costs and what it
buys.
effort: <rough estimate>

## Questions
Explicit questions needing human input, one per line in `Qn:` format:

Q1: which approach — A, B, or something else?
Q2: who approves the plan?
```

**Then stop.** The driver prints the questions verbatim. The human answers each
with `awino loop answer --question Q1 --answer "..."`, or declares a default
with `awino loop default --question Q2 --reason "..."`. The loop does not
advance to planning until every question has an answer or a declared default:
an unanswered question is a decision the plan would have to guess, and guessing
is what this phase exists to prevent. Re-answering overwrites; the ledger keeps
both answers.

---

## Phase 3 — Plan (new session)

Read the research document (Phase 1) and the pairing brief (Phase 2) first — the recorded human
decisions are injected into your prompt as "Human decisions so far", and your
plan's `## Decisions` section must trace every decision to a pairing question
(`Q1`) or mark it `default:` with a reason. **Honda first:** a decision that
chooses a candidate approach must also say whether it followed the default
recommendation or overrode it, with a reason — e.g. "chose B: followed the
default recommendation because the asked-for change needs nothing from A's
extras", or "chose A: overrode the default recommendation because <reason>".
Then, in order:

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

One row per pairing question, referencing its `Qn:` id — the driver validates
the trace, so a decision that answers no asked question fails validation.
When a row chooses a candidate approach, its rationale must say whether it
followed or overrode the default recommendation, and why.

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

## Phase 4 — Implement (new session)

Read the plan (Phase 3) **completely** before touching anything.

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
| `UNPAIRED_PLAN` | planning with pairing questions unanswered (the driver refuses the advance) |
| `UNDERSPECIFIED_PLAN` | plan assumes context the implementer will not have |
| `PLAN_DRIFT` | improvising during implement instead of iterating the plan |
| `PHASE_SKIP` | moving on before the success criterion passed |
| `SINGLE_SESSION_RPI` | all three phases in one context window |
| `CEREMONY_OVERKILL` | RPI on a change that did not need it |
| `SILENT_CHECKBOX` | plan file not updated, so recovery is impossible |

## Completion

Done when: research reviewed, pairing questions answered, plan approved, every phase's success criterion has
pasted passing output, and every checkbox in the plan is ticked or explicitly
deferred with a reason.

Grounding: chapters/9-mental-models/3-specs-as-source-code.md,
chapters/4-context/2-context-strategies.md, chapters/7-patterns/1-plan-build-review.md
