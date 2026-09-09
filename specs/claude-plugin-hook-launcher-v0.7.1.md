# Claude plugin hook launcher v0.7.1

## Problem

Claude Code discovers the `awino:awino` agent and its hooks, but each hook calls
bare `awino`. Claude's hook Bash environment does not add the plugin `bin/`
directory to `PATH`, so SessionStart exits 127 before project memory, goals,
resume state, and routing context can be injected.

## Decision

Every Claude hook must call the immutable versioned plugin launcher through the
official hook-time `${CLAUDE_PLUGIN_ROOT}` variable:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/awino" hook <event>
```

The plugin release must be bumped to `0.7.1` because Claude caches plugin bytes
by version. Reusing `0.7.0` would leave existing installations pointed at stale
hook commands.

## Project behavior

The plugin still does not silently create project state. When a project has a
confirmed `.smith/project.yaml`, SessionStart and UserPromptSubmit load it and
inject its goals, tenets, expectations, memory, current run, and pending
continuation. When it is absent, A.W.I.N.O. asks before onboarding creates it.

## Verification

1. Plugin manifest validation passes.
2. A regression test proves every hook starts with the plugin-relative launcher
   and no hook starts with bare `awino`.
3. Claude installs `0.7.1` into a new cache directory.
4. A fresh Claude process in another project shows a successful SessionStart
   hook instead of `awino: command not found`.
5. The fresh process lists and activates the scoped `awino:awino` agent.
