---
name: awino-author-agent
description: Use when asked to create a new agent, subagent, orchestrator, or specialist. Searches the existing skill library first, writes a spec for approval, then emits a validated agent definition to staging.
allowed-tools: Read, Write, Glob, Grep, Bash
---

# Smith Author Agent

Emit a new agent definition that inherits the book's practices by construction.

## Instructions

### Step 1: Reuse before creation

Search, in order, and report what you found:

1. `.smith/emitted/` — did we already build this?
2. `$SMITH_SKILL_LIBRARY` — the local library
   has hundreds of skills. Grep for the capability.
3. Registry `reference_configs` — the book ships real agent definitions
   (`ref.meta-agent`, `ref.scout`, `ref.docs-scraper`, `ref.expert-4pack`).

If an existing agent covers ≥70% of the need, propose **adapting** it. Creating a
near-duplicate is `DUPLICATE_AGENT`.

### Step 2: Classify the role — this determines tools

| Role | Tools | Rationale |
| --- | --- | --- |
| reviewer / analyst | `Read, Grep, Glob` | read-only, cannot mutate |
| scout / explorer | `Read, Grep, Glob` | discovery only |
| orchestrator / coordinator | `Task, Read, Glob, TodoWrite` | **no Write, Edit, or Bash** |
| builder / implementer | `Read, Write, Edit, Grep, Glob, Bash` | full mutation, scoped files |
| test runner | `Bash, Read, Grep` | execute and report |
| fetcher / ingestor | `WebFetch, Write, Read` | network in, disk out |

The orchestrator row is the important one. An orchestrator that *can* implement
*will* implement — you get bloated context, no parallelism, and monolithic
reasoning that is hard to debug. Removing the capability is the forcing function.
Cite `chapters/5-tool-use/3-tool-restrictions.md` and
`chapters/7-patterns/3-orchestrator-pattern.md`.

### Step 3: Write the spec — do not skip to the file

Write `.smith/specs/<slug>-agent-spec.md`:

```markdown
# Spec: <agent-name>

## Requirement
<verbatim user request>

## Reuse analysis
- Searched: emitted/, local library, book reference configs
- Closest existing: <path> — <why insufficient>

## Design
- Role: <one of the six>
- Single responsibility: <one sentence, no "and">
- Tools: <explicit list> — least privilege for the role
- Model: <sonnet|opus|haiku> and why
- Spawns subagents: yes/no. If yes, they are leaf nodes.

## Failure modes it self-polices
- `MODE` — definition

## Completion protocol
- What "done" means
- The exact verification command
- Where it reports to

## Book grounding
- chapters/... — which practice this embodies

## Verification
- [ ] frontmatter valid, NO COLONS in description
- [ ] tools least-privilege for role
- [ ] single responsibility
- [ ] failure modes named
- [ ] completion protocol has a real command
```

**Present the spec and stop.** Ask: *"Approve, edit, or hold?"* Declining is a
valid outcome — report the resume path.

### Step 4: Emit to staging after approval

Write to `.smith/emitted/<agent-name>.md` using
`.smith/templates/agent.md.tmpl`. Never write directly into a live harness —
the human promotes it with `awino install`, which adapts the frontmatter and
destination for Claude, Kilo, Goose, Cursor, or Copilot.

Frontmatter, exactly:

```yaml
---
name: agent-name
description: Brief action-oriented description. Expects INPUT_NAME the thing it needs
tools: Read, Grep, Glob
model: sonnet
---
```

**Never put a colon inside a description value.** Write `Expects SPEC` not
`Expects: SPEC`. Colons cause silent agent-discovery failure — the agent simply
never appears, with no error.

Body sections, in order: role, propulsion, constraints, capabilities,
workflow, failure modes, communication, completion protocol.

Include the propulsion clause verbatim:

> Read your assignment. Execute immediately. Do not ask for confirmation, do not
> propose a plan and wait for approval, do not summarize back what you were told.
> Start working within your first tool call.

### Step 5: Lint

```powershell
& .smith\scripts\lint_agent.ps1 -Path .smith\emitted\<name>.md
```

Paste the output. Any FAIL blocks completion.

### Step 6: Report

```markdown
## Agent Emitted

| Field | Value |
|---|---|
| name | ... |
| role | ... |
| tools | ... |
| staged at | .smith/emitted/<name>.md |
| lint | PASS (n checks) |

**Promote with:** `awino install --harness <target>` after adding the staged
persona to Smith's source registry. For Kilo, the live global directory is
`~/.config/kilo/agents/`, not `~/.kilo/agent/`.
**Grounded in:** chapters/...
```

## Failure Modes

| Mode | Guard |
| --- | --- |
| `DUPLICATE_AGENT` | reuse search is mandatory and reported |
| `SPEC_SKIP` | no file before an approved spec |
| `TOOL_OVERGRANT` | tools match the role table, nothing extra "just in case" |
| `ORCHESTRATOR_ARMED` | coordinators never get Write, Edit, or Bash |
| `COLON_IN_DESCRIPTION` | silent discovery failure — lint catches it |
| `MULTI_RESPONSIBILITY` | if the description needs "and", split the agent |
| `DIRECT_PROMOTION` | emit to staging, human promotes |
| `NO_COMPLETION_PROTOCOL` | every agent states how it proves it is done |

## Completion

Done when: reuse searched, spec approved, file staged, lint output pasted with
zero FAILs, promotion command given.


