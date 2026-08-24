# Distribution

How to share A.W.I.N.O., and why it is packaged this way.

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
links A.W.I.N.O. into the agent harness, and verifies the result. No pip, no global
Python packages, no version conflicts with anything already installed.

---

## Canonical package

The canonical distribution is `awino-harness`. It ships both the `awino` command
and the deprecated `smith` compatibility alias, plus the bundled registry, skills,
agents, hooks, and templates. A source clone remains the recommended editable
installation; a wheel is the reproducible isolated-tool installation.

| Concern | pip | git + uv |
| --- | --- | --- |
| Installs into which Python? | whichever is on PATH, often the system one | its own isolated `.venv` |
| Dependency conflicts | shares the user's site-packages | fully isolated |
| Ships the knowledge registry? | needs package-data configuration | it is just files in the repo |
| Ships skills and hooks? | awkward, they are not Python | they are files in the repo |
| Updating | `pip install -U`, loses local edits | `git pull`, keeps your lessons |
| Contributing a fix | fork, build, publish, wait | edit the clone, it is already live |
| Publishing overhead | PyPI account, name, release process | `git push` |

The deciding factor is that **A.W.I.N.O. is not primarily a Python library.** The
package therefore treats its non-Python bundle as required runtime data rather
than optional package data.

A second factor: **A.W.I.N.O. is meant to be edited.** Its lessons ledger accumulates
your project's rules. A pip install is a read-only artifact that a reinstall
replaces; a clone is a working copy that `git pull` updates while keeping what you
added.

```bash
uv tool install awino-harness
awino route "what is a harness"
```

---

## What a recipient gets

```
~/dev/awino/              the clone, editable, updated with git pull
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
verification. Use this when sending A.W.I.N.O. to a colleague.

```powershell
irm https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.ps1 | iex
```

Optional environment variables: `AWINO_DIR` for the clone location, `AWINO_REPO`
for a fork.

### 2. Clone and install, for someone who already has git and uv

```bash
git clone https://github.com/Lukematic/agent-smith.git
cd awino
./install.sh        # or ./install.ps1 on Windows
```

### 3. Vendored, for a team that wants A.W.I.N.O. pinned inside one repository

```bash
cd your-project
git submodule add https://github.com/Lukematic/agent-smith.git .smith
cd .smith && ./install.sh --local
```

`--local` installs to `./.agents/` instead of `~/.agents/`, so the install travels
with the repository. Use this when a team must share one A.W.I.N.O. version exactly;
otherwise prefer the global install, since one shared A.W.I.N.O. avoids
`KNOWLEDGE_FORK`.

---

## Before you publish

```bash
just check        # lint, tests, artifact validation, clutter
just doctor       # every project gate
uv build          # canonical wheel and sdist
uvx twine check dist/*
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

## Release checklist

- [ ] Set one version in `pyproject.toml`; confirm `awino --version` and package metadata agree.
- [ ] Run `just check`, `uv build`, and `uvx twine check dist/*`.
- [ ] Inspect the wheel and sdist for registry, skills, agents, hooks, and templates.
- [ ] Install the wheel into an empty temporary environment and smoke canonical `awino` plus the deprecated compatibility command.
- [ ] Run `awino update-preflight --no-pull`; retain the printed `BACKUP` path.
- [ ] Prove rollback with `awino rollback <BACKUP>` against temporary project/harness roots.
- [ ] Confirm canonical docs and installers use `awino`; only compatibility docs use `smith`.
- [ ] Tag only after the artifact and isolated-install checks pass.
- [ ] Publish through Trusted Publishing; never claim publication from a local build.

## Publishing

```bash
git init
git add -A
git commit -m "A.W.I.N.O.: agentic-engineering harness with a gate ledger"
gh repo create awino --public --source=. --push
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
cd ~/dev/awino
git pull
uv sync --all-groups     # only if dependencies changed
awino update-preflight   # backup, then fast-forward only
awino doctor             # confirm nothing broke
awino update             # refresh the knowledge registry against upstream
```

`awino doctor` after a pull is the important step. It is the difference between
believing the update worked and knowing it did.

If user-owned state needs restoration, use the exact path printed by preflight:

```bash
awino rollback ~/.smith/backups/<timestamp>  # project state only
# Add --include-harness only when intentionally restoring detected editor config.
```

---

## What a recipient should do first

```bash
awino context     # what A.W.I.N.O. thinks home and project are
awino mission     # what A.W.I.N.O. thinks the project is for
awino doctor      # health, with remedies
awino work        # tracked work, if a tracker exists
```

`awino context` first, always. Every gate command A.W.I.N.O. records comes from that
resolution, so a wrong answer there makes every later green gate meaningless.

