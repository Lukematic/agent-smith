---
name: awino-deepplan
description: Deep planning before any code. Use when the human wants to plan in depth, be grilled, brainstorm options, or break a problem into steps before executing. Frames the goal, grills one question at a time with a recommendation each, reads the code, slices the work with a real check per slice, red-teams it, then opens a gated run bound to the approved plan.
---

# A.W.I.N.O. Deep Plan

Plan like a senior engineer who has been burned before: understand the goal,
interrogate it, look at the code, go wide on options, then narrow to small
steps that each prove themselves. Nothing gets built until the human approves
the plan, and then the harness holds the work to it.

You are a planning partner, not a form. Be conversational and direct. Disagree
when you should: a weak idea gets specific, kind pushback. Label what you could
not check `[inferred]`.

## The process

`awino deepplan` always prints where the session stands, the next stage, the
file to write, and the prompt for it.

```bash
awino deepplan start "<short title>" --brief <file>   # or write into brief.md
awino deepplan                                         # status + next stage prompt
awino deepplan record <stage>                          # checks it; prints what is missing
```

1. **frame**: the goal in one sentence, why, `Done when` criteria numbered
   `C1:`, `C2:`... that a person or command can check, constraints, out of scope.
2. **grill** (the heart of it; see below).
3. **directions** (checkpoint): at least five variations, each through a named
   lens; two or three real directions with value, hardest part, and the hidden
   assumption; one recommendation. Stop and ask which direction.
4. **recon**: read the code that matters and cite at least three real
   `path:line` references; what to reuse; the test command that runs today;
   the risks.
5. **slices**: two to twelve thin vertical slices in dependency order. Each
   names its files (existing paths, or marked `(new)`), what it truly depends
   on, a `Verify:` command that runs here, what done looks like, and which
   criteria it covers. Every criterion is covered. Scope: will change, will not.
6. **redteam**: a premortem (three ways it fails, each with the warning sign and
   the mitigation), the strongest objections with answers, and the rollback.
   Hand this to a separate reviewer subagent when you can; authors grade their
   own plans too kindly.
7. **plan**: `awino deepplan compile` writes `plan.md` in the format the
   harness executes and checks. Show it to the human. Only when they approve:
   `awino deepplan go --by "<name>" --note "<their words>"`. That opens a gated
   run bound to the plan's exact bytes and file scope.

### The grill

The grill is a conversation the engine records, one question at a time:

- **Read before you ask.** Anything the code, docs, or history can answer, answer
  it yourself: `awino deepplan learn "<fact>" --evidence path/to/file.py:42`.
  The evidence must be a real line in the project.
- **One question, with your recommendation and why it matters:**
  `awino deepplan ask "<question>" --recommend "<your answer>" --why "<what it changes>"`.
  Offer two or three concrete options when that helps. Then stop and wait.
  The engine refuses a second question while one is open.
- **Record their words:** `awino deepplan answer "<their words>"`, or `--accept`
  only when they actually take your recommendation. Never answer your own question.
- **Order:** ask what unblocks the most other decisions first, then walk down
  the tree. Skip anything whose answer would not change the plan.
- **Cover what bites later:** purpose and users; v1 versus later; data and state;
  edge cases and failure modes; security and privacy; performance; compatibility
  and migration of what exists; how we prove it works; rollout and rollback.
- **Parking:** `awino deepplan defer --reason "<why it can wait>"` when the human
  wants to move on.
- **Finish:** `awino deepplan grill-done`. It refuses while a question is open
  and asks for at least three human answers, unless `--enough "<why>"`.

### Executing the approved plan

Work one phase at a time, in order. Run the phase's success command yourself
until it passes, then verify it with `awino deepplan done <n>`: it runs that
command from the approved plan as gate evidence (failures count toward the
run's three strikes) and ticks the phase off in `progress.md` only if it
passes, so a fresh session can resume from `awino deepplan`. `awino gate close`
refuses while any phase is unverified, and `awino gate open` refuses a Deep
Plan's `plan.md`: the run is opened only by `go`. Never tick by hand
and never edit `plan.md`: the run is bound to its exact bytes. Anything outside
the plan's scope goes under Follow-ups in `progress.md`, never into this run. Finish with
`awino gate check --diff-base HEAD` and `awino gate close`. If reality breaks
the plan, stop and say what changed. Patch the stage it came from (usually
`slices.md`), `record` it, `compile`, and get the new plan approved with `go`;
the harness refuses evidence once the plan's bytes change.

## Failure Modes

| Failure | What it looks like | Fix |
| --- | --- | --- |
| Interview by spreadsheet | Five questions in one message | One question, a recommendation, then wait |
| Asking what the code knows | "Which database do you use?" in a repo with `models.py` | `learn` it with file:line evidence |
| Answering for the human | `--accept` without them saying so | Only their words; defer if they want to move on |
| Horizontal slices | "Slice 1: all models. Slice 2: all routes" | Thin end-to-end slices that each work and show it |
| Prose as verification | `Verify: tests pass` | A backticked command that runs on this machine |
| Plan as wish | Paths that do not exist | Real paths, or marked `(new)` |
| Rubber-stamp red team | One soft risk | Three failure modes with warning signs, from a separate reviewer |
| Silent re-plan | Editing plan.md mid-run | Stop, re-record the stage, compile, get approval again |

Grounding: chapters/7-patterns/1-plan-build-review.md,
chapters/9-mental-models/3-specs-as-source-code.md,
chapters/7-patterns/6-human-in-the-loop.md.

## Completion

Planning is complete when `awino deepplan go` printed `PLAN_APPROVED` with the
plan's sha256: every stage recorded, the directions checkpoint confirmed in the
human's words, and the plan approved by name. Paste the output of
`awino deepplan` and the plan path before saying planning is done. Execution
is complete only when every phase shows `[x]` in `awino deepplan` and
`awino gate close` exits zero; until then report which phases are verified and
which are not.
