# Install

From a GitHub URL to a working agent, and what to do first.

---

## One line

**Windows (PowerShell 7+)**

```powershell
irm https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.ps1 | iex
```

**macOS or Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.sh | sh
```

That clones the repository, installs `uv` if missing, builds an isolated
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
cd agent-smith

./install.sh              # or ./install.ps1 on Windows
```

Which does:

| Step | What happens | Verified by |
| --- | --- | --- |
| 1 | install `uv` if absent | `uv --version` |
| 2 | build `.venv` from the lockfile | `uv sync --frozen` |
| 3 | regenerate derived files | `smith fix` |
| 4 | install persona and skills per harness | `smith install-status` |
| 5 | run every project gate | `smith doctor` |
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
runs as `uv run smith ...`. Seeds is an optional issue tracker; Smith never
installs or initializes it without being asked.

---

## Where it lands

```
~/dev/agent-smith/               the clone, editable, updated with git pull
  .venv/                         isolated environment

~/.claude/agents/agent-smith.md  persona, if Claude Code is present
~/.claude/skills/smith-*/        skills, junctioned to the clone
~/.agents/agents/agent-smith.md  persona, if Goose is present
~/.agents/plugins/agent-smith/   the whole plugin, linked
~/.kilo/agent/agent-smith.md     persona, if Kilo is present
~/.kilo/skills/smith-*/          skills, linked
```

Skills are **linked**, not copied, so `git pull` updates the live install with
nothing else to run. The persona is copied, because harnesses read it at startup
and a stable copy is easier to reason about than a file that changes mid-session.

Check any time:

```bash
smith install-status
```

---

## Pointing a harness at the persona

Smith's persona is one Markdown file. If your tool is not auto-detected, install it
by hand.

### Automatic, for a specific harness

```bash
smith install --harness claude      # ~/.claude/
smith install --harness agents      # ~/.agents/
smith install --harness kilo        # ~/.kilo/
smith install --harness cursor      # ~/.cursor/rules/, frontmatter adapted
```

Add `--scope project` to install into the current repository instead of your home
directory. Use that only when a team must pin one exact version: a single shared
Smith cannot fork its own knowledge base, and per-project copies will.

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

`smith install --harness cursor` rewrites the frontmatter for you. For everything
else the body is portable as-is.

### Pointer, for a project that should route to Smith

The lightest option: three lines in `AGENTS.md` or `CLAUDE.md` that make Smith
discoverable without copying anything.

```bash
smith pointer                 # print the block
smith install --pointer       # append it to AGENTS.md or CLAUDE.md
```

Prefer this over copying Smith into a project. Copying forks its knowledge base;
a pointer does not.

---

## First five minutes

```bash
smith context     # what Smith thinks home and project are
smith mission     # what Smith thinks this project is for
smith doctor      # health, with a remedy per finding
smith skills      # what is available
smith work        # tracked work, if a tracker exists
```

Run `smith context` **first, in every new repository.** Every gate command Smith
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
smith plan "add retry logic to the fetch client"
```

That reports which rung the request actually sits on, where the binding constraint
is, how much autonomy the current verification supports, and whether fanning out to
parallel agents is justified yet.

Then work under a gate:

```bash
smith gate open code-change "add retry logic" --scope src/client.py
smith gate record tested --cmd "<your test command>"
smith gate record linted --cmd "<your lint command>"
smith gate check --diff-base HEAD
smith gate close
```

`smith gate close` refuses unless every gate holds. You will not be able to report
the work as complete until it passes, which is the point.

Smith discovers your project's real commands, so you do not have to use its
toolchain:

```bash
smith context     # shows the detected install, lint, and test commands
```

---

## Updating

```bash
cd ~/dev/agent-smith
git pull
uv sync --all-groups     # only if dependencies changed
smith doctor             # confirm nothing broke
smith update             # refresh knowledge against upstream
```

Skills update automatically via the links. `smith doctor` after a pull is the
difference between believing the update worked and knowing it did.

---

## Troubleshooting

**`uv: command not found` after the bootstrap**
The installer edits `PATH` for future shells. Open a new terminal, or
`export PATH="$HOME/.local/bin:$PATH"`.

**Symlinks fail on Windows**
Smith falls back to a directory junction, then to a copy. A copy needs
`smith install` re-run after each `git pull`; the output tells you which you got.

**`smith` is not recognized**
It lives in the clone's environment. Use `uv run smith ...` from the clone, or
`uv tool install --editable .` to expose it globally.

**`No global harness directories found`**
No supported tool is installed yet. Name one explicitly with
`smith install --harness claude`, which creates the directory.

**The doctor fails right after install**
Run `smith fix`. It repairs what is mechanically fixable and reports what needs
judgement. Anything left is a real problem with a stated remedy.

**Smith thinks the wrong directory is the project**
Set `SMITH_PROJECT=/path/to/project`, or run from inside the project. Confirm with
`smith context`.

