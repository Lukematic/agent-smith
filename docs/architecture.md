# A.W.I.N.O. — Design Plan

**Status:** v0.3 — multi-harness agent package
**Owner:** you
**Upstream knowledge source:** `jayminwest/agentic-engineering-book` (updated ~daily)
**Deployment:** see [DEPLOYMENT.md](DEPLOYMENT.md) — install once globally, never per-repo

---

## 1. The one-sentence thesis

A.W.I.N.O. is **not a knowledge dump**. It is a *harness* with a **registry** of
where knowledge lives, a **fetcher** that pulls just-in-time, a **memory** that
records what was actually learned, **loops** that match the shape of the problem,
and a **factory** that emits new agents, skills, and tools.

The failure mode we are explicitly designing against is **context bloat**:
copying 82 chapter files into a prompt. The book itself names the fix —
progressive disclosure (ch. 4/7) and harness engineering (ch. 6.5): make the
mistake structurally impossible rather than warning against it in prose.

---

## 2. Five layers

```
┌─ L5  LOOPS     ── direct | RPI | Ralph | delegate — chosen per problem shape
├─ L4  FACTORY   ── authors agents, skills, and tools; lint blocks bad output
├─ L3  MEMORY    ── Memory MCP for recall + file ledger for audit (dual-write)
├─ L2  RETRIEVAL ── topic -> registry -> fetch raw.githubusercontent -> cache
└─ L1  CONSTITUTION ── AWINO.md: harness-over-prompt, named failure modes,
                       spec-as-contract, tool restriction, cost awareness
```

L1 always loaded. L2–L5 progressive: metadata always, bodies on demand.

---

## 3. Folder contract

The folder contains a portable persona, model-invoked skills, deterministic CLI,
and an Open Plugin manifest for compatible harnesses.

```
.smith/
  plugin.json               # Open Plugin manifest
  AWINO.md                  # L1 canonical constitution — always loaded
  AGENT_SMITH.md            # deprecated compatibility pointer
  PLAN.md                   # this file
  DEPLOYMENT.md             # global install, per-repo footprint, loop selection
  agents/
    agent-smith.md          # the persona — installs to ~/.agents/agents/ SEPARATELY
  hooks/
    hooks.json              # SessionStart staleness guard
  knowledge/
    SOURCES.yaml            # upstream repos + refresh policy
    REGISTRY.yaml           # topic -> chapter path index (the routing table)
    MANIFEST.json           # what was fetched, when, which sha
    DRIFT.md                # generated drift report
    cache/                  # fetched markdown, gitignored
  skills/                   # 10 skills, indexed in docs/skills.md by `awino fix`
    awino-bootstrap/        # first run: scaffold + verify
    awino-consult/          # answer a concept question
    awino-triage/           # failure-mode diagnosis (ch. 11.2)
    awino-rpi/              # research -> plan -> implement
    awino-ralph/            # fresh-context iteration + cross-model review
    awino-delegate/         # parallel subagents, disjoint file ownership
    awino-memory/           # dual-write memory discipline
    awino-author-agent/     # emit an agent
    awino-author-tool/      # skill vs hook vs script vs recipe vs MCP gate
    awino-self-update/      # refresh registry, report lesson drift
  memory/
    lessons.md              # binding prevention rules (append-only)
    expertise/              # <domain>.jsonl records
    SESSION_LOG.md          # history + three-strikes tracking
  templates/
    agent.md.tmpl
  src/smith/                # the deterministic half: anything a script does reliably
    cli.py                  # every command, typer
    knowledge.py            # fetch, cache, provenance, drift, routing, budget
    validate.py             # artifact-aware validator with PASS/WARN/SKIP/FAIL
    enforce.py              # the gate ledger: completion is computed
    health.py               # project gates: uv, just, ruff, docs, seeds, skills
    fix.py                  # safe auto-repair, judgement calls reported
    tidy.py                 # clutter detection and reversible archiving
    paths.py                # one source of truth for layout
  tests/                    # proves the gates actually block
  install.ps1 / install.sh  # one command from clone to working install
  justfile                  # discoverable command surface
  pyproject.toml            # uv deps, ruff, pytest
  specs/                    # spec-as-contract output
  emitted/                  # staged artifacts awaiting human promotion
```

Rule: **nothing else** goes here. Findings go to the target repo
(`thoughts/`, `.seeds/`). A.W.I.N.O. owns its own house only.

---

## 4. Phased build

| Phase | Deliverable | Gate | Status |
| --- | --- | --- | --- |
| P0 | folder + constitution + registry + fetcher | answers "what is a harness?" fetching exactly 1 file | **done** |
| P1 | consult + triage skills | a vague complaint becomes a named mode + surface | **done** |
| P2 | memory dual-write (MCP + ledger) | a lesson in session N changes behavior in N+1 | **done** (9 lessons) |
| P3 | author-agent / author-tool + lint | emitted artifact passes lint; bad artifact blocks | **done** (8/8 caught) |
| P4 | loops: RPI, Ralph, delegate | right loop chosen and declared before work starts | **done** |
| P5 | global multi-harness install | personas, skills, and modes install per detected harness | **done** |
| P6 | first real repo adoption | project pointer; RPI research on a live task | **done** |
| P7 | Seeds integration | optional worklist integration and evidence-backed closure | **done** |

Stop after any phase. Each is independently useful — build a room, not the house.

---

## 5. Anti-bloat mechanics (the load-bearing part)

1. **Registry, not corpus.** ~82 entries of title + path + tags + `use_when`.
   That is the entire always-available index. Bodies are never preloaded.
2. **Fetch budget.** Max 3 chapter files per task. Ambiguous routing → ask, do
   not fetch ten.
3. **Cache with provenance.** Every fetch stamps `{path, sha, fetched_at}` into
   `MANIFEST.json`. Fresh cache is reused free; stale is refetched.
4. **Distillation, not accumulation.** What survives is a line in
   `memory/expertise/<domain>.jsonl`, not chapter text. Cache is disposable.
5. **Memory entries are one line.** Memory MCP rides in every prompt. Anything
   longer lives in a file and the entry points at the path.
6. **Appendices at directory granularity.** 111 nested example configs are a
   browse-on-demand corpus entered via `_index.md`, not 111 registry entries.

---

## 6. Loop selection

```
Confined to 1-2 files and well understood?  ── yes ─► direct edit
Understand the code well enough to plan?    ── no  ─► RPI research, then stop
Machine-checkable gate + many attempts?     ── yes ─► Ralph
Single ordered pass?                        ── yes ─► RPI plan + implement
Independent disjoint-file workstreams?      ── yes ─► delegate
```

They compose: RPI research → RPI plan → **Ralph the implement phase**, so each
attempt gets fresh context and a second model reviews it.

---

## 7. Constitution highlights (enforced text in AWINO.md)

- **Harness over prompt.** Change the system, not the wording. Prompt patches are
  logged as debt, not fixes.
- **Named failure modes.** "The agent is bad" is rejected as a diagnosis.
- **Tool restriction as forcing function.** Orchestrators get
  `Task, Read, Glob, TodoWrite` and nothing else.
- **Spec as contract.** No implementation before an approved spec.
- **Propulsion.** After approval, execute — do not re-summarize.
- **Verify before done.** Paste the command and its real output.
- **Cost awareness.** Fewest agents that produce useful parallelism.

---

## 8. Resolved decisions

| Question | Decision |
| --- | --- |
| Network access | fetch freely into `cache/`; no other network |
| Where emitted agents go | `emitted/` staging; human promotes to `~/.agents/agents/` |
| Refresh cadence | `--auto-update` on the plugin + staleness warning after 14 days |
| Per-repo install | **no by default** — one global install; repos get a minimal harness-appropriate pointer |
| Memory store | both: Memory MCP for recall, file ledger for audit; file wins |




