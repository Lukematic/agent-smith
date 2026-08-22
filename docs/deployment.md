# Deployment

Install Agent Smith **once at user scope**. Each repository gets project-local
intent, run state, and an optional pointer—not a copied knowledge base.

This prevents `KNOWLEDGE_FORK`: fixing a lesson or updating the registry once must
improve every project that uses Smith.

---

## Global versus project-local

| Artifact | Scope | Why |
| --- | --- | --- |
| Persona, skills, constitution | global/user | one maintained behavior source |
| Knowledge registry and cache | global/user | the same source corpus everywhere |
| Foundational agent lessons | global/user | universal harness rules |
| Confirmed project intent | project `.smith/project.yaml` | mission differs per repository |
| Project conventions and failures | project `.smith/memory/` | local, not doctrine |
| Run ledger and Ralph state | project `.smith/` | belongs to the work |
| Seeds worklist | project `.seeds/` | issues belong to the repository |
| RPI research and plans | project `thoughts/` | describes the codebase |

**Rules and knowledge are shared; findings, decisions, and worklists are local.**

---

## Install once

```powershell
irm https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.ps1 | iex
```

```bash
curl -fsSL https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.sh | sh
```

Or clone and inspect first:

```bash
git clone https://github.com/Lukematic/agent-smith.git
cd agent-smith
./install.sh          # .\install.ps1 on Windows
```

The installer detects and adapts to supported harnesses:

| Harness | Installed artifact |
| --- | --- |
| Claude Code | agent persona plus linked skills |
| Kilo | primary persona, linked skills, selectable modes |
| Roo/Zoo | selectable custom modes |
| GitHub Copilot | VS Code chat mode |
| compatible open-agent harness | agent plus plugin |
| Cursor | adapted rule |

Check what was found:

```bash
smith install-status
smith mode-status
```

---

## First command in a project

```bash
smith onboard
```

It:

1. resolves Smith home versus project root;
2. reads project instructions, metadata, README, non-goals, and current tracker;
3. detects toolchain, lint, test, and install commands;
4. reflects a mission draft with its source/confidence;
5. asks one unresolved frontier question at a time;
6. optionally offers Seeds initialization;
7. persists confirmed intent to `.smith/project.yaml`.

Example:

```bash
smith onboard --set primary_user="working scientists reviewing literature"
smith onboard --set goals="search open indexes; inspect evidence; cited synthesis"
smith onboard --set tenets="no claim without source IDs; no credentials on disk"
smith onboard --set expectations="free hosting; simple web UI; BYOK or local model"
smith onboard --set success_metric="a scientist can review and trace every claim"
smith onboard --confirm
```

Then:

```bash
smith plan "build the first useful slice"
```

---

## Minimal project pointer

A repository may add a short pointer to `AGENTS.md`, `CLAUDE.md`, `.goosehints`,
or the harness-specific equivalent:

```bash
smith pointer
```

The pointer says where Smith is installed and that agentic-engineering tasks should
route to it. It does not copy the persona, skills, registry, or memory.

---

## Manual fallback

If a harness is not detected:

1. Open `agents/agent-smith.md` in the clone.
2. Copy the body after YAML frontmatter.
3. Paste it into the product's system prompt, custom mode, persona, agent
   instructions, or project instructions field.
4. Grant only the tools appropriate to the intended role.

The Markdown body is portable. Frontmatter and permission schemas are not, which
is why `smith install` rebuilds them per harness.

---

## Update

```bash
cd <your-agent-smith-clone>
git pull
uv sync --all-groups
smith install --overwrite
smith install-mode --force
smith doctor
smith update
```

Linked skills update with the clone. Personas and mode files are regenerated to
keep their harness-specific frontmatter current.

---

## Memory policy

When Memory MCP is available, Smith may dual-write short recall records to MCP and
append-only files. The files are the audit ledger and win on disagreement.

Categories:

| Category | Scope | Example |
| --- | --- | --- |
| agent doctrine | global | orchestrators do not receive implementation tools |
| failure modes | global | named recurrence pattern |
| book routing | global | topic-to-chapter route |
| project conventions | project | this repo verifies with `uv run pytest` |
| project failures | project | this pipeline loses source IDs at normalization |

Scientific/domain conclusions are never promoted into global doctrine. They remain
source-grounded project evidence requiring domain review.

---

## Which loop to use

```text
1–2 well-understood files                 -> direct
Mission or user outcome unclear           -> smith-discover / smith onboard
Codebase not understood                   -> smith-rpi research, then stop
Clear machine-checkable gate, many tries  -> smith-ralph
Independent disjoint workstreams          -> smith-delegate
Research/RAG factual synthesis            -> smith-evidence
Scientific/data pipeline                  -> smith-reproducibility
```

These compose. A typical research-product sequence is:

```text
onboard -> discover -> RPI research -> plan interrogation -> approved spec
        -> implementation -> deterministic tests -> evidence gate
        -> independent verification -> project lesson
```
