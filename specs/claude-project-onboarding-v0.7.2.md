# Claude project onboarding v0.7.2

## Problem

A project may contain `.smith/` run and memory directories but no confirmed
`.smith/project.yaml`. `awino onboard` without a `--set` answer intentionally
prints the next question but does not persist invented intent. Before this
release, SessionStart stayed silent in that state, so Claude could begin
substantive work without asking the human for project goals.

## Decision

At SessionStart:

- when `.smith/project.yaml` is human-confirmed, inject its mission, goals,
  tenets, expectations, workflow rules, run continuation, and project memory;
- when it is absent or unconfirmed, inject `PROJECT_SETUP_REQUIRED`, derive the
  next onboarding frontier question from repository evidence, tell the active
  agent to ask that one question, and include the exact `awino onboard --set
  key=value` command that persists the human answer;
- never invent, auto-confirm, or silently write project goals.

Release as `0.7.2` because Claude caches plugin bytes by version.

## Verification

1. A missing project file emits the exact next frontier question and persistence
   command.
2. A confirmed project file injects project memory and no setup request.
3. Existing routing, resume, compaction, and session-log hook tests pass.
4. A fresh Claude process in `treads-pipeline` emits
   `PROJECT_SETUP_REQUIRED` with the `primary_user` question.
