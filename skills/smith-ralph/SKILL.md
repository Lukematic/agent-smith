---
name: smith-ralph
description: Iterative work-review loop with fresh context per iteration and cross-model review. Use when a task has a machine-checkable completion gate and may need several attempts to get right
---

# Smith Ralph

Standard agent loops rot through **context accumulation**: every failed attempt
stays in history, so by iteration four the model wades through its own noise
before it can work. Ralph fixes this by starting each iteration with **fresh
context** and passing state through **files only**.

The second mechanism is **cross-model review**: one model works, a *different*
model reviews. A reviewer that shares the worker's blind spots ships the worker's
bugs.

## When NOT to use this

- Simple one-shot tasks — the loop overhead dominates
- Exploratory or interactive work — there is nothing to gate on
- **Tasks with no verifiable completion criteria** — this is the disqualifier.
  Ralph needs a machine-checkable SHIP signal. No gate, no Ralph.

## Cost warning — state it before starting

Up to `RALPH_MAX_ITERATIONS` (default 10) iterations, each running **both**
models. Announce the ceiling and get confirmation. Silently burning ten
iterations of two models is `UNBOUNDED_SPEND`.

## State contract

All state in `.goose/ralph/`. Nothing else survives an iteration.

| File | Purpose |
| --- | --- |
| `task.md` | the task — read first, every iteration |
| `iteration.txt` | current iteration number |
| `work-summary.txt` | what the worker did this iteration |
| `work-complete.txt` | exists when the worker claims done |
| `review-result.txt` | `SHIP` or `REVISE` |
| `review-feedback.txt` | specific feedback for the next iteration |
| `.ralph-complete` | written on success |
| `RALPH-BLOCKED.md` | written when the worker is stuck — aborts the loop |

**If it is not in a file, it did not happen.** The next iteration has no memory
of you. Writing a summary only to the chat is `STATE_LOST`.

## Running it

```bash
~/.config/goose/recipes/ralph-loop.sh "task description"
~/.config/goose/recipes/ralph-loop.sh ./thoughts/plans/2026-08-21-thing.md
```

Prefer the file form for anything non-trivial — a plan or PRD carries far more
signal than a sentence. This is the natural seam with `agent-smith:smith-rpi`:
RPI produces the plan, Ralph executes it under a gate.

Set models explicitly to skip prompts:

```bash
RALPH_WORKER_MODEL="gpt-5.5" RALPH_WORKER_PROVIDER="openai" \
RALPH_REVIEWER_MODEL="claude-sonnet-4-5" RALPH_REVIEWER_PROVIDER="anthropic" \
RALPH_MAX_ITERATIONS=6 \
~/.config/goose/recipes/ralph-loop.sh ./task.md
```

Worker and reviewer **should differ**. Same-model review still runs but loses the
independent perspective that is the point.

## Work phase contract

1. `cat .goose/ralph/task.md` — the task
2. `cat .goose/ralph/iteration.txt`
3. `cat .goose/ralph/review-feedback.txt` — **if this exists, address it first**
4. List existing files; read before modifying
5. Make meaningful incremental progress; run verification
6. `echo "<what I did>" > .goose/ralph/work-summary.txt` — always
7. `echo done > .goose/ralph/work-complete.txt` — only if genuinely complete
8. If truly stuck, write `RALPH-BLOCKED.md` with what was tried and why

Ignoring existing feedback and redoing prior work is `FEEDBACK_IGNORED` — the
most common and most expensive Ralph failure.

## Review phase contract

You are a different model. Your fresh perspective is the product.

Criteria:
1. Does the work actually accomplish the task?
2. Does it run without errors?
3. Is it reasonably complete, not half-done?
4. Are there obvious bugs?

**Strict but fair.** Do not nitpick style when functionality is correct. Do
reject incomplete work, code that does not run, and failing tests. Rubber-stamping
to end the loop is `REVIEW_THEATER`; blocking on formatting is `NITPICK_BLOCK`.

Output exactly one:

```bash
echo "SHIP" > .goose/ralph/review-result.txt
# or
echo "REVISE" > .goose/ralph/review-result.txt
echo "<specific, actionable feedback>" > .goose/ralph/review-feedback.txt
```

`REVISE` with vague feedback wastes an entire iteration. Name the file, the
problem, and the expected behavior.

## Exit conditions

| Condition | Result |
| --- | --- |
| `review-result.txt` = SHIP | success, `.ralph-complete` written |
| `RALPH-BLOCKED.md` appears | abort, escalate to human |
| max iterations reached | failure — the task was mis-scoped or ungated |

Hitting max iterations means the loop is not converging. Do not raise the
ceiling; re-scope the task. Three consecutive REVISEs on the *same* issue means
stop and escalate — that is the three-strikes rule.

## Reset

```bash
rm -rf .goose/ralph
```

## Reporting

```markdown
## Ralph Loop Complete

| Field | Value |
|---|---|
| task | ... |
| worker / reviewer | model (provider) / model (provider) |
| iterations | N of M |
| outcome | SHIPPED / BLOCKED / MAX_REACHED |

### Iteration history
| # | Work | Review | Feedback |

### Lesson
<what to change so this converges faster next time>
```

## Failure Modes

| Mode | Definition |
| --- | --- |
| `NO_GATE` | Ralph on a task with no machine-checkable completion signal |
| `STATE_LOST` | progress reported in chat instead of written to a state file |
| `FEEDBACK_IGNORED` | worker did not read `review-feedback.txt` first |
| `REVIEW_THEATER` | reviewer shipped work it did not actually verify |
| `NITPICK_BLOCK` | reviewer blocked on style while functionality was correct |
| `VAGUE_FEEDBACK` | REVISE without a file, a problem, and an expectation |
| `SAME_MODEL_REVIEW` | worker reviewing its own work without saying so |
| `UNBOUNDED_SPEND` | loop started without stating the iteration ceiling |
| `CEILING_INFLATION` | raising max iterations instead of re-scoping |

## Completion

Done when: `.ralph-complete` exists, the shipping iteration's verification output
is pasted, and a lesson was written to memory about why it took N iterations.

Grounding: chapters/7-patterns/4-autonomous-loops.md,
chapters/4-context/2-context-strategies.md, chapters/9-mental-models/8-loop-engineering.md
