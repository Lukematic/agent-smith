---
name: awino-triage
description: Use when an agent is misbehaving and the complaint is vague. Converts "the agent is bad" into a named failure mode, a responsible surface, and a structural fix rather than a prompt patch.
allowed-tools: Read, Glob, Grep, Bash
---

# A.W.I.N.O. Triage

"The agent is bad" is not a diagnosis. It bundles unrelated problems with
unrelated fixes and points effort at the model, which is rarely the cause.

This skill converts a complaint into a fixable claim.

## Instructions

### Step 1: Refuse the vague complaint

If the user gave a mood rather than a symptom, ask for exactly three things and
stop:

1. **What did it do?** (observable action, not your interpretation)
2. **What should it have done?**
3. **How often?** (once / intermittently / every time)

Frequency matters: *every time* points at prompt or tools; *intermittently*
points at context or model.

### Step 2: Localize to a surface

Four surfaces. Load `chapters/11-agent-readiness/1-the-four-surfaces.md` if you
need the authoritative treatment.

| Surface | Question | Fixed by |
| --- | --- | --- |
| **prompt** | Were the instructions actually stated? | rewording, structuring, ordering |
| **model** | Is this model capable of this task? | model swap, decomposition |
| **context** | Did the agent *see* what it needed? | retrieval, scoping, compaction |
| **tools** | Could it act, and only as intended? | tool design, restriction, hooks |

The whole point of naming the surface is that **the fix follows from the
surface**. A context failure is not fixed by rewording the prompt.

### Step 3: Name the failure mode

Fetch `chapters/11-agent-readiness/2-failure-modes.md` for the canonical list.
Common named modes from the operational vocabulary:

| Mode | Symptom | Surface |
| --- | --- | --- |
| `PATH_BOUNDARY_VIOLATION` | wrote outside its worktree | tools |
| `FILE_SCOPE_VIOLATION` | edited an unassigned file | tools |
| `SILENT_FAILURE` | error occurred, nothing reported | prompt + tools |
| `INCOMPLETE_CLOSE` | marked done without gates passing | prompt |
| `PREMATURE_COMPLETION` | claimed success, no evidence | prompt |
| `CONTEXT_BLOAT` | loaded everything, degraded quality | context |
| `MISROUTED_TOOL` | used search where it needed compute | tools |
| `ORCHESTRATOR_IMPLEMENTS` | coordinator wrote code itself | tools |
| `POLLING_LOOP` | burned tokens checking status | prompt |
| `LOST_IN_MIDDLE` | ignored info present in a long context | context |

If nothing fits, propose a new mode name in `SCREAMING_SNAKE_CASE` and record it
in `memory/lessons.md` — the vocabulary is supposed to grow.

### Step 4: Propose the structural fix first

Two columns, always. The prompt-patch column exists to be *rejected*.

| Symptom | Prompt patch (rejected) | Harness fix (required) |
| --- | --- | --- |
| writes to `/tmp` | "always write to /workspace" | permission filter enforcing the prefix |
| commits unlinted | "always lint first" | hook intercepting the commit tool call |
| duplicate file reads | "avoid reading twice" | dedup returning cached content |
| bad output shape | format spec in prompt | schema validator that rejects |
| implements instead of delegating | "you are a coordinator" | remove Write/Edit/Bash from its tools |

Prompt patches are fragile for a structural reason: they depend on consistent
instruction-following in every context on every turn, and instructions dilute
each other. Harness fixes run as code.

If only a prompt patch is available, label it `PROMPT-PATCH (debt)` and state
the structural fix it defers. Load
`chapters/6-harnesses/5-harness-engineering.md` for the doctrine.

### Step 5: Make the mistake unrepeatable

A fix is not complete until recurrence is blocked. Specify one:

- **Hook** — intercepts the tool call before it lands
- **Permission / tool removal** — capability no longer exists
- **Validator** — output rejected unless it conforms
- **Test** — regression case that fails if it comes back
- **Binding lesson** — one line in `memory/lessons.md`, loaded every session

Then append the lesson:

```markdown
- [2026-08-21] `ORCHESTRATOR_IMPLEMENTS` — coordinator agents get Task/Read/Glob/TodoWrite only. Never Write, Edit, or Bash. (surface: tools)
```

### Step 6: Report

```markdown
## Triage: <one-line symptom>

**Failure mode:** `NAMED_MODE`
**Surface:** context
**Frequency:** every time

### Why
Two sentences of mechanism, cited.

### Fix
| Rejected | Required |
|---|---|
| ... | ... |

### Unrepeatable via
Hook / restriction / validator / test — pick one and name the file to change.

### Recorded
memory/lessons.md line added.

Sources: chapters/11-agent-readiness/2-failure-modes.md, chapters/6-harnesses/5-harness-engineering.md
```

## Failure Modes (of this skill)

| Mode | Guard |
| --- | --- |
| `VAGUE_ACCEPTED` | never triage a mood; demand the symptom |
| `PROMPT_PATCH_REFLEX` | structural column is mandatory |
| `NO_SURFACE` | every diagnosis names one of the four surfaces |
| `NO_RECURRENCE_BLOCK` | a fix without an unrepeatability mechanism is incomplete |
| `MISSING_LESSON` | triage always ends in a lessons.md line |

## Completion

Done when: named mode, named surface, structural fix, recurrence block, and a
lessons.md line exist.
