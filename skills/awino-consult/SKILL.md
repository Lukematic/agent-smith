---
name: awino-consult
description: Use when asked a conceptual agentic-engineering question such as what is a harness, how should context be managed, or which pattern fits. Fetches at most three chapters from the book registry and answers with citations.
allowed-tools: Read, Glob, Grep, Bash
---

# A.W.I.N.O. Consult

Answer a concept question from the living book — grounded, cited, and cheap.

## Non-negotiable budget

**Maximum 3 knowledge files per question.** If you believe you need more, the
question is compound. Split it and say so. Opening a 4th file is
`CONTEXT_BLOAT`.

## Instructions

### Step 1: Classify, do not fetch

Read `.smith/knowledge/REGISTRY.yaml` only. Match the question against the
`routes:` block first — it maps common phrasings straight to chapter keys.

If no route matches, match on `tags` and `use_when`. If still nothing matches,
say so and offer `awino-self-update` — upstream may have added a chapter.

Never guess a path. A fabricated path is `UNGROUNDED_CLAIM`.

### Step 2: Announce the plan before spending

```
Routing: "<question>" -> [6.1, 6.5]
Files:   chapters/6-harnesses/1-what-is-a-harness.md
         chapters/6-harnesses/5-harness-engineering.md
Budget:  2/3
```

### Step 3: Fetch

```powershell
& .smith\scripts\fetch.ps1 -Path <registry path>
```

Then `Read` the cache file. Prefer `Grep` on the cache file when the question is
narrow — a targeted grep costs a fraction of a full read on a large file like
`10-practitioner-toolkit/1-claude-code.md`.

If `CACHE_HIT`, note the age. If age exceeds the policy, say
`[STALE: Nd]` in the answer.

### Step 4: Answer

Structure:

```markdown
## <Concept>

**Definition (1-2 sentences).**

- Key point (book: chapters/6-harnesses/1-what-is-a-harness.md)
- Key point (book: chapters/6-harnesses/5-harness-engineering.md)

**In your context:** how this applies to the current project. Mark speculation `[inferred]`.

**Recommendation:** one sentence.

**Next:** the concrete action.

---
Sources: <paths> | sha <12-char> | fetched <date>
```

Rules:
- Quote at most 15 consecutive lines from any chapter.
- Every book-derived claim carries a path. Everything else is marked `[inferred]`.
- Tables over prose for comparisons. Bullets over paragraphs.
- No preamble, no restating the question, no hedging.

### Step 5: Write back if durable

If the answer produced a rule you will want next session, append to
`.smith/memory/expertise/<domain>.jsonl`:

```json
{"type":"convention","domain":"harness","description":"Repeat mistakes get structural fixes not prompt text","classification":"foundational","source":"chapters/6-harnesses/5-harness-engineering.md","ts":"2026-08-21"}
```

Do not record restatements of chapter content. Record decisions that apply to
*this* project.

## Failure Modes

| Mode | Guard |
| --- | --- |
| `CONTEXT_BLOAT` | hard stop at 3 files |
| `UNGROUNDED_CLAIM` | no path, no claim |
| `FABRICATED_PATH` | paths come from the registry only |
| `WALL_OF_TEXT` | 15-line quote ceiling, bullets/tables default |
| `STALE_KNOWLEDGE` | cache age stated when over policy |
| `MISSING_LESSON` | durable rules get written to expertise |

## Examples

**"What's a harness?"** → routes `[6.1, 6.2]` → 2 files → definition + the
six-component stack + one line on why harness quality is often mistaken for
model quality.

**"My agent keeps writing to the wrong directory"** → this is not a consult.
Hand off to `awino-triage`.

**"How do skills, subagents, and slash commands differ, and how should I
structure my orchestrator, and what does it cost?"** → compound. Refuse as one
consult; propose three: `[5.5, 7.7]`, `[7.3, 5.3]`, `[8.3]`.

## Completion

Done when: routing stated, budget respected, every claim cited, sources footer
present, and any durable rule written to memory.

Verify with the exact command and paste its output:

```bash
awino route "<the question>"     # confirms routing without spending budget
awino status                     # confirms cache age and lesson count
```

If a run is open, record the consult so knowledge use is auditable:

```bash
awino gate skill awino-consult
```

