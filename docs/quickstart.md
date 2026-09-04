# Quickstart - six things to remember

A.W.I.N.O. has many commands. You need six. Everything else is reachable from
these, and `awino best` runs the written order so you do not have to recall it.

## 1. Start a sitting

```bash
awino best
```

Prints the startup report (project, mission, health, tracker, active run,
stance), then asks the next unanswered mission question, then names the next
ready Seed. Read-only. In a chat session, just say "start" - the agent runs it.

## 2. Set up or repair a repo

```bash
awino start --fix
```

Creates `.smith/` state, creates `.venv` when a `pyproject.toml` exists, and
asks one yes/no question for anything that is a human decision (create a
project file? init a tracker?). Nothing silent: every action prints
`CREATED`/`DECLINED`/`FAILED`.

## 3. Define why the project exists

```bash
awino mission --heilmeier
awino mission --set "risks=workers ignore the skill\nlogin dependency"
```

Eight questions, asked one gap at a time in the stance each calls for.
Exams written as `claim -> command` become verification commands. The living
document is `<state>/MISSION.md`; it regenerates on every answer, on every
`gate close`, and at `awino best --end`. Derived insights name risks with no
exam, exams with no command, and jargon.

## 4. Do a piece of work

```bash
awino gate open bugfix "what you are fixing" --scope path/to/file.py
awino floor open "the concrete failure, in plain words" --scope path/to/file.py
#   ... whatever agent is present executes the printed prompt ...
awino floor close          # re-runs verification itself; a claim alone never completes
awino gate record tested --cmd "<real test command>"
awino gate close           # refuses unless every gate holds; then walks you through it
```

`--verify` is discovered from `justfile`/`Makefile`/pytest; if none exists you
must pass one. `gate close` fires the task-close order: a walkthrough of what
changed and what was proven (`<state>/WALKTHROUGH.md`) plus three grill
questions to test your understanding.

## 5. Let it drive the backlog

```bash
awino auto --max-seeds 3 --confirm-budget --dry-run   # see what would run
awino auto --max-seeds 3 --confirm-budget             # run it, bounded
```

Stops on the first Seed that does not verify and leaves a pending decision.
Never pushes.

## 6. End a sitting

```bash
awino best --end
```

Summary, lesson check, mission refresh.

## Talking to it

The agent's posture switches on your words - no command needed:

| You say | It becomes |
| --- | --- |
| "I think we should..." | steel-man: strongest case against, first |
| "break this down" | first-principles |
| "so that means..." | assumption audit |
| "teach me", "I don't understand" | teach-back with a grill at the end |
| "research..." | intake: five sub-questions before any answer |

`awino stance` shows and sets the default. Every switch prints one `STANCE ->`
line; nothing switches silently.

## Updating

```bash
awino update
```

Snapshots your mission, lessons, and ledger; pulls; restores them;
re-provisions the environment (recreates `.venv` if lost); refreshes installed
skill copies; runs health. New features like the mission document need no
setup - they appear in `awino best` on first use.

## Editing the order

`<state>/playbook.json` overrides any event's steps:

```json
{"session-start": ["start", "mission-gap", "next-seed"]}
```

Known steps: `start carry-intent mission-gap next-seed walkthrough grill-offer clear-intent
mission-refresh summary lesson-check`. Unknown steps are refused at load.
