---
name: awino-memory
description: Dual-write memory discipline for A.W.I.N.O. - stores durable rules in the Memory MCP extension for recall and mirrors every entry to an append-only file ledger for audit
---

# A.W.I.N.O. Memory

Two stores, deliberately. When the active harness provides a Memory MCP extension,
it gives **recall** by loading saved memories into context. Files give
**audit** — dates, supersessions, and history that a key-value cache cannot hold.

**Rule: every `remember_memory` call is mirrored to a file. If the two disagree,
the file wins.** Memory MCP is a cache; `$AWINO/memory/` is the ledger.

## Why both

| Property | Memory MCP | File ledger |
| --- | --- | --- |
| in every prompt automatically | yes | no |
| survives a wiped extension | no | yes |
| keeps history and supersessions | no | yes |
| costs tokens on every turn | **yes** | only when read |
| greppable, diffable, reviewable | no | yes |

That token cost is why entries are one line. Anything longer lives in a file and
the memory entry points at the path.

## The five categories — do not invent more

| Category | `is_global` | Contains | Example |
| --- | --- | --- | --- |
| `agentic_doctrine` | `true` | universal rules about building agents | orchestrators never get Write, Edit, or Bash |
| `failure_modes` | `true` | named modes coined during triage | `FEEDBACK_IGNORED` — worker skipped review-feedback.txt |
| `book_routing` | `true` | topic-to-chapter routing learned in practice | harness questions route to 6.1 and 6.5 |
| `project_conventions` | `false` | how *this* repo works | verify with `uv run pytest -q` |
| `project_failures` | `false` | what goes wrong in *this* repo | builders keep touching `src/legacy/` |

Global = about agent-building. Local = about a specific codebase. Getting this
backwards pollutes every other project — a repo's test command is not doctrine.

Entries outside these five are `MEMORY_SPRAWL`. If you believe a sixth is needed,
propose it and get approval; do not create it silently.

## Write protocol

### Step 1 — Qualify it

Store only what changes future behavior. Ask:
- Would a future session act differently knowing this?
- Is it durable, not a one-off?
- Is it a *decision or rule*, not a restatement of chapter content?

Book content does not go in memory — that is what the registry and fetcher are
for. Storing chapter summaries is `CORPUS_IN_MEMORY`, and it is expensive because
it rides in every prompt forever.

### Step 2 — Store for recall

```
remember_memory(
  category="agentic_doctrine",
  data="Orchestrator agents get Task/Read/Glob/TodoWrite only - never Write/Edit/Bash",
  tags=["tools","orchestration","least-privilege"],
  is_global=true
)
```

One line. Imperative. Self-contained — it will be read with no surrounding context.

### Step 3 — Mirror to the ledger

Doctrine and failure modes → `$AWINO/memory/lessons.md`, append-only:

```markdown
- [2026-08-21] `ORCHESTRATOR_IMPLEMENTS` — orchestrators get Task/Read/Glob/TodoWrite only, never Write/Edit/Bash. (surface: tools)
```

Patterns and decisions → `$AWINO/memory/expertise/<domain>.jsonl`:

```json
{"type":"convention","domain":"orchestration","description":"Orchestrators get Task/Read/Glob/TodoWrite only","classification":"foundational","source":"chapters/5-tool-use/3-tool-restrictions.md","ts":"2026-08-21"}
```

Types: `convention | pattern | failure | decision`.
Classification: `foundational` (confirmed across sessions) | `tactical`
(session-specific, the default) | `observational` (unverified, one-off).

Do not promote something to `foundational` on first sighting. One observation is
`observational`. Repetition earns promotion.

### Step 4 — Confirm the round trip

A memory that does not come back is not a memory. On the next session:

```
retrieve_memories(category="agentic_doctrine", is_global=true)
```

Confirm the entry appears. Unverified writes are `PHANTOM_MEMORY`.

## Revision protocol

`lessons.md` is **append-only**. Never edit a line in place.

| Situation | Action |
| --- | --- |
| still holds | append a re-verified date; leave the original |
| needs refinement | append the refined line below the original |
| now wrong | mark original `[SUPERSEDED 2026-08-21]`, append the replacement, and `remove_specific_memory` the stale MCP entry |

History is evidence. Editing it in place destroys the record of what you used to
believe and why you changed — which is exactly what you need when a lesson turns
out to have been right the first time.

## Read protocol

At session start, in this order:

1. `$AWINO/memory/lessons.md` — binding rules that override defaults
2. `retrieve_memories(category="agentic_doctrine", is_global=true)`
3. `retrieve_memories(category="project_conventions", is_global=false)`
4. Relevant `expertise/<domain>.jsonl` only when working that domain

Lessons **override defaults**. If a lesson contradicts your instinct, the lesson
wins — it was earned from a real failure.

## Hygiene

Run during `awino-self-update`:

- Duplicates across MCP and files → reconcile, file wins
- Entries longer than one line → move body to a file, point the entry at it
- Local entries that are actually doctrine → promote to global
- Global entries that are actually project-specific → demote
- Contradictions → resolve explicitly, record the supersession
- `observational` entries seen repeatedly → promote to `tactical` or `foundational`

## Reporting

```markdown
## Memory Updated

| Store | Category | Scope | Entry |
|---|---|---|---|
| MCP | agentic_doctrine | global | one-line rule |
| lessons.md | — | global | dated append |

Round trip verified: yes / no
Supersessions: N
```

## Failure Modes

| Mode | Definition |
| --- | --- |
| `MEMORY_SPRAWL` | entries outside the five categories |
| `SINGLE_WRITE` | stored to MCP without mirroring to a file, or vice versa |
| `CORPUS_IN_MEMORY` | chapter content stored instead of a decision |
| `SCOPE_INVERSION` | project specifics stored global, or doctrine stored local |
| `HISTORY_REWRITE` | a lessons.md line edited in place |
| `PHANTOM_MEMORY` | write never confirmed by a retrieve |
| `PREMATURE_FOUNDATIONAL` | one observation classified as confirmed doctrine |
| `MULTILINE_ENTRY` | long entry riding in every prompt instead of living in a file |
| `MISSING_LESSON` | non-trivial session ended with no memory write |

## Completion

Done when: the entry exists in both stores, scope and category are correct, the
round trip was confirmed with a retrieve, and any supersession is recorded.

Grounding: chapters/4-context/1-context-fundamentals.md,
chapters/8-practices/6-knowledge-evolution.md,
chapters/12-long-horizon-agent-state/3-memory-and-intent.md
