# Agent Smith

An agentic-engineering expert that **spawns agents for a task and refuses to let
them claim completion they have not earned**.

Two problems it solves:

1. **Knowledge goes stale and bloats context.** Smith holds an *index* of a
   daily-updated book, not the book. Bodies are fetched on demand, capped at
   three files per task.
2. **Agents forget instructions.** Prose like "always verify before saying done"
   competes with forty other lines and loses. Smith replaces the instruction with
   a **gate ledger**: completion is computed from recorded exit codes, never
   asserted.

```
Agents do not report done. They run a gate that decides whether they may.
```

---

## Install

Requires [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just).

```bash
git clone https://github.com/Lukematic/agent-smith.git
cd agent-smith
just install
just doctor
```

Wire it into your agent harness as a global plugin plus a persona:

```bash
just link
```

That symlinks the repo to `~/.agents/plugins/agent-smith/` (skills and hooks) and
installs `~/.agents/agents/agent-smith.md` (the persona). Skills load namespaced
as `agent-smith:smith-consult` and so on.

Per-project you add **three lines** to `.goosehints`, never a copy of this repo.
See [docs/deployment.md](docs/deployment.md) for why copying causes
`KNOWLEDGE_FORK`.

---

## The enforcement loop

This is the part that fixes "agents never follow instructions".

```bash
# 1. Open a run. The task class determines the gates. You do not choose them.
smith gate open code-change "add retry to the fetch client" --scope src/smith/knowledge.py

#    gates required before close:
#      [ ] planned
#      [ ] tested
#      [ ] linted
#      [ ] tests_not_weakened
#      [ ] scope_respected

# 2. Satisfy each gate with a real command. Smith runs it and records the exit code.
smith gate record planned --attest "docs/plans/2026-08-21-retry.md"
smith gate record tested  --cmd "uv run pytest"
smith gate record linted  --cmd "uv run ruff check src tests"

# 3. Independent checks that do not trust the agent's word.
smith gate check --diff-base HEAD

# 4. Try to close. Refused unless every gate holds.
smith gate close
```

A failing gate refuses:

```
REFUSED  GATE_FAILING tested recorded a nonzero exit code.
You may not report this work as complete.
```

### Why this works when instructions do not

| Instruction approach | Ledger approach |
| --- | --- |
| "always run the tests" | exit code recorded by Smith, not described by the agent |
| "never weaken a test" | diff is parsed for deleted asserts and added skips |
| "stay in your file scope" | `git diff --name-only` reconciled against declared scope |
| "stop after 3 attempts" | attempt counter blocks the gate at 3 |
| "load the right skill" | `smith gate skill <name>` makes usage auditable |

The agent supplies the command. **Smith supplies the exit code.** A model can
claim a suite passed; it cannot produce a zero exit code from a failing one.

---

## Commands

```bash
just              # list everything
just check        # lint + test + validate. The single gate.
just fmt          # format and autofix
just tidy-check   # find clutter without moving it
just tidy         # archive clutter to archive/YYYY-MM-DD/
just update       # refresh knowledge, report drift
just status       # cache age, indexed chapters, binding lessons
```

Knowledge and routing:

```bash
smith route "what is a harness"      # show chapters, fetch nothing
smith fetch chapters/6-harnesses/1-what-is-a-harness.md
smith drift                          # registry vs upstream
```

Authoring gates:

```bash
smith validate skills agents         # every artifact
smith validate --selftest            # prove the validator blocks bad input
```

---

## Layout

```
agent-smith/
  AGENT_SMITH.md      constitution, always loaded
  README.md           this file
  justfile            every command
  pyproject.toml      uv + ruff + pytest
  plugin.json         Open Plugin manifest
  agents/             the persona, installed separately
  hooks/              SessionStart staleness guard
  skills/             smith-* skills
  knowledge/          REGISTRY.yaml index + disposable cache
  memory/             lessons.md ledger + expertise records
  src/smith/          deterministic CLI
  tests/              proves the gates actually block
  docs/               architecture, deployment, enforcement
  archive/            dated clutter, reversible
```

One rule: **docs live in `docs/`, code in `src/`, nothing loose at the root.**
`just tidy-check` fails if that drifts.

---

## Docs

| Doc | Read it when |
| --- | --- |
| [docs/install.md](docs/install.md) | installing, and pointing any harness at the persona |
| [docs/enforcement.md](docs/enforcement.md) | agents ignore instructions and you want the mechanism |
| [docs/deployment.md](docs/deployment.md) | deciding global vs per-repo install |
| [docs/architecture.md](docs/architecture.md) | understanding the five layers and phases |
| [docs/harness.md](docs/harness.md) | which mental model fires when, and the guides/sensors inventory |
| [docs/distribution.md](docs/distribution.md) | sharing Smith, and why not pip |

| [docs/skills.md](docs/skills.md) | Skills |
| [docs/harness.md](docs/harness.md) | The Harness |
---

## Principles

- **Harness over prompt.** A repeated mistake gets a structural fix, not a
  warning. Prompt patches are labelled `PROMPT-PATCH (debt)`.
- **Named failure modes.** "The agent is bad" is rejected. Name the mode and the
  surface: prompt, model, context, or tools.
- **Three strikes.** Never retry the same failing gate more than three times.
  Stop and escalate with what was tried.
- **Cite or mark inferred.** Every knowledge claim carries a chapter path.
- **Archive, do not delete.** A wrong archive is recoverable.






