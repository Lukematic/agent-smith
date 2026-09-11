# A.W.I.N.O.

**Agentic Workflow Intelligence & Navigation Orchestrator** is a human-facing
controller for agentic work. It establishes project intent, routes work to focused
capabilities, records executed evidence, and refuses completion when required gates
remain unsatisfied.

## For agents: when given this repository URL in a new project or session

If a human pastes `https://github.com/Lukematic/agent-smith` into your chat or project, follow this 4-step checklist:

### Step 1: Check and update the global A.W.I.N.O. engine

Run `awino --version` (or check if `~/.local/bin/awino.ps1` exists on Windows).

- **If already installed:** Check if the installation is stale or behind upstream:
  ```powershell
  awino start          # reports 'A.W.I.N.O. freshness: ahead=N behind=M'
  ```
  If `behind > 0`, run `awino update`. **Safe rebase contract:** `awino update` snapshots and preserves your target project's `.awino/project.yaml`, `.awino/memory/`, `.seeds`, and ledger history—it updates the engine and knowledge base without overwriting project intent.
- **If NOT installed on this machine:** Clone it once into a central directory (e.g. `~/.local/share/awino` or user tools directory, **never** inside the human's target project directory), and run the installer:

  Windows (PowerShell):
  ```powershell
  git clone https://github.com/Lukematic/agent-smith.git "$HOME\.local\share\awino"
  cd "$HOME\.local\share\awino"
  .\install.ps1
  ```

  macOS / Linux:
  ```bash
  git clone https://github.com/Lukematic/agent-smith.git "$HOME/.local/share/awino"
  cd "$HOME/.local/share/awino"
  ./install.sh
  ```

The installer installs `uv` if missing, builds the locked `.venv` in the clone, installs the global `awino` command (`~/.local/bin/awino.ps1` on Windows / `bin/awino` on POSIX), installs the persona and skills into every detected harness (Claude Code, Kilo, Roo, Goose, Cursor, Copilot), and verifies with `awino doctor --fast`. It creates no Python environment in the target project.

### Step 2: Set up or repair harness integration in the project

In the **human's target project directory**:

```powershell
awino start --fix    # verifies health, checks upstream freshness, writes .kilo/kilo.json and .kilo/agent/awino.md
```

In Kilo, `awino start --fix` writes `.kilo/kilo.json` with `default_agent: awino` and `.kilo/agent/awino.md`, so every **new** chat in that project opens as A.W.I.N.O., runs `awino start` then `awino best` itself, and replies with the header `[A.W.I.N.O. | mode | loop | run | knowledge | stance]`. An already-open chat keeps the agent it was opened with; open a new one.

### Step 3: Scaffold environment & confirm project intent

Inspect and configure what the project needs (environment, virtual environment, toolchain runner, tracker, and mission):

1. **Environment & Toolchain Setup:**
   ```powershell
   awino project-bootstrap    # inspects pyproject.toml, .venv, uv/poetry, justfile/Makefile, and tracker state
   ```
   If dependencies, virtualenv, or task runners are missing, confirm setup with `awino project-bootstrap --environment <choice> --tracker <choice> --runner <choice> --confirm`.

2. **Mission & Goals Onboarding:**
   If the project does not yet have a confirmed `.awino/project.yaml`, run:
   ```powershell
   awino onboard
   ```
   Answer the frontier questions (primary user, mission, goals, binding tenets) with `awino onboard --set key=value`, then confirm with `awino onboard --confirm`. Do not manually improvise a private scaffold.

### Step 4: Execute or resume work

```powershell
awino best "<the user's task request in their own words>"
```

`awino best` routes to the right skill, checks health, tracks the run in the ledger, and refuses completion until all required verification gates pass.

## Start here

- **One thing to remember (`awino best`):** [Quickstart](docs/quickstart.md)
- **Using A.W.I.N.O.:** [User guide](docs/user-guide.md)
- **Understanding the operating model:** [Operating guide](docs/operating-guide.md)
- **Integrating or operating A.W.I.N.O.:** [Agent and harness guide](docs/agent-guide.md)
- **All documentation:** [Documentation index](docs/README.md)

## Install in Claude Code

Plugin installation is a user trust action. A URL pasted into chat cannot safely
authorize global installation, and the agent must not work around that boundary with
a global Bash mutation. Run these commands yourself in Claude Code:

```text
/plugin marketplace add Lukematic/agent-smith
/plugin install awino@awino
/reload-plugins
```

Native CLI equivalents are:

```bash
claude plugin marketplace add Lukematic/agent-smith
claude plugin install awino@awino
```

The native install provides the `awino` agent and all 16 canonical `awino-*` skills
automatically. It does not initialize `.seeds` or `.awino`, install Python, or run a
global shell installer. The deterministic gate ledger is an optional CLI layer and
requires `uv`. Its launcher automatically creates or refreshes a locked `.venv`
for each installed plugin version and prints `DEGRADED` when `uv` is unavailable.

After reload, select the `awino` agent. In a new project, let it ask before running
`awino work-init`.

## Standalone CLI install

For the deterministic ledger CLI and non-Claude harness integrations, install from a clone:

```bash
git clone https://github.com/Lukematic/agent-smith.git awino
cd awino
./install.sh
```

On Windows, run `./install.ps1` in PowerShell instead. Then verify the installation:

```bash
awino install-status
awino mode-status
awino doctor
```

In each new project, begin with:

```bash
awino onboard
```

For a fresh project with no tracker state, A.W.I.N.O. asks first and then uses
`awino work-init`; plugin installation itself never creates project state.

The canonical operating constitution is [`AWINO.md`](AWINO.md).

## How it works

1. The primary A.W.I.N.O. controller identifies the project, mission, toolchain,
   tracker, active run, pending decision, and recommended next action.
2. It routes the request to a canonical `awino-*` skill or handles a small task
   directly. Optional specialist modes provide stricter tool permissions but are not
   required.
3. For tracked work, a run declares its objective, write scope, optional plan, and
   optional Seeds issue.
4. A.W.I.N.O. executes verification commands and records their actual exit codes.
5. `awino gate close` computes whether the run may be called complete.

The startup display reports `Project`, `Mission confidence`, `Toolchain`, `Tracker`,
`Active run`, `Pending human decision`, `Next recommended action`, and `Route skill`.

## Compatibility

**Agent Smith** is the former product name. The `smith` executable, `agent-smith`
persona, legacy environment names, and some repository filenames remain deprecated
compatibility aliases. `AGENT_SMITH.md` is a deprecated pointer to `AWINO.md`, not
a second constitution. A pre-rename project whose state lives in `.smith/` keeps
working: the first command that touches state transparently migrates it to
`.awino/` (atomic rename, zero data loss). New instructions, automation, and examples must use the
`awino` command and A.W.I.N.O. identity.

## Optional modes

The generated editor modes are: 🧭 A.W.I.N.O., 🧭 A.W.I.N.O. Consult,
🧭 A.W.I.N.O. Plan, 🧭 A.W.I.N.O. Discover, and 🧭 A.W.I.N.O. Research.

## Reference documentation

| Document | Purpose |
| --- | --- |
| [Operating guide](docs/operating-guide.md) | Status fields, modes versus loops, knowledge budget, gates, scenarios, and evidence-driven interaction |
| [User guide](docs/user-guide.md) | Installation, paired work, Seeds, gates, updates, and troubleshooting |
| [Agent and harness guide](docs/agent-guide.md) | Startup, routing, plans, checkpoints, verification, and status protocol |
| [Mission](docs/MISSION.md) | Product mission and naming rationale |
| [Philosophy](docs/PHILOSOPHY.md) | Owner's working philosophy and how the product enforces it |
| [Installation](docs/install.md) | Detailed installer and harness destinations |
| [Architecture](docs/architecture.md) | Internal layers and design boundaries |
| [Deployment](docs/deployment.md) | Global and project-local deployment choices |
| [Distribution](docs/distribution.md) | Packaging and release workflow |
| [Enforcement](docs/enforcement.md) | Gate-ledger mechanics |
| [Harness](docs/harness.md) | Mental models, guides, and sensors |
| [Skills](docs/skills.md) | Generated canonical skill catalog |
| [Commands](docs/commands.md) | Generated command reference (live `--help`) |
| [API keys](docs/api-keys.md) | Provider credentials and custom gateways |
| [Nuclear engineer walkthrough](docs/walkthrough-nuclear-engineer.md) | Domain-support example |
| [Credits and sources](docs/credits-and-sources.md) | Attribution and upstream sources |

## License

[MIT](LICENSE)
