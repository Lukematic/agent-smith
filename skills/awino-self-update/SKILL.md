---
name: awino-self-update
description: Use when asked to update yourself or refresh knowledge. Diffs the local registry against the upstream book repo, curates entries for new chapters, and reports drift against recorded lessons.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

# A.W.I.N.O. Self-Update

A.W.I.N.O.'s knowledge is upstream and changes daily. This skill keeps the
*index* current without ever bulk-importing the corpus.

## Instructions

### Step 1: Diff

```powershell
& .smith\scripts\registry_build.ps1
```

Read `.smith/knowledge/DRIFT.md`. It reports upstream chapter count,
registry chapter count, `ADDED`, `REMOVED`, and the appendix example corpora.

Appendices are intentionally indexed at *directory* granularity — the example
configs under `appendices/examples/<project>/` are a browse-on-demand corpus
entered via `_index.md`, not registry entries. Do not add them individually.

### Step 2: Curate ADDED entries one at a time

For each newly added chapter path — **not in bulk**:

1. Fetch it: `& .smith\scripts\fetch.ps1 -Path <path>`
2. Read only the frontmatter plus the first section.
3. Write a registry entry:

```yaml
      - {key: 7.12, path: chapters/7-patterns/12-new-thing.md,
         title: New Thing,
         tags: [tag, tag],
         use_when: the specific situation that should route here}
```

`use_when` is the load-bearing field — it is what routing keys off. A registry
entry without `use_when` is dead weight. Writing one requires having read the
file; guessing it is `UNGROUNDED_CLAIM`.

4. If the new chapter matches an existing `routes:` phrasing, add its key there.

### Step 3: Handle REMOVED entries

For each removed path:
1. Delete its registry entry.
2. `Grep memory/ ` for the path. If a lesson or expertise record cites it, flag
   the citation as orphaned — do not silently delete the lesson. Lessons outlive
   chapters; re-anchor to the nearest surviving chapter instead.

### Step 4: Lesson drift check

This is the step that makes update meaningful rather than cosmetic.

For each rule in `memory/lessons.md` that cites a chapter, check whether that
chapter changed (sha differs in `MANIFEST.json` after refetch). If it changed,
re-read the relevant section and answer: **does the lesson still hold?**

Three outcomes:
- **holds** — bump the lesson's verified date
- **refined** — append a new dated line; never edit history in place
- **contradicted** — mark the old line `[SUPERSEDED yyyy-mm-dd]` and add the new one

`lessons.md` is append-only. History is evidence.

### Step 5: Prune cache

Delete cache files not referenced in `MANIFEST.json`, and any whose registry
entry no longer exists. Cache is disposable; memory is not.

### Step 6: Report

```markdown
## Self-Update Complete

| Metric | Before | After |
|---|---|---|
| registry chapters | 82 | 84 |
| upstream chapters | 84 | 84 |
| drift | 2 added | 0 |
| cache files | 12 | 9 |

### New chapters indexed
- chapters/7-patterns/12-x.md — routed under "multi agent coordination"

### Lesson drift
- 2 lessons re-verified, 1 refined, 0 superseded

**Next:** <what the new material changes about current work, or "no action">
```

## Failure Modes

| Mode | Guard |
| --- | --- |
| `BULK_REGISTRY_DUMP` | one entry at a time, each with tags and use_when |
| `GUESSED_USE_WHEN` | you must read the file before you can describe when to route to it |
| `SILENT_LESSON_DELETE` | orphaned citations are flagged, never deleted |
| `HISTORY_REWRITE` | lessons.md is append-only; supersede, do not edit |
| `CORPUS_IMPORT` | never copy chapter bodies into the repo outside cache/ |
| `APPENDIX_EXPLOSION` | appendices stay at directory granularity |

## Completion

Done when: drift is 0 or explicitly deferred with a reason, every ADDED entry has
`use_when`, lesson drift is reported, and cache is pruned.

