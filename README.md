# A.W.I.N.O.

**Agentic Workflow Intelligence & Navigation Orchestrator** — working history:
Agent Smith. Mission and full naming rationale: [docs/MISSION.md](docs/MISSION.md).

An agentic-engineering working partner that **spawns agents for a task and
refuses to let them claim completion they have not earned**.

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

**One line.** Everything needed is installed for you.

```powershell
irm https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.ps1 | iex
```

```bash
curl -fsSL https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.sh | sh
```

Or clone first, if you prefer to read before you run:

```bash
git clone https://github.com/Lukematic/agent-smith.git
cd agent-smith
./install.sh          # or  .\install.ps1  on Windows
```

### What you need, and what gets handled for you

| Tool | Needed | If missing |
| --- | --- | --- |
| `git` | **yes** | you install it: `winget install Git.Git`, `brew install git` |
| `uv` | yes | **installed automatically** from astral.sh |
| Python 3.12 | yes | **installed automatically** by uv, isolated in `.venv` |
| `just` | no | **installed automatically** via winget, scoop, brew, cargo, apt, dnf, or pacman |
| `sd` (seeds) | no | **installed automatically** when bun or npm already exists |

`git` is the only thing you must have first: it cannot be installed silently, and
nothing else can run before the clone.

Nothing touches your system Python. Optional tools never block the install: if
`just` cannot be installed, every recipe still runs as `uv run awino ...`. Seeds is
only installed when a JavaScript runtime is already present, because pulling in a
runtime to get an optional tracker would be a large uninvited change.

To skip optional tooling entirely, on a locked-down machine or in CI:

```bash
./install.sh --no-tools        #  .\install.ps1 -NoTools
```

### Where it goes

The installer detects your editors and installs where each one looks:

| Harness | Persona lands at | Skills | Selectable as |
| --- | --- | --- | --- |
| Claude Code | `~/.claude/agents/` | `~/.claude/skills/` | a subagent |
| Kilo | `~/.config/kilo/agents/` | `~/.config/kilo/skills/` | **a mode, via `mode: primary`** |
| Roo | `custom_modes.yaml` or `.roomodes` | via modes | **a mode in the selector** |
| GitHub Copilot | `<VS Code prompts>/agent-smith.chatmode.md` | not supported | a chat mode |
| Goose | `~/.agents/agents/` | `~/.agents/plugins/` | an agent |
| Cursor | `.cursor/rules/agent-smith.mdc` | not supported | always-on context |

Frontmatter is **rebuilt per harness**, never copied. Each tool validates a
different shape, and an unexpected or missing field is not always ignored: a Kilo
agent without `mode: primary` installs as a subagent and never appears in the
selector, which looks exactly like a failed install.

Skills are **linked**, not copied, so `git pull` updates the live install with
nothing else to run. Re-running the installer is safe and idempotent.

### Verify

```bash
awino install-status     # persona and skills, per harness
smith mode-status        # Kilo and Roo modes
awino doctor             # every project gate
```

### Modes, for Kilo and Roo

🧭 A.W.I.N.O. is the single default human-facing controller. Consult, plan,
discover, research, RPI, and evidence are routed visibly as canonical `awino-*`
skills or isolated subagents. The other Kilo modes are optional manual
least-privilege presets, never required. A.W.I.N.O. cannot silently switch the
user's selected Kilo mode.

Five modes appear in the selector:

| Mode | Tools | Structurally cannot |
| --- | --- | --- |
| 🧭 A.W.I.N.O. | read, edit, command, mcp | — |
| 🧭 A.W.I.N.O. Consult | read, mcp | edit, so a consult stays a consult |
| 🧭 A.W.I.N.O. Plan | read, **Markdown-only** edit, mcp | execute commands or implementation edits |
| 🧭 A.W.I.N.O. Discover | read, mcp | implement before mission confirmation |
| 🧭 A.W.I.N.O. Research | read, mcp | release unsupported factual synthesis |

```bash
awino install-mode                  # every detected editor
awino install-mode --editor kilo    # just one
```

Reload the editor window and they appear in the mode dropdown.

The primary controller starts by displaying:

```text
Project: <path or unknown>
Mission confidence: <confirmed|derived|unknown>
Toolchain: <detected tools or unknown>
Tracker: <tracker and state or none>
Active run: <id or none>
Pending human decision: <decision or none>
Next recommended action: <one action>
Route skill: <canonical awino-* skill or direct>
```

### Per project

Add **three lines** to `AGENTS.md`, never a copy of this repo:

```bash
smith pointer
```

See [docs/install.md](docs/install.md) for pointing any other tool at the persona,
and [docs/deployment.md](docs/deployment.md) for why copying causes
`KNOWLEDGE_FORK`.

If your harness is not detected, open `agents/agent-smith.md` from the clone and
paste its body into that product's **system prompt**, **custom mode**, or **agent
instructions** field. The Markdown body is portable; only frontmatter and tool
permissions are harness-specific. `docs/install.md` lists the known destinations.

### First five minutes

```bash
awino context     # what Smith thinks home and project are
smith onboard     # mission, user, goals, tenets, expectations, success
awino doctor      # health, with a remedy per finding
smith work        # tracked work, if a tracker exists
```

Run `awino context` **first in every new repository.** Every gate command Smith
records comes from that resolution, so a wrong answer there makes every later green
gate meaningless.

---

## The enforcement loop

This is the part that fixes "agents never follow instructions".

```bash
# 1. Open a run. The task class determines the gates. You do not choose them.
awino gate open code-change "add retry to the fetch client" --scope src/smith/knowledge.py

#    gates required before close:
#      [ ] planned
#      [ ] tested
#      [ ] linted
#      [ ] tests_not_weakened
#      [ ] scope_respected

# 2. Satisfy each gate with a real command. Smith runs it and records the exit code.
awino gate record planned --attest "docs/plans/2026-08-21-retry.md"
awino gate record tested  --cmd "uv run pytest"
awino gate record linted  --cmd "uv run ruff check src tests"

# 3. Independent checks that do not trust the agent's word.
awino gate check --diff-base HEAD

# 4. Try to close. Refused unless every gate holds.
awino gate close
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
| "load the right skill" | `awino gate skill <name>` makes usage auditable |

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
awino route "what is a harness"      # show chapters, fetch nothing
awino fetch chapters/6-harnesses/1-what-is-a-harness.md
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
| [docs/api-keys.md](docs/api-keys.md) | API Keys and Custom Gateways |
| [docs/walkthrough-nuclear-engineer.md](docs/walkthrough-nuclear-engineer.md) | How Smith supports a nuclear engineer without pretending to be one |
| [docs/credits-and-sources.md](docs/credits-and-sources.md) | Credits and Sources |
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







