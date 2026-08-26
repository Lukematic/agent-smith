---
name: awino-debug
description: Debug concrete bugs, errors, exceptions, ValueError failures, pytest failures, and failing tests through reproduce, diagnose, fix, and verify phases with evidence-backed fix authorization.
allowed-tools: Read, Glob, Grep, Bash, Edit, Write, MultiEdit
---

# A.W.I.N.O. Debug

Use this skill for a concrete bug, error, exception, or failing test. Vague complaints
about agent behavior remain `awino-triage` work.

The lifecycle has exactly four phases:

1. **Reproduce**: open with `awino debug begin`, add a failing test or observed trace,
   and record it with `awino debug evidence`. If a required input or output is
   missing, first search the repository for the script or tool that generates it
   and run it with `--help` or a dry run before reporting the input as absent. An
   empty output directory is evidence the generator has not been run, not evidence
   it cannot run - see `MISSING_INPUT_ANNOTATED_INSTEAD_OF_GENERATED` below.
2. **Diagnose**: record an evidence-backed hypothesis with `awino debug hypothesize`.
3. **Fix**: run `awino debug authorize-fix --by <reviewer>` before changing production
   paths, then record each distinct approach with `awino debug attempt`.
4. **Verify**: execute the real regression command and record its result with
   `awino debug verify` before closing the normal run gates.

Three distinct failed approaches produce `ARCHITECTURE_QUESTIONABLE`; two do not,
and a successful third approach is not counted as a third failure.

The PreToolUse hook denies `Edit`, `Write`, and `MultiEdit` against production paths
before authorization. Claude Code does not expose shell filesystem effects as typed
paths, so Bash cannot be comprehensively guarded. Do not use Bash, scripts, patch
commands, or indirect tools to bypass the production-edit lock.

## Failure Modes

| Mode | Guard |
| --- | --- |
| `FIX_BEFORE_DIAGNOSIS` | authorization requires evidence and a hypothesis |
| `THREE_DISTINCT_PATCHES` | three failed approaches with one signature question the architecture |
| `SHELL_GUARD_BYPASS` | persona forbids Bash or indirect production edits before authorization |
| `MISSING_INPUT_ANNOTATED_INSTEAD_OF_GENERATED` | before reporting an input as missing, grep/glob for the generator that produces it and attempt to run it (`--help`, dry run, smallest real invocation); only after confirming no generator exists may absence be reported as a blocker |

## Completion

Complete only after `awino debug verify --cmd "<regression command>"` executes and
passes, the normal bugfix gates close, and any architecture escalation is resolved.

Grounding: `chapters/6-harnesses/3-feedback-loops.md`,
`chapters/11-failure-modes/2-agent-failure-modes.md`.
