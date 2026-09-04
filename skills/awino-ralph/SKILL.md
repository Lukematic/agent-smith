---
name: awino-ralph
description: Iterative work-review loop with fresh context per iteration and independent verification. Use when a task has a machine-checkable completion gate and may need several attempts to get right
---

# A.W.I.N.O. Ralph

Standard agent loops rot through **context accumulation**: every failed attempt
stays in history, so by iteration four the model wades through its own noise
before it can work. Ralph starts each iteration with **fresh context** and passes
state through the **ledger only**.

The second mechanism is **independent verification**: the worker's completion
claim is worth nothing; a separate process re-runs the check.

Both mechanisms are already built. Ralph is not a script you install; it is the
floor loop driven until the gate holds.

## When NOT to use this

- Simple one-shot tasks - the loop overhead dominates
- Exploratory or interactive work - there is nothing to gate on
- **Tasks with no verifiable completion criteria** - the disqualifier. No
  `--verify` command that goes red on the problem, no Ralph.

## Cost - state it before starting

Up to `--max-floors` (hard cap 3) fresh worker contexts per trip. Announce the
ceiling and confirm before spending. A silent multi-iteration spend is
`UNBOUNDED_SPEND`.

## The loop

```bash
awino gate open <class> "<objective>" --scope <files>
awino floor open "<the task, concretely>" --verify "<red-capable command>" --scope <files> --max-floors 3
#   fresh context executes the printed prompt (this session, a subagent, any harness)
awino floor close           # re-runs --verify itself; routes COMPLETE / REVISE(next floor) / MAX-ITERATIONS
#   on REVISE: a new prompt is written carrying the exact failure text; execute it; close again
awino gate close            # completion authority; fires the walkthrough
```

For a command-only gate with no worker (flaky test, build retry):

```bash
awino gate loop <gate> --cmd "<command>" --max-iterations 3
```

## State contract

Everything lives in the ledger, nothing in a scratch directory:

| Where | What |
| --- | --- |
| `dispatch-pending` artifact | the open floor: prompt path, verification, budget |
| `dispatch-floor` artifact | each closed floor: invocation id, verified true/false |
| `dispatch-route` / checkpoint | the routing decision and carried feedback |
| `dispatch-terminal` artifact | complete / max-iterations |

Read it with `awino gate status` or `awino floor close` (which refuses when no
floor is pending). Nothing else survives an iteration, by design.

## Verification is the skill

`--verify` must be **tight**: fast, deterministic, and red on *this* problem.
`floor open` discovers `just test` / `make test` / pytest when a recipe exists and
refuses when nothing does. Never pass a tautology.

Write the verify command relative to the project root `awino context` prints -
not to A.W.I.N.O.'s home. This has gone wrong twice (`FLOOR_VERIFY_CWD`).

## Failure Modes

| Mode | Definition |
| --- | --- |
| `UNBOUNDED_SPEND` | starting without announcing the floor ceiling |
| `NO_GATE` | running Ralph on work with no red-capable verify |
| `SELF_GRADED` | treating the worker's `COMPLETE` as evidence instead of `floor close` |
| `CONTEXT_CARRYOVER` | pasting the previous attempt into the next prompt by hand; the ledger carries feedback |
| `CEILING_RAISE` | passing `--max-floors` above 3 or reopening after `MAX-ITERATIONS` without a human decision |

## Completion

Done when `awino floor close` prints `COMPLETE` and `awino gate close` prints
`COMPLETE  N gate(s) satisfied`. `MAX-ITERATIONS` is a human decision, not a
retry.

Grounding: chapters/9-mental-models/8-loop-engineering.md,
chapters/7-patterns/1-plan-build-review.md
