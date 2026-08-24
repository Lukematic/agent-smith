---
name: awino-author-tool
description: Decide whether a capability should be a skill, a hook, a script, a recipe, or an MCP server, then build it. Use when asked to create a tool or extend what an agent can do
---

# A.W.I.N.O. Author Tool

Most "I need a tool for this" requests do not need a tool. Building an MCP server
where a skill would do is `TOOL_SPRAWL`: more surface, more failure modes, more
tokens spent on tool descriptions, and a thing you now maintain.

**Skills teach *how to reason*. Tools grant *new capability*.** If the model
already can do it and just does it wrong, that is a skill or a hook — not a tool.

## Step 1 — The decision gate

Answer in order and stop at the first yes.

| Question | If yes | Artifact |
| --- | --- | --- |
| Is this a procedure the model could already follow, just inconsistently? | teach it | **skill** |
| Should it run automatically on a lifecycle event, without being asked? | enforce it | **hook** |
| Is it deterministic logic with fixed inputs and outputs? | script it | **script** the skill calls |
| Is it a repeatable task needing fixed prompts, settings, extensions, params, or a schedule? | package it | **recipe** |
| Does it need to reach a system the model genuinely cannot reach? | connect it | **MCP server** |
| Is it a bundle of the above to share or version? | distribute it | **plugin** |

The most-skipped row is **script**. "Regenerate the index", "check the manifest",
"lint the definition" are code, not reasoning. Asking a model to do
deterministic work every time is slower, costlier, and less reliable than a
15-line script the skill invokes. Determinism belongs in code.

## Step 2 — Prefer a hook when enforcement matters

If the goal is "the agent must always X", a prose instruction is a
`PROMPT_PATCH (debt)`. Instructions compete with each other and dilute; hooks run
as code.

`hooks/hooks.json` in the plugin:

```json
{
  "hooks": {
    "SessionEnd": [
      { "hooks": [{ "type": "command", "command": "${PLUGIN_ROOT}/scripts/notify.sh" }] }
    ]
  }
}
```

Hook commands receive the event payload as JSON on stdin. Use `${PLUGIN_ROOT}` for
paths inside the plugin.

Hooks execute local commands, which is exactly why plugins are a trust boundary.
Review before enabling; never hook something you have not read.

## Step 3 — Reuse before building

Search and report, in order:

1. `$AWINO/emitted/` — did we build it already?
2. `$AWINO_SKILL_LIBRARY` — the local library
3. Built-in skills and extensions exposed by the active harness
4. Installed harness plugins or extension inventory
5. Registry `reference_configs` — the book ships real examples

Existing capability at ≥70% → adapt it. A near-duplicate is `DUPLICATE_TOOL`.

## Step 4 — Design (only if a tool is genuinely required)

Tool design rules:

- **Consolidate.** Many narrow tools cause misrouting. For a large catalog use
  umbrella dispatch: `search`, `describe`, `invoke`.
- **Name for the model, not the codebase.** The name and description *are* the
  routing signal. `search_customer_orders` beats `query2`.
- **Say when NOT to use it** in the description. Negative guidance prevents more
  misrouting than positive guidance.
- **Typed inputs and outputs.** Validate at the boundary; reject malformed input
  rather than passing it through.
- **Errors are for the model.** "File not found at /x/y — check the path" is
  actionable; `ENOENT` is not.
- **Least privilege.** Read-only unless mutation is the point.

## Step 5 — Spec, then approval

Write `$AWINO/specs/<slug>-tool-spec.md`:

```markdown
# Spec: <name>

## Requirement
<verbatim request>

## Decision gate
| Question | Answer | Why |
Chosen artifact: skill | hook | script | recipe | mcp | plugin
Rejected alternatives and why.

## Reuse analysis
Searched: emitted/, local library, builtins, plugins, book refs
Closest existing: <path> — why insufficient

## Design
- Inputs (typed) / Outputs (typed)
- Failure cases and the message each returns
- Permissions required
- Where it lives

## Verification
- [ ] runs on a happy path — command + expected output
- [ ] rejects malformed input
- [ ] error messages are model-actionable
- [ ] does not duplicate an existing capability
```

Present and stop. Approval required before building.

## Step 6 — Build and place correctly

| Artifact | Location |
| --- | --- |
| global skill | `~/.agents/skills/<name>/SKILL.md` |
| project skill | `<project>/.agents/skills/<name>/SKILL.md` |
| plugin skill | `<plugin>/skills/<name>/SKILL.md` → loads as `<plugin>:<name>` |
| hook | `<plugin>/hooks/hooks.json` + `<plugin>/scripts/` |
| script | beside the skill that calls it, in the skill's directory |
| recipe | `~/.config/goose/recipes/<name>.yaml` |

Emit to `$AWINO/emitted/` first. The human promotes. Writing straight into a live
skills directory is `DIRECT_PROMOTION`.

Supporting files live in the skill directory and are reachable via the developer
extension's file tools once the skill loads.

## Step 7 — Verify with real execution

Run it. Paste the output. Then run it wrong on purpose and paste that too — a tool
whose failure path is untested will fail silently in production.

```powershell
& $AWINO\scripts\lint_agent.ps1 -Path $AWINO\emitted\<name>\SKILL.md
```

## Reporting

```markdown
## Tool Authored

| Field | Value |
|---|---|
| artifact | skill / hook / script / recipe / mcp |
| rejected | what was considered and why not |
| staged at | ... |
| lint | PASS (n checks) |

### Happy path
<pasted output>
### Failure path
<pasted output>

**Promote with:** <exact command>
```

## Failure Modes

| Mode | Definition |
| --- | --- |
| `TOOL_SPRAWL` | built a tool where a skill or hook would do |
| `SKIPPED_GATE` | did not walk the decision table before building |
| `DUPLICATE_TOOL` | rebuilt something that already exists |
| `PROMPT_PATCH_INSTEAD_OF_HOOK` | prose instruction where enforcement was required |
| `MODEL_DOES_DETERMINISM` | asked the model to do work a script should do |
| `OPAQUE_ERRORS` | error messages the model cannot act on |
| `UNTYPED_BOUNDARY` | no input validation, malformed data passes through |
| `NAME_FOR_HUMANS` | tool name/description not written as a routing signal |
| `NO_NEGATIVE_GUIDANCE` | description omits when not to use it |
| `DIRECT_PROMOTION` | written into a live directory instead of staging |
| `UNTESTED_FAILURE_PATH` | only the happy path was verified |

## Completion

Done when: the decision gate is documented with rejected alternatives, reuse was
searched, the spec was approved, lint passes, and **both** happy-path and
failure-path outputs are pasted.

Grounding: chapters/5-tool-use/1-tool-design.md, chapters/5-tool-use/4-scaling-tools.md,
chapters/5-tool-use/5-skills-and-meta-tools.md, chapters/6-harnesses/5-harness-engineering.md

