# A.W.I.N.O.

**Agentic Workflow Intelligence & Navigation Orchestrator** is a human-facing
controller for agentic work. It establishes project intent, routes work to focused
capabilities, records executed evidence, and refuses completion when required gates
remain unsatisfied.

## Start here

- **Six things to remember:** [Quickstart](docs/quickstart.md)
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

The native install provides the `awino` agent and all 14 canonical `awino-*` skills
automatically. It does not initialize `.seeds` or `.smith`, install Python, or run a
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
a second constitution. New instructions, automation, and examples must use the
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
| [Installation](docs/install.md) | Detailed installer and harness destinations |
| [Architecture](docs/architecture.md) | Internal layers and design boundaries |
| [Deployment](docs/deployment.md) | Global and project-local deployment choices |
| [Distribution](docs/distribution.md) | Packaging and release workflow |
| [Enforcement](docs/enforcement.md) | Gate-ledger mechanics |
| [Harness](docs/harness.md) | Mental models, guides, and sensors |
| [Skills](docs/skills.md) | Generated canonical skill catalog |
| [API keys](docs/api-keys.md) | Provider credentials and custom gateways |
| [Nuclear engineer walkthrough](docs/walkthrough-nuclear-engineer.md) | Domain-support example |
| [Credits and sources](docs/credits-and-sources.md) | Attribution and upstream sources |

## License

[MIT](LICENSE)
