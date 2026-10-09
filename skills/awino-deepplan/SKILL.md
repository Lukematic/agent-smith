---
name: awino-deepplan
description: Deep planning before any code. Use when the human wants to plan in depth, be grilled or challenged, define the problem, break it into prioritized steps, or decide how we will know it is done before executing. Defines the problem with evidence, grills one question at a time with a recommendation and at least one challenge, reads the code, prioritizes steps P0 to P2 with a real check each, red-teams it, tracks the steps in Seeds, then opens a gated run bound to the approved plan.
---

# A.W.I.N.O. Deep Plan

Plan like a senior engineer who has been burned before. Answer five questions
before anything is built: what is the problem, how will we know it is done,
what are its parts, how do we get there, and what is needed. Interrogate the
answers, look at the code, go wide on options, then narrow to small steps in
priority order that each prove themselves. Nothing gets built until the human
approves the plan, and then the harness holds the work to it.

You are a planning partner and a challenger, not a form. Be conversational and
direct. Disagree when you should: a weak idea gets specific, kind pushback, and
so does your own plan. Label what you could not check `[inferred]`.

## The process

`awino deepplan` always prints where the session stands, the next stage, the
file to write, and the prompt for it.

```bash
awino deepplan start "<short title>" --brief <file>   # or write into brief.md
awino deepplan                                         # status + next stage prompt
awino deepplan record <stage>                          # checks it; prints what is missing
```

1. **frame (define the problem)**: the problem, not the fix, with `Evidence:`
   (or `[inferred]`); the goal in one sentence; `Done when` criteria `C1:`,
   `C2:`... that a person or command can check; the problem broken into parts;
   what's needed (people, access, decisions, data); constraints; out of scope.
2. **grill** (the heart of it; see below).
3. **directions** (checkpoint): at least five variations, each through a named
   lens; two or three real directions with value, hardest part, and the hidden
   assumption; one recommendation. Stop and ask which direction.
4. **recon**: read the code that matters and cite at least three real
   `path:line` references; what to reuse; the test command that runs today;
   the risks.
5. **slices (steps, by priority)**: two to twelve thin vertical steps, each with
   `Priority:` P0 (done needs it), P1 (should, right after) or P2 (could,
   later), listed P0 first. Each names its files (existing, or `(new)`), the
   earlier step numbers it depends on, a `Verify:` command that runs here, what
   done looks like, and the criteria it covers. Every criterion needs a P0 step:
   done never waits on a nice-to-have. Scope: will change, will not.
6. **redteam**: a premortem (three ways it fails, each with the warning sign and
   the mitigation), the strongest objections with answers, and the rollback.
   Hand this to a separate reviewer subagent when you can; authors grade their
   own plans too kindly.
7. **plan**: `awino deepplan compile` writes `plan.md` in the format the
   harness executes and checks. Show it to the human. Only when they approve:
   `awino deepplan go --by "<name>" --note "<their words>"`. That opens a gated
   run bound to the plan's exact bytes and file scope.
8. **seeds**: `awino deepplan seeds` puts the plan in the Seeds tracker: an
   epic, one seed per step with its priority and dependencies, linked as a
   seeds plan, so `sd ready` shows what can start. It never creates a tracker
   unasked; `--init` does, when the human wants one.

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
- **Challenge at least once**, marked with `--challenge`: a vague answer
  ("fast": what number?), a solution posing as the problem (what is wrong
  today?), scope creep (can it be P1?), the riskiest assumption (how would we
  know it is wrong, and how soon?).
- **Finish:** `awino deepplan grill-done`. It refuses while a question is open
  and asks for at least three human answers and one challenge, unless
  `--enough "<why>"`.

### Executing the approved plan

Work P0 phases first, each after what it depends on. Run the phase's success
command yourself until it passes, then verify it with `awino deepplan done <n>`:
it runs that command from the approved plan as gate evidence (failures count
toward the run's three strikes), ticks the phase off in `progress.md` only if it
passes, and closes its seed with that evidence. Done is every P0 phase verified:
`awino gate close` refuses before that, and reports open P1/P2 phases as
follow-ups (their seeds stay open). `awino gate open` refuses a Deep Plan's
`plan.md`: the run is opened only by `go`. Never tick by hand
and never edit `plan.md`: the run is bound to its exact bytes. Anything outside
the plan's scope goes under Follow-ups in `progress.md`, never into this run. Finish with
`awino gate check --diff-base HEAD` and `awino gate close`. If reality breaks
the plan (a check that fails for a reason the plan did not foresee), stop: never
loosen the check to get green. Say what changed and offer options with your
recommendation. Patch the stage it came from (usually `slices.md`) and `record`
it: that withdraws the approval, so nothing more is verified against the old
plan. `compile`, show the change, and on their yes run `go` again: the old run
is paused, and steps that were verified and did not change must pass their
check again on the new run to stay ticked.

## Failure Modes

| Failure | What it looks like | Fix |
| --- | --- | --- |
| Interview by spreadsheet | Five questions in one message | One question, a recommendation, then wait |
| Solution as problem | "Problem: add caching" | What is slow, for whom, measured how |
| Yes-man grill | Every answer accepted as given | Challenge the vague, the assumed, the scope creep |
| Everything is P0 | Nine must-haves | P0 is only what done needs; the rest is P1/P2 and stays tracked |
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
human's words, at least one challenge in the grill record, and the plan
approved by name. Paste the output of
`awino deepplan` and the plan path before saying planning is done. Execution
is complete only when every P0 phase shows `[x]` in `awino deepplan` and
`awino gate close` exits zero; until then report which phases are verified and
which are not, and name the P1/P2 phases left as follow-ups.
