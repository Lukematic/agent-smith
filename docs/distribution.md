# Distribution

How to share Agent Smith, and why it is packaged this way.

---

## The short answer

Push to GitHub. Recipients run one line:

```powershell
irm https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.ps1 | iex
```

```bash
curl -fsSL https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.sh | sh
```

That clones the repo, installs `uv` if missing, creates an isolated environment,
links Smith into the agent harness, and verifies the result. No pip, no global
Python packages, no version conflicts with anything already installed.

---

## Why not pip

`pip install agent-smith` looks like the obvious answer and is the wrong one here.

| Concern | pip | git + uv |
| --- | --- | --- |
| Installs into which Python? | whichever is on PATH, often the system one | its own isolated `.venv` |
| Dependency conflicts | shares the user's site-packages | fully isolated |
| Ships the knowledge registry? | needs package-data configuration | it is just files in the repo |
| Ships skills and hooks? | awkward, they are not Python | they are files in the repo |
| Updating | `pip install -U`, loses local edits | `git pull`, keeps your lessons |
| Contributing a fix | fork, build, publish, wait | edit the clone, it is already live |
| Publishing overhead | PyPI account, name, release process | `git push` |

The deciding factor is that **Smith is not primarily a Python library.** It is a
knowledge registry, ten skills, a persona, hooks, and a CLI. The Python part is the
smallest part. Packaging the whole thing as a wheel to deliver a directory of
Markdown is the wrong shape.

A second factor: **Smith is meant to be edited.** Its lessons ledger accumulates
your project's rules. A pip install is a read-only artifact that a reinstall
replaces; a clone is a working copy that `git pull` updates while keeping what you
added.

`uv tool install` is the closest pip-shaped option and remains reasonable for the
CLI alone, but it still would not deliver `knowledge/`, `skills/`, or `memory/`.

---

## What a recipient gets

```
~/dev/agent-smith/              the clone, editable, updated with git pull
  .venv/                        isolated environment, never touches system Python

~/.agents/plugins/agent-smith   symlink to the clone, so skills load
~/.agents/agents/agent-smith.md the persona
```

A symlink rather than a copy is deliberate: `git pull` then updates the live
install with nothing else to run. That is the same reason the plugin manifest
supports `--auto-update`.

---

## The three install paths

### 1. Bootstrap script, for someone who has nothing

Handles git clone, uv installation, environment setup, harness linking, and
verification. Use this when sending Smith to a colleague.

```powershell
irm https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.ps1 | iex
```

Optional environment variables: `SMITH_DIR` for the clone location, `SMITH_REPO`
for a fork.

### 2. Clone and install, for someone who already has git and uv

```bash
git clone https://github.com/Lukematic/agent-smith.git
cd agent-smith
./install.sh        # or ./install.ps1 on Windows
```

### 3. Vendored, for a team that wants Smith pinned inside one repository

```bash
cd your-project
git submodule add https://github.com/Lukematic/agent-smith.git .smith
cd .smith && ./install.sh --local
```

`--local` installs to `./.agents/` instead of `~/.agents/`, so the install travels
with the repository. Use this when a team must share one Smith version exactly;
otherwise prefer the global install, since one shared Smith avoids
`KNOWLEDGE_FORK`.

---

## Before you publish

```bash
just check        # lint, tests, artifact validation, clutter
just doctor       # every project gate
```

Then confirm the sharing-specific concerns:

- [ ] `memory/lessons.md` contains only rules you are willing to publish
- [ ] `state/` and `knowledge/cache/` are gitignored, since both are local
- [ ] No absolute paths from your machine in tracked files
- [ ] `README.md` names the repository URL a recipient will actually clone
- [ ] `git status` is clean

Check for leaked local paths:

```bash
git grep -n "C:\\\\Users" -- ':!*.lock' || echo "clean"
```

---

## Publishing

```bash
git init
git add -A
git commit -m "Agent Smith: agentic-engineering harness with a gate ledger"
gh repo create agent-smith --public --source=. --push
```

Recipients then use the bootstrap line at the top of this document.

Tag releases so a team can pin one:

```bash
git tag -a v0.3.0 -m "Gate ledger, mental models, FAIR docs, seeds integration"
git push --tags
```

---

## Updating an install

```bash
cd ~/dev/agent-smith
git pull
uv sync --all-groups     # only if dependencies changed
smith doctor             # confirm nothing broke
smith update             # refresh the knowledge registry against upstream
```

`smith doctor` after a pull is the important step. It is the difference between
believing the update worked and knowing it did.

---

## What a recipient should do first

```bash
smith context     # what Smith thinks home and project are
smith mission     # what Smith thinks the project is for
smith doctor      # health, with remedies
smith work        # tracked work, if a tracker exists
```

`smith context` first, always. Every gate command Smith records comes from that
resolution, so a wrong answer there makes every later green gate meaningless.

