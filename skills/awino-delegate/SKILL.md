---
name: awino-delegate
description: Decompose work into parallel subagents with disjoint file ownership. Use when independent workstreams can run simultaneously and the orchestrator should coordinate rather than implement
---

# A.W.I.N.O. Delegate

Subagents run in **isolated sessions** and return a result. They do not inherit
the parent conversation. That isolation is the feature — it keeps the
orchestrator's context clean — and the constraint: whatever the subagent needs
must be in its assignment.

This is A.W.I.N.O.'s orchestrator capability. Use it instead of maintaining a
second always-on "master orchestrator" persona. The primary A.W.I.N.O. agent owns
human alignment, project memory, run gates, and final verification; this skill owns
decomposition, dispatch, and synthesis. One controller avoids two competing state
machines while retaining focused subagent execution.

## Orchestration contract

For non-trivial work:

1. Load confirmed `.smith/project.yaml` and the active issue/run.
2. If planning decisions remain, use the adaptive grill and wait for approval.
3. Create or link one tracked issue when the project requires it.
4. Stabilize shared interfaces before parallel work.
5. Spawn the minimum useful agents in dependency waves.
6. Treat agents as specialists, not authorities: independently verify every result.
7. Pause immediately on user `TWEAK`, update the plan/run checkpoint, then resume.
8. Close and sync tracker state only after executed gates pass and only commit/push
   when the user explicitly requested it.

Each materially new user task starts in a fresh parent session. Subagents are fresh
leaf sessions within that one task; they do not weaken the one-task-per-session rule.

## When NOT to parallelize

| Situation | Why not | Do instead |
| --- | --- | --- |
| sequential dependencies | B cannot start before A finishes | one session, or a pipeline |
| same-file edits | concurrent writes overwrite each other | serialize, or split the file |
| many cross-dependencies | coordination cost exceeds the benefit | single agent |
| task too small | spawn overhead dominates | do it directly |

The single hardest rule: **two subagents must never own the same file.** Verify
disjoint ownership *before* spawning. Overlap is `SAME_FILE_PARALLEL` and it
silently destroys work.

## Step 1 — Decompose and check ownership

Write the ownership table before spawning anything:

| Subagent | Role | Files owned (writes) | Files read | Depends on |
| --- | --- | --- | --- | --- |
| backend | builder | `server.js`, `routes/*` | `api-contract.md` | architect |
| frontend | builder | `index.html`, `style.css`, `script.js` | `api-contract.md` | architect |
| qa | test runner | `tests/*` | all source | backend, frontend |
| docs | writer | `README.md` | all source | backend, frontend |

Intersect every "files owned" pair. Any overlap → redesign the split.
Anything in a `depends on` column cannot run in the same wave.

## Step 2 — Size the tasks

| Size | Symptom | Fix |
| --- | --- | --- |
| too small | coordination costs more than the work | merge into a sibling |
| too large | runs a long time with no check-in; wasted effort risk grows | split |
| right | self-contained unit with one clear deliverable | go |

## Step 3 — Write self-contained assignments

The subagent knows nothing you know. Every assignment carries all five:

```markdown
## Assignment: <id>

**Role:** builder | reviewer | scout | test runner
**Objective:** one sentence.
**File scope (you may WRITE only these):**
- path/one
- path/two
**Context to read first:** paths + why
**Constraints:**
- Do not modify files outside your scope. That is FILE_SCOPE_VIOLATION.
- Do not spawn subagents. You are a leaf node.
- Do not start blocking processes such as servers.
**Verification:** the exact command that must pass.
**Done means:** paste the command output, then state "<id> COMPLETE".
```

Include an explicit completion signal. Without it, subagents trail off and the
orchestrator cannot tell finished from stalled.

Never start a blocking server in a subagent — it hangs the session. Emit the
command for a human to run in a separate terminal instead.

## Step 4 — Spawn in waves

One wave per dependency level. Within a wave, spawn in a **single message with
multiple parallel calls** — that is what makes them concurrent rather than
sequential.

```
Wave 1: architect                  (scaffolding + contract)
Wave 2: backend, frontend          (parallel, disjoint files)
Wave 3: qa, docs                   (parallel, read-only over wave 2 output)
```

State the wave plan before spawning. Sequential spawning of independent work is
`FALSE_PARALLELISM` — you pay the coordination cost and get none of the speedup.

## Step 5 — Coordinate, do not implement

While a wave runs the orchestrator has one job: coordination. Do not pick up
implementation work "to help" — that is `ORCHESTRATOR_IMPLEMENTS`, and the
structural cure is removing Write/Edit/Bash from the orchestrator's tools rather
than promising not to use them.

- Do **not** poll in a tight loop. Check at reasonable intervals.
- Batch communications: one comprehensive message per subagent, not five small ones.
- If a subagent stalls or errors, either give it one clarification or replace it.
  Two failed recoveries → escalate.

## Step 6 — Verify and synthesize

For each subagent: confirm it emitted its completion signal, then **independently
verify** its claim. A subagent asserting success is not evidence. Run the
verification command yourself.

Check for scope violations: `git status` should show only files that appear in the
ownership table. Anything else needs a justification or gets reverted.

## Reporting

```markdown
## Delegation Complete

| Subagent | Role | Files | Status | Verified |
|---|---|---|---|---|

### Waves
Wave 1: ... | Wave 2: ... (parallel) | Wave 3: ... (parallel)

### Independent verification
<pasted output>

### Scope check
git status vs ownership table: clean / N unexpected files

### Follow-ups
<new work discovered, as seeds>
```

## Failure Modes

| Mode | Definition |
| --- | --- |
| `SAME_FILE_PARALLEL` | two subagents own one file |
| `FALSE_PARALLELISM` | independent work spawned sequentially |
| `ORCHESTRATOR_IMPLEMENTS` | coordinator did the work itself |
| `THIN_ASSIGNMENT` | subagent lacked context, scope, or verification |
| `NO_COMPLETION_SIGNAL` | cannot distinguish finished from stalled |
| `NESTED_SPAWN` | a subagent spawned its own subagent |
| `POLLING_LOOP` | tokens burned on tight status checks |
| `UNVERIFIED_TRUST` | accepted a success claim without running the check |
| `BLOCKING_PROCESS` | subagent started a server and hung |
| `SCOPE_CREEP_UNCAUGHT` | out-of-scope writes not reconciled against the table |
| `COMPETING_CONTROLLER` | a second master orchestrator maintains separate state or rules |

## Completion

Done when: the ownership table showed no overlap, every subagent emitted its
signal, every claim was independently verified with pasted output, and
`git status` reconciles against the ownership table.

Grounding: chapters/7-patterns/3-orchestrator-pattern.md,
chapters/4-context/4-multi-agent-context.md, chapters/5-tool-use/3-tool-restrictions.md,
chapters/8-practices/7-operating-agent-swarms.md
