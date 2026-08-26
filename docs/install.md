# Install

From a GitHub URL to a working agent, and what to do first.

---

## Native Claude Code plugin

Run these commands yourself in Claude Code:

```text
/plugin marketplace add Lukematic/agent-smith
/plugin install awino@awino
/reload-plugins
```

Native CLI equivalents:

```bash
claude plugin marketplace add Lukematic/agent-smith
claude plugin install awino@awino
```

Merely pasting the repository URL into chat cannot safely trigger installation.
Plugin installation crosses a user trust boundary, so one explicit user action is
required. The agent must not simulate consent by running a global Bash installer.

The plugin supplies the `awino` agent and exactly 14 canonical `awino-*` skills. It
does not initialize `.seeds` or `.smith`. A.W.I.N.O. asks before project setup and
uses `awino work-init` after approval.

The native agent and skills work without the Python CLI. Deterministic ledger,
mission, and gate commands require `uv`. On first use after an install or update,
the plugin launcher uses the committed lockfile to create that version's `.venv`
automatically. It emits `DEGRADED` only when `uv` is unavailable or preparation
fails.

Update with `claude plugin update awino@awino`, uninstall with
`claude plugin uninstall awino@awino`, and run `/reload-plugins` after updates.
Claude Code 2.1.186 or later is required for this release's plugin `settings.json`
default-agent behavior; on older versions, select `awino` manually.

---

## Standalone CLI bootstrap

**Windows (PowerShell 7+)**

```powershell
irm https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.ps1 | iex
```

**macOS or Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.sh | sh
```

This optional standalone route clones the repository, installs `uv` if missing, builds an isolated
environment, installs the persona and skills into every agent harness it finds, and
verifies the result. It refuses loudly rather than half-succeeding, because a
partial install produces an agent answering confidently from a broken knowledge
base.

Nothing is installed into your system Python. No `pip`, no global packages.

---

## Or, step by step

If you would rather see each step:

```bash
git clone https://github.com/Lukematic/agent-smith.git
cd awino

./install.sh              # or ./install.ps1 on Windows
```

Which does:

| Step | What happens | Verified by |
| --- | --- | --- |
| 1 | install `uv` if absent | `uv --version` |
| 2 | build `.venv` from the lockfile | `uv sync --frozen` |
| 3 | regenerate derived files | `awino fix` |
| 4 | install persona and skills per harness | `awino install-status` |
| 5 | run every project gate | `awino doctor` |
| 6 | run the test suite | `pytest` |

If step 5 or 6 fails, installation reports `INSTALL INCOMPLETE` and tells you the
remedy. That is intentional: a green install claim with a red gate underneath is
the exact failure this whole tool exists to prevent.

---

## Prerequisites

| Tool | Required | If missing |
| --- | --- | --- |
| `git` | yes | `winget install Git.Git`, `brew install git` |
| `uv` | yes | installed automatically by the bootstrap |
| `just` | optional | `winget install Casey.Just`, `brew install just` |
| `sd` (seeds) | optional | `bun install -g @os-eco/seeds-cli` |

Only `git` cannot be installed for you. `just` is convenience: every recipe also
runs as `uv run awino ...`. Seeds is an optional issue tracker; A.W.I.N.O. never
installs or initializes it without being asked.

The plugin's A.W.I.N.O. backend has its own locked environment. Its launcher ignores
`VIRTUAL_ENV` and `CONDA_PREFIX` only for that child process, preserving the user's
shell and the caller directory as `AWINO_PROJECT`. Target commands execute from that
project directory. In a uv project, `uv run` selects the target `.venv` by cwd without
`--active`. Use `awino env` for detected-manager details and optional POSIX/Windows
activation commands; activation is not required. `awino setup --dry-run` is read-only,
and onboarding never creates an environment.

In short: Python executes code; `venv` isolates packages; `pip` installs packages;
`uv` locks, synchronizes, and runs a project's declared environment.

---

## Where it lands

```
~/dev/awino/               the clone, editable, updated with git pull
  .venv/                         isolated environment

~/.claude/agents/agent-smith.md  persona, if Claude Code is present
~/.claude/skills/awino-*/        skills, junctioned to the clone
~/.agents/agents/agent-smith.md  persona, if Goose is present
~/.agents/plugins/agent-smith/   the whole plugin, linked
~/.config/kilo/agents/agent-smith.md  Kilo primary persona
~/.config/kilo/skills/awino-*/        Kilo skills, linked
<VS Code prompts>/agent-smith.chatmode.md  GitHub Copilot chat mode
```

Skills are **linked**, not copied, so `git pull` updates the live install with
nothing else to run. The persona is copied, because harnesses read it at startup
and a stable copy is easier to reason about than a file that changes mid-session.

Check any time:

```bash
awino install-status
```

---

## Pointing a harness at the persona

A.W.I.N.O.'s persona is one Markdown file. If your tool is not auto-detected, install it
by hand.

### Automatic, for a specific harness

```bash
awino install --harness claude      # ~/.claude/
awino install --harness agents      # ~/.agents/
awino install --harness kilo        # ~/.config/kilo/
awino install --harness copilot     # VS Code user prompts directory
awino install --harness cursor      # ~/.cursor/rules/, frontmatter adapted
```

Add `--scope project` to install into the current repository instead of your home
directory. Use that only when a team must pin one exact version: a single shared
A.W.I.N.O. cannot fork its own knowledge base, and per-project copies will.

### Manual, for any tool that reads a system prompt

The persona is `agents/agent-smith.md` in the clone. Point your tool at it:

| Tool | Where the file goes |
| --- | --- |
| Claude Code | `~/.claude/agents/agent-smith.md` |
| Claude Projects | paste the file body into project instructions |
| Cursor | `.cursor/rules/agent-smith.mdc`, needs `alwaysApply` frontmatter |
| Continue, Cline, Roo | the custom-mode or persona field in settings |
| OpenAI Assistants | the `instructions` field |
| Anything with a system prompt | paste the body, or reference the path |

`awino install --harness cursor` rewrites the frontmatter for you. For everything
else the body is portable as-is.

### Pointer, for a project that should route to A.W.I.N.O.

The lightest option: three lines in `AGENTS.md` or `CLAUDE.md` that make A.W.I.N.O.
discoverable without copying anything.

```bash
awino pointer                 # print the block
awino install --pointer       # append it to AGENTS.md or CLAUDE.md
```

Prefer this over copying A.W.I.N.O. into a project. Copying forks its knowledge base;
a pointer does not.

---

## First five minutes

```bash
awino context     # what A.W.I.N.O. thinks home and project are
awino mission     # what A.W.I.N.O. thinks this project is for
awino doctor      # health, with a remedy per finding
awino skills      # what is available
awino work        # tracked work, if a tracker exists
```

Run `awino context` **first, in every new repository.** Every gate command A.W.I.N.O.
records comes from that resolution, so a wrong answer there makes every later green
gate meaningless.

Then ask your agent a real question:

```
what is a harness?
```

It should cite specific chapter paths and open at most three knowledge files.

---

## First real task

```bash
awino plan "add retry logic to the fetch client"
```

That reports which rung the request actually sits on, where the binding constraint
is, how much autonomy the current verification supports, and whether fanning out to
parallel agents is justified yet.

Then work under a gate:

```bash
awino gate open code-change "add retry logic" --scope src/client.py
awino gate record tested --cmd "<your test command>"
awino gate record linted --cmd "<your lint command>"
awino gate check --diff-base HEAD
awino gate close
```

`awino gate close` refuses unless every gate holds. You will not be able to report
the work as complete until it passes, which is the point.

A.W.I.N.O. discovers your project's real commands, so you do not have to use its
toolchain:

```bash
awino context     # shows the detected install, lint, and test commands
```

---

## Updating

```bash
cd ~/dev/awino
git pull
uv sync --all-groups     # only if dependencies changed
awino doctor             # confirm nothing broke
awino update             # refresh knowledge against upstream
```

Skills update automatically via the links. `awino doctor` after a pull is the
difference between believing the update worked and knowing it did.

---

## Troubleshooting

**`uv: command not found` after the bootstrap**
The installer edits `PATH` for future shells. Open a new terminal, or
`export PATH="$HOME/.local/bin:$PATH"`.

**Symlinks fail on Windows**
A.W.I.N.O. falls back to a directory junction, then to a copy. A copy needs
`awino install` re-run after each `git pull`; the output tells you which you got.

**The deprecated compatibility command is not recognized**
It lives in the clone's environment. Use `uv run awino ...` from the clone, or
`uv tool install --editable .` to expose it globally.

**`No global harness directories found`**
No supported tool is installed yet. Name one explicitly with
`awino install --harness claude`, which creates the directory.

**The doctor fails right after install**
Run `awino fix`. It repairs what is mechanically fixable and reports what needs
judgement. Anything left is a real problem with a stated remedy.

**A.W.I.N.O. thinks the wrong directory is the project**
Set `AWINO_PROJECT=/path/to/project`, or run from inside the project. Confirm with
`awino context`.

