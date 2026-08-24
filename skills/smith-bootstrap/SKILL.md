---
name: smith-bootstrap
description: Use on Agent Smith first run to create the .smith folder scaffold and verify the knowledge registry against upstream. Triggers on "bootstrap agent smith", "set up agent smith", missing .smith folder.
allowed-tools: Read, Write, Glob, Grep, Bash
---

# Smith Bootstrap

First-run initialization for Agent Smith. Idempotent — safe to re-run.

## Instructions

### Step 1: Locate or create the home

Check for `.smith/` at the workspace root.

- **Present** → skip to Step 3 (verification only).
- **Absent** → create the full scaffold in Step 2.

Never create `.smith/` in a subdirectory. It is a root-level sibling of
your project folders.

### Step 2: Create the scaffold

```text
.smith/
  AWINO.md                  # canonical constitution - REQUIRED, do not stub
  AGENT_SMITH.md            # deprecated compatibility pointer
  PLAN.md
  knowledge/{SOURCES.yaml, REGISTRY.yaml, MANIFEST.json, cache/}
  skills/                   # smith-* skills
  memory/{lessons.md, SESSION_LOG.md, expertise/}
  templates/{agent.md.tmpl, skill.md.tmpl, spec.md.tmpl}
  specs/                    # spec-as-contract output
  emitted/                  # staged agents/skills awaiting human promotion
  scripts/{fetch.ps1, registry_build.ps1}
```

Write `knowledge/cache/.gitignore` containing `*` — the cache is disposable.

Seed `memory/lessons.md` with a header and zero rules. Do not invent lessons.

### Step 3: Verify the registry

```powershell
& .smith\scripts\registry_build.ps1
```

Read `knowledge/DRIFT.md`. Report:

| Metric | Value |
| --- | --- |
| upstream chapters | N |
| registry chapters | N |
| added / removed | N / N |

If `ADDED > 0`, do **not** silently bulk-add. Hand off to `smith-self-update`,
which fetches each new file and writes a curated entry with tags and `use_when`.

### Step 4: Verify the fetcher

Fetch exactly one file as a smoke test:

```powershell
& .smith\scripts\fetch.ps1 -Path chapters/6-harnesses/1-what-is-a-harness.md
```

Expect `OK <cachefile> sha=... bytes=...` on first run and `CACHE_HIT` on a
second immediate run. Paste both outputs. This is the verification gate — do
not claim bootstrap succeeded without it.

### Step 5: Check the local skill library

Before Smith ever authors a skill, it must know what already exists:

```powershell
Get-ChildItem -Recurse -Filter SKILL.md $SMITH_SKILL_LIBRARY |
  Measure-Object | Select-Object Count
```

Record the count in `memory/SESSION_LOG.md`. Reuse beats creation.

### Step 6: Report

```markdown
## Agent Smith Bootstrapped

| Check | Result |
|-------|--------|
| scaffold | created / already present |
| registry | 82/82 chapters, 0 drift |
| fetcher | OK + CACHE_HIT verified |
| local skills available | N |
| lessons recorded | N |

**Next:** ask a concept question (routes to smith-consult) or describe a failing
agent (routes to smith-triage).
```

## Failure Modes

| Mode | Guard |
| --- | --- |
| `SCAFFOLD_IN_WRONG_PLACE` | .smith must be at workspace root |
| `STUB_CONSTITUTION` | AWINO.md must be the full text, never a placeholder |
| `BULK_REGISTRY_DUMP` | never add registry entries without tags and use_when |
| `PREMATURE_COMPLETION` | bootstrap is not done until the fetch smoke test output is pasted |
| `INVENTED_LESSONS` | lessons.md starts empty; rules come from real sessions |

## Completion

Done when: scaffold exists, drift report generated, fetch + cache-hit outputs
pasted, local skill count recorded.



