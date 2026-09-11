# A.W.I.N.O. — Design Plan

**Status:** v0.3 — multi-harness agent package
**Owner:** you
**Upstream knowledge source:** `jayminwest/agentic-engineering-book` (updated ~daily)
**Deployment:** see [deployment.md](deployment.md) — install once globally, never per-repo

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
├─ L3  MEMORY    ── file ledger for recall + audit (no MCP client; see below)
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
.awino/
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
  src/awino/                # the deterministic half: anything a script does reliably
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
5. **Memory entries are one line.** There is no MCP client: recall reads the
   file ledger directly. Anything longer lives in a file and the entry points
   at the path.
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
| Memory store | file ledger for recall and audit (no MCP client implemented) |

---

## 9. Working memory (the mind to the ledger's court record)

The ledger proves what happened; working memory holds what it means and
what's next. Five pieces, all deterministic, in project state (`.awino/`)
except the user model (`~/.awino/profile.yaml` — the human is not the
project):

- **Mission + success criteria** = the target (how we know we won vs. not).
- **Seeds** = the commitments.
- **Checklist** = the now. One item per loop, updated by the loop drivers at
  every phase boundary (advance, back, lock, close). `awino best` shows the
  compact brief — focus + blocked — never a dump.
- **Facts / decisions** = the understanding. Facts are append-only; a
  correction marks the old entry superseded with a dated pointer, never
  rewriting it. Every decision records its why; a missing why is invalid and
  buddy flags it. Pair-planning answers and plan approvals feed it
  automatically.
- **Ledger** = the proof.
- **User model** = the who. How this human works, learned from explicit
  evidence only: a small named set of deterministic rules (`RULE-*`),
  updated by outcome verdicts and session corrections. Fields the human set
  explicitly are never overwritten by a rule.
- **Buddy** = the auditor. Reads all of it and reports staleness and
  inconsistency; `--fix` applies mechanical corrections and prompts where
  judgment is needed, never inventing rationale.

Representation choice: the checklist is **JSON, not markdown**. The drivers
rewrite it at every phase boundary and the tests assert on parsed structure;
a markdown checklist would force every test to parse prose and every driver
to do string surgery. The human never reads the raw file — `awino best`
renders the brief. Facts and decisions stay markdown because humans do read
those, as append-only logs.

### Skill receipts

Skill usage is a gated, checkable step. When a phase's artifact validates,
the loop driver writes a receipt for every required skill:

`.awino/receipts/<loop-id>--<phase>--<skill>.json`

```json
{
  "skill": "awino-rpi",
  "version": "<skill SKILL.md content hash, 12 hex chars>",
  "phase": "research",
  "inputs_hash": "<sha256>",
  "output_artifact": "thoughts/research/2026-09-11-0800-auth.md",
  "artifact_hash": "<sha256 of the artifact bytes at validation time>",
  "timestamp": "<iso-8601>"
}
```

The honest write point is the driver, right after the phase artifact
validates — a handwritten receipt file alone proves nothing, because the
receipt only passes the gate when (a) its `inputs_hash` matches the phase's
current actual inputs and (b) its declared output artifact equals the
phase's artifact, exists, and — unless it is byte-identical to what
validated — passes that artifact's own validation at gate time.
`artifact_hash` lets the gate accept an unchanged artifact without
re-running the validator: some validators (ralph retry) record progress as
a side effect and are not idempotent, so a re-run on an unchanged artifact
would wrongly fail. When the artifact changed after the receipt was
written, the gate re-runs the artifact's own validator and reports its
problems.

**`inputs_hash`** is SHA-256 over canonical JSON (sorted keys, UTF-8) of
exactly the phase's consumed inputs: `artifact_path` (the repo-relative
phase artifact path), `criteria_hash` (the mission's live objective +
success criteria hash), and `seed_id` (the loop's linked seed id, or `""`
when none). A receipt is refreshed when the inputs moved (stale
`inputs_hash`) or the artifact's bytes changed (stale `artifact_hash`); an
unchanged receipt stands and emits no duplicate ledger event.

**Required skills** come from the pairing brief's `## Required skills`
section — one `- <phase>: <skill>[, <skill>...]` item per phase — and the
brief's validator rejects a missing section, names any phase missing a
declaration, and rejects skill names that resolve to no real skill (project
`<project>/skills/` first, then the bundled `skills/`). Loops
without a pairing brief (or non-RPI loops) fall back to the loop kind's own
skill (`awino-rpi`, `awino-ralph`, `awino-delegate`); phases that produce no
artifact (verify, assign, implement, controller-verify) require no receipts.

**The gate**: `advance()` refuses to leave a phase while any required skill
lacks a valid receipt, raising `ReceiptBlocked` with one problem per skill —
missing receipt (names the skill), malformed receipt file (names the skill),
stale `inputs_hash` (inputs moved since the attestation), an output artifact
that is not the phase's artifact or is missing (names the path), or an
output that fails its own validation (names the first failure). `loop next`
renders these as `REFUSED` lines naming the skill and the problem; the fix
is to re-validate the phase so the driver re-attests — receipts are written
by the driver, never by hand.

**The trail**: each written receipt emits a `skill_receipt` ledger event.
The checklist item's `"skills"` entry is the current phase's
`{"<skill>": "received|missing|invalid"}` (per-phase history accumulates
under `"skills_by_phase"`), and the compact brief shows the current phase's
line (e.g. `SKILLS  awino-rpi=received`).

**Buddy** audits completed phases for valid receipts and flags receiptless
ones. `buddy --fix` never writes a receipt — it re-arms the phase via the
driver's re-entry (active loops) or the dedicated re-open path (done
loops), so the skill step runs again and the driver's `check()` writes a
fresh receipt when the artifact validates. Forging one would make the audit
lie.

## 10. The spine (the owner's 10-step precondition chain)

Every loop kind runs on one ordered, enforced state machine — the spine.
`RpiDriver.SPINE` is a tuple of `SpineStep`s (name, artifact, check, refuse,
from_phases) in the owner's exact order:

1. **mission** — the mission file carries objective + success criteria
2. **pair-plan** — the written pair-plan (required-skills declarations,
   effort-marked approaches, one default recommendation)
3. **challenge** — thinking-mode output, or an explicit human waiver
4. **understand** — the comprehension record (explanation + answered probes)
5. **real-problem** — the applicability check with the user-confirmed problem
6. **honda-scope** — the Honda scope, approved (human approval)
7. **beyond-honda** — beyond-Honda options with effort labels, recorded
8. **capture** — boundary records (ledger trail, decisions, working memory)
9. **work** — executed work (terminal)
10. **outcome verdict** — yes/partial/no (terminal)

`advance()` evaluates the steps in owner order and refuses at the **first**
missing precondition, naming the missing artifact. Refusals reuse the
long-standing gate exceptions where they exist (`ProblemUnconfirmed`,
`PairingIncomplete`, `ApprovalRequired`, `ComprehensionRequired`) and raise
`SpineBlocked` otherwise. `spine_status()` reports every step as
ok/missing/pending in owner order; `awino loop status` renders it as the
`SPINE` section.

**Enforcement points.** Each step gates only the advance-FROM phases named
in its `from_phases`. `()` means universal: mission and capture are checked
on every advance (the mission is re-verified at every boundary; the trail
and checklist must show the current position). `None` means terminal:
work and verdict are enforced at their own boundary, never inside
`advance()` — work by the implement phase's own validation (RPI's gate
handoff), the verdict by `awino loop close`, which refuses without
`--verdict`.

**Tuple order vs. phase chronology.** The tuple is the owner's reasoning
order, which doubles as refusal priority (at plan, a missing thinking run
is named before a missing approval — challenge comes before Honda) and as
the status display order. The RPI phase flow (research → pair-plan → plan →
implement) is monotonic: the lawyer-move confirmation happens in research,
pair-planning in pair-plan, thinking/comprehension/approval in plan. A step
gated at an earlier phase stays satisfied afterward — you cannot reach
pair-plan without the mission, and you cannot reach plan without the brief —
so the chain holds end to end even though artifacts are produced in phase
order, not tuple order.

**Two closes, not one.** `awino loop next` completing the final phase is
*work-closure* (`loop_closed`, phase `done`): the work is handed off. `awino
loop close --verdict` is *outcome-closure* (`outcome_verdict`): the human
judges whether the goal was accomplished — a question that can only be
answered after the work exists, which is why the verdict is terminal and
post-hoc. A done loop with no verdict still owes step 10: `spine_status`
reports it `missing`, and the verdict is the only step `loop close` can
satisfy (it refuses without `--verdict`).

**Waivers are explicit, human-only, reasoned, and ledger-recorded.** The
machine never waives on its own. The thinking waiver (`awino loop approve
--waive-thinking --waive-reason "..."`) records a `thinking_waived` ledger
event naming the reason and a decisions.md entry keyed
`<loop-id>:thinking-waiver`. A waiver with no reason is not a conscious
decision: approval still demands thinking.

Base loop kinds (Ralph, Delegate) inherit the universal and terminal steps
— mission, capture, work, verdict — and declare their own middle steps;
RPI declares all ten.

## 11. Precedent: case law

Where a new decision is recorded, the machine surfaces what the human chose
last time and how it turned out — "last time you chose X because Z;
outcome was partial." Deterministic, advisory, never blocking, never
invented: no match means silence, and any failure inside matching is
swallowed so precedent can never break recording.

**Surfaced at the human decision points**, before the new entry lands in
decisions.md: pair-planning answers/defaults, thinking waivers, plan
approvals. The CLI prints them as `PRECEDENT` lines after the recording
echo. Buddy's backfill and think.py's run-logging are deliberately *not*
wired: backfill replays decisions already made (repair, not a pending
choice), and a thinking run-log is bookkeeping, not a choice.

**The matching rule** (`working_memory.find_precedents` — pure, same
inputs → same outputs):

1. Candidates are live, non-superseded decisions.md entries with
   non-empty decision text.
2. Area-scoped: the past entry must carry the lookup's decision area
   (pairing answers compare against past pairing answers — the key's
   suffix carries it: Q-ids → `pairing`, `approval` → `approval`,
   `thinking-waiver` → `thinking-waiver`). Area-less lookups fall back to
   the loop kind (key prefix `<kind>-<id>`).
3. At least two shared content keywords: lowercase alphanumerics of length
   ≥ 4 from the new decision's decision + why, minus stopwords.
4. The past decision's outcome is the latest `outcome_verdict` ledger event
   for the loop it was recorded for (`verdict: <yes|partial|no>` in the
   event detail), if any.
5. Ranked deterministically: more shared keywords first, then newer
   entries first, then lower decision ids. At most three precedents.

**The wording** (`working_memory.format_precedent`): `last time you chose
<decision> because <why>; outcome was <verdict>.` The outcome clause is
omitted when the past loop never closed with a verdict — no invented
outcomes, ever.




