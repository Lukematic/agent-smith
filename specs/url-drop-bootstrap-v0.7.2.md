# URL-Drop Bootstrap and Non-Destructive Update Contract

## Problem

When a human starts a new project or session and pastes `https://github.com/Lukematic/agent-smith`, agents across different harnesses (Claude Code, Kilo, Copilot, Roo) frequently misunderstand what to do:
1. They may clone a second, private copy of A.W.I.N.O. inside the target project, causing knowledge forks and repository clutter.
2. They may fail to check if the global engine is already installed or stale (`behind` origin/main).
3. They may manually improvise project scaffolding (`pyproject.toml`, virtualenvs) instead of using `awino project-bootstrap` and `awino onboard`.
4. Users fear that running updates will overwrite project-local mission (`.smith/project.yaml`), memory, or issue trackers.

## Decision

1. Add a binding rule to `memory/lessons.md`: `URL_DROP_BOOTSTRAP`.
2. Document a standardized 4-step checklist prominently in `README.md` and `docs/agent-guide.md`:
   - Step 1: Check/update global A.W.I.N.O. engine (`awino --version` / `awino update`). Safe rebase contract: `awino update` snapshots and preserves project intent, memory, and seeds without overwriting.
   - Step 2: Set up/repair harness integration (`awino start --fix`).
   - Step 3: Provision environment & confirm intent (`awino project-bootstrap` + `awino onboard`).
   - Step 4: Execute/resume work (`awino best "<task>"`).

## Verification

1. `memory/lessons.md` contains the new rule.
2. `README.md` and `docs/agent-guide.md` reflect the standardized 4-tier checklist and safe update contract.
3. Full doc, health, and hook regression tests pass.
