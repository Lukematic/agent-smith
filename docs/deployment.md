# DEPLOYMENT — how Agent Smith spans repos

**The question:** do I drop `.smith/` into every repo?

**The answer:** No. Copying it per-repo gives you N diverging forks of the same
knowledge harness. You would fix a lesson in repo A and never see it in repo B.
That is `KNOWLEDGE_FORK`, and it is the exact failure the registry exists to
prevent.

Install Smith **once, globally, as a git-backed Goose plugin with auto-update**.
Each repo gets a ~15-line pointer file, not a copy.

---

## 1. The split: what is global vs per-project

| Thing | Scope | Why |
| --- | --- | --- |
| Smith's skills | **global** (plugin) | one source of truth, auto-updated |
| Smith's constitution | **global** (plugin) | rules should not fork per repo |
| Knowledge registry + cache | **global** (plugin) | the book is the same book everywhere |
| Foundational lessons | **global** memory | "orchestrators get no Bash" is universal |
| Project conventions | **per-project** memory | "this repo uses uv, not pip" |
| Worklist (seeds) | **per-project** | issues belong to the repo |
| RPI research/plans | **per-project** (`thoughts/`) | they describe *this* codebase |
| Ralph state | **per-project** (`.goose/ralph/`) | run-scoped |

The rule: **rules and knowledge are global; findings and worklists are local.**

---

## 2. Global install (once)

Smith's folder *is* a valid Goose Open Plugin — `plugin.json` at the root,
`skills/`, `hooks/hooks.json`.

```bash
# publish .smith/ as its own git repo once, then:
goose plugin install --auto-update https://github.com/Lukematic/agent-smith.git
```

That lands it at `~/.agents/plugins/agent-smith/` and namespaces every skill:

```
agent-smith:smith-consult
agent-smith:smith-triage
agent-smith:smith-rpi
agent-smith:smith-ralph
agent-smith:smith-delegate
agent-smith:smith-author-agent
agent-smith:smith-author-skill
agent-smith:smith-author-tool
agent-smith:smith-self-update
agent-smith:smith-memory
```

`--auto-update` means goose re-pulls before plugin skills load (rate-limited, so
not every session). Your daily-updated book knowledge harness stays current
without you doing anything.

Until it is a git repo, symlink for local dev:

```powershell
New-Item -ItemType SymbolicLink `
  -Path "$HOME\.agents\plugins\agent-smith" `
  -Target "<your-path>"
```

Symlinked/copied plugins are discovered but **not** managed by
`goose plugin update` — you run `smith-self-update` manually instead.

### The custom agent is separate

Plugins carry skills and hooks only. The Smith *persona* is a custom agent and
must be installed on its own:

```powershell
Copy-Item "$HOME\.agents\plugins\agent-smith\agents\agent-smith.md" `
          "$HOME\.agents\agents\agent-smith.md"
```

Then in any session: `@agent-smith what is a harness?` or
`Delegate to agent-smith: triage why my builder keeps writing outside its scope.`

---

## 3. Per-project footprint (tiny, by design)

In each repo you work in, exactly two things:

**a) `.goosehints`** — three lines that make Smith discoverable in context:

```text
Agentic-engineering questions, agent authoring, and agent debugging go to the
agent-smith agent (@agent-smith). Its skills are namespaced agent-smith:*.
Project-specific conventions live in .goose/memory/; never edit the global plugin from here.
```

**b) `.agents/skills/project-context/SKILL.md`** — optional, only if the repo has
conventions Smith must respect (test command, package manager, forbidden dirs).

That is it. No registry copy. No skills copy. No constitution copy.

```
your-repo/
  .goosehints                       # 3 lines pointing at Smith
  .goose/memory/                    # project-local memory (Memory MCP)
  .goose/ralph/                     # ralph loop state, gitignored
  thoughts/{research,plans}/         # RPI outputs, committed
  .seeds/                           # worklist
  .agents/skills/project-context/    # optional local conventions
```

---

## 4. Memory: dual-write, two lifetimes

The Memory MCP extension is loaded into **every prompt** at session start. That
makes it the right place for *recall* and the wrong place for *audit*. So Smith
writes both:

| Store | Mechanism | Scope | Purpose |
| --- | --- | --- | --- |
| Memory MCP | `remember_memory(...)` | `is_global=true` for doctrine, `false` for project | fast recall, always in context |
| `memory/lessons.md` | file append | global plugin | append-only audit trail with dates and supersessions |
| `memory/expertise/*.jsonl` | file append | global plugin | structured records with classification |

Rule: **every `remember_memory` call is mirrored to a file.** Memory MCP is a
cache with no history; the files are the ledger. If they disagree, the file wins.

Categories Smith uses:

| Category | `is_global` | Example |
| --- | --- | --- |
| `agentic_doctrine` | true | orchestrators never get Write/Edit/Bash |
| `failure_modes` | true | a new named mode Smith coined |
| `book_routing` | true | "harness questions route to 6.1/6.5" |
| `project_conventions` | false | this repo verifies with `uv run pytest -q` |
| `project_failures` | false | the builder here keeps touching `src/legacy/` |

Cost control: Memory MCP content rides in every prompt. Keep entries one line.
Anything longer goes in a file and the memory entry points at the path — the docs
explicitly recommend this for large instructions.

---

## 5. Which loop for which problem

Smith's job is picking the right machine, not running everything through one.

```
Is the change confined to 1-2 files and well understood?
├─ yes ──► direct edit. No ceremony. (RPI here is overkill.)
└─ no
   │
   Do you understand the current code well enough to plan?
   ├─ no  ──► smith-rpi, research phase only. Stop. Review.
   └─ yes
      │
      Is there a machine-checkable completion signal (tests/build)?
      ├─ yes, and the work needs many attempts ──► smith-ralph
      │                                            (fresh context per iteration,
      │                                             cross-model review)
      └─ yes, and it is a single ordered pass ──► smith-rpi plan + implement
      │
      Are there independent workstreams with disjoint files?
      └─ yes ──► smith-delegate (parallel subagents)
```

| Loop | Use when | Do not use when |
| --- | --- | --- |
| **RPI** | complex, multi-file, spans layers; you need a reviewable plan | small changes — it is deliberately slow |
| **Ralph** | clear pass/fail gate; benefits from repeated attempts | exploratory work, no verifiable criteria |
| **Subagents** | independent parallel work, disjoint file ownership | sequential deps, same-file edits |
| **Direct** | one obvious change | anything you cannot hold in your head |

These compose: RPI research → RPI plan → **Ralph the implement phase** so each
attempt gets fresh context and a second model reviews it.

---

## 6. Rollout order

| Step | Action | Gate |
| --- | --- | --- |
| 1 | symlink plugin, copy the agent file | `@agent-smith` responds with a status header |
| 2 | ask "what is a harness?" | answers citing 6.1/6.5, opens ≤3 files |
| 3 | enable Memory MCP, store one doctrine entry | entry recalled in a fresh session |
| 4 | run `smith-self-update` | drift report shows 0 |
| 5 | pick **one** real repo, add `.goosehints` | Smith reads project memory, not global only |
| 6 | run RPI research on a real task in that repo | `thoughts/research/*.md` exists and is accurate |
| 7 | author one agent via `smith-author-agent` | lint passes, you promote it manually |
| 8 | publish to git, reinstall with `--auto-update` | `goose plugin update agent-smith` works |

Do not do step 5 in five repos at once. One repo, prove the loop, then fan out.

---

## 7. Guardrails against wild-west drift

| Risk | Guard |
| --- | --- |
| per-repo forks of Smith | one global plugin; repos get pointers only |
| stale knowledge | `--auto-update` + staleness warning after 14 days |
| context bloat | 3-file fetch ceiling, registry-only routing |
| memory sprawl | 5 fixed categories; one-line entries; files for anything longer |
| agent sprawl | reuse search mandatory before authoring; emitted to staging |
| tool sprawl | new tools only after the "why not a skill?" gate |
| silent breakage | `lint_agent.ps1` blocks on FAIL; hooks run it on write |
| lost lessons | append-only ledger; Memory MCP is a cache, files are truth |




