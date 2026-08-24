# Third-Party Skills Review: obra/superpowers and Agent-Skills-for-Context-Engineering

Date: 2026-08-23
Reviewed by: A.W.I.N.O. session, at Luke's request
Sources reviewed directly (fetched, not assumed): obra/superpowers README, CLAUDE.md,
porting-to-a-new-harness.md, the 2026-01-22 document-review-system spec+plan, and
systematic-debugging/SKILL.md; muratcankoylan/Agent-Skills-for-Context-Engineering
README, skills-improvement-analysis.md, tool-design/SKILL.md, harness-engineering/SKILL.md.

## 1. Are they competitors?

**No, and treating them as competitors would be a category error.** Neither project
ships a CLI, a gate ledger, a mission/onboarding flow, or a multi-harness installer.
They ship **prompt content** (Markdown skill files) plus, for superpowers, a
**bootstrap-injection mechanism** per coding-agent harness (Claude Code, Codex,
Cursor, Gemini CLI, OpenCode, pi, etc.).

A.W.I.N.O. is closer to what their bootstrap injector plus CLI would look like if it
had a deterministic backend: Python code that actually runs commands, records real
exit codes, and refuses completion without evidence. Superpowers and
context-engineering are **closer to what A.W.I.N.O.'s skill library is** -
Markdown files describing procedures.

**The honest overlap is real, though**: both projects solve "how do I make an agent
actually follow a workflow instead of skipping it," which is exactly A.W.I.N.O.'s
core problem (`gate close` exists because prose instructions get skipped). They
solved a *different slice* of it - the harness bootstrap and skill-content
craftsmanship slice, not the deterministic-verification slice. That makes them
**complementary sources to learn from**, not something to compete with, and not
something to adopt wholesale into A.W.I.N.O.'s much smaller, code-backed skill set.

## 2. What is genuinely worth adopting

### 2.1 Document review loops (from superpowers' 2026-01-22 spec+plan)

Superpowers added a **spec-document-reviewer** and **plan-document-reviewer**: a
second subagent, dispatched with a fixed prompt template, that checks a
just-written spec or plan chunk against a table (Completeness, Coverage,
Consistency, Clarity, YAGNI for specs; Completeness, Spec Alignment, Task
Decomposition, Task Syntax, Chunk Size for plans) and returns `Approved` or `Issues
Found` plus advisory recommendations. The loop: write -> review -> fix -> re-review
-> approve, capped with "surface to human after 5 iterations" and "surface to
human after 3 iterations of the same disagreement."

**This maps directly onto A.W.I.N.O.'s existing gap.** `smith-discover` produces
`.smith/project.yaml`; `smith-rpi` produces `thoughts/plans/*.md`. Neither is
independently reviewed before implementation starts. Adding a `--review` step that
dispatches a second, differently-scoped subagent against the same rubric closes
that gap, and the 5-iteration/3-iteration disagreement caps are a concrete,
reusable version of a rule A.W.I.N.O. already has in spirit (three-strikes) but
had not written down for *document* review specifically, only for *gate*
retries.

**Decision: adapt, do not copy verbatim.** Superpowers' version assumes a `Task`
tool and Claude Code-shaped subagent dispatch. A.W.I.N.O. already has
`smith-delegate` and `spawn.py` for that. The adaptation is: reuse
`spawn.Assignment` with `Role.REVIEWER` (already read-only, already exists) to
dispatch the review, and record the verdict as a `planned` gate attestation with
the reviewer's verdict text, not as new infrastructure.

### 2.2 Systematic debugging's "Iron Law" and 3-strikes-to-architecture escalation

`skills/systematic-debugging/SKILL.md` states one thing A.W.I.N.O.'s own doctrine
gestures at but never enforced as a *phase gate*: "NO FIXES WITHOUT ROOT CAUSE
INVESTIGATION FIRST," and a **four-phase structure** (Root Cause -> Pattern Analysis
-> Hypothesis+Test -> Implementation) where **Phase 4 has an explicit escalation
rule**: if 3+ minimal fixes fail and each fix reveals a new problem in a different
place, that is evidence of an architectural problem, not a debugging problem, and
the agent must stop and discuss architecture with the human rather than attempting
fix #4.

A.W.I.N.O. has `THREE_STRIKES` in `healing.py` for *command* retries, but
`smith-triage` does not have this phase structure, and nothing currently
distinguishes "this specific fix didn't work, try another" from "the last three
fixes each revealed a new problem elsewhere, which means the architecture itself
is probably wrong."

**Decision: adopt, adapted to A.W.I.N.O.'s failure-mode vocabulary.** `smith-triage`
already produces a named `FAILURE_MODE` + surface + structural fix. Adding the
four-phase gate (require Phase 1 evidence before any fix is proposed) and the
"3 fixes, each revealing a new problem elsewhere -> stop, question architecture,
escalate to human" rule strengthens it without new infrastructure -
`healing.HealingRun` already tracks attempt history; it needs a check for "is the
failure signature different each time" to detect the architectural-problem
pattern, not just "did the same failure recur."

### 2.3 Tool-design's consolidation principle and MCP naming rule

`skills/tool-design/SKILL.md` states two concrete, testable rules A.W.I.N.O.'s own
`smith-author-tool` skill gestures at in prose but does not check mechanically:

1. **Consolidation principle**: "if a human engineer cannot definitively say which
   tool should be used in a given situation, an agent cannot be expected to do
   better." Overlapping tool descriptions cause selection errors as the catalog
   grows.
2. **MCP fully-qualified naming**: `ServerName:tool_name`, always - unqualified
   names fail silently across multiple MCP servers.

A.W.I.N.O.'s CLI *is* a tool catalog (34 commands). `validate.py` already checks
frontmatter shape; it does not check for description overlap across the skill
catalog, which is exactly the failure mode the consolidation principle names.

**Decision: adopt the audit checklist as a new `smith fix` / `smith doctor` check**,
not a new skill - this is a mechanical property of the existing skill/command
catalog, better enforced as code (matching A.W.I.N.O.'s own `MODEL_DOES_DETERMINISM`
principle) than as a skill a model reads and might skip.

### 2.4 Harness-engineering's four-surface model

`skills/harness-engineering/SKILL.md`'s **Locked / Editable / Append-only /
Human-controlled** surface taxonomy is a cleaner, more general restatement of
something `AWINO.md` §2.4 (tool restriction as forcing function) and
`enforce.py`'s gate contracts already encode implicitly, but never named this
explicitly for skill authors.

**Decision: adopt as documentation, not new code.** Add this exact four-way
taxonomy to `docs/harness.md` as the naming convention for what A.W.I.N.O. already
does (gates = locked feedback surface; `.smith/project.yaml` and lessons.md =
append-only; source files under `--scope` = editable; `git push`, credential
files, `sd close` = human-controlled). This is a documentation gap, not a feature
gap - no seed needed beyond the doc update, which is folded into this review doc.

### 2.5 Gotchas-section requirement (from skills-improvement-analysis.md)

The muratcankoylan repo's self-audit found that the single highest-signal missing
piece across their skill corpus was a **Gotchas section**: concrete, specific
failure modes a model actually hits, not theoretical ones. They measured going from
31% coverage to 100% coverage as the highest-impact low-effort change available.

A.W.I.N.O.'s 13 skills already have `Failure Modes` tables (a superset of Gotchas -
they include the surface and the fix, which Gotchas do not require). **This is
already better than what they found lacking**, confirmed by grepping A.W.I.N.O.'s
own skills.

**Decision: no seed needed.** A.W.I.N.O. already does this. Noted here so the
comparison is honest rather than silently agreeing with every recommendation from
the source material.

### 2.6 Git worktree isolation (from superpowers' using-git-worktrees + the repo's own porting guide's emphasis on isolated, verifiable branches)

Superpowers' basic workflow creates an isolated worktree on a new branch before any
implementation starts, runs project setup, and verifies a clean test baseline
*before* work begins - catching "the tests were already broken" as a distinct,
named problem rather than blaming the agent's change.

A.W.I.N.O.'s `smith-delegate` dispatches subagents into `.smith/assignments/`
prompt files but does not isolate each subagent's *file writes* into a separate
git worktree, so two subagents scoped to genuinely disjoint files still write into
the same working tree - which is fine for disjoint scopes, but gives no clean
rollback if one subagent's work needs to be discarded.

**Decision: adopt as an opt-in enhancement to `smith-delegate`**, not a
requirement - most A.W.I.N.O. delegate use cases are small enough that a shared
tree is fine, and worktree creation has real cost (disk, setup time). Flag it as
`--worktree` for larger delegated batches.

## 3. What is explicitly rejected, and why

- **Superpowers' plugin marketplace distribution model** (`.claude-plugin/`,
  `.codex-plugin/`, per-harness bootstrap injectors for 12+ IDEs): A.W.I.N.O.
  already solved this differently and, for its purpose, better - `smith install`
  detects the harness and rewrites frontmatter per-target from one source of
  truth, rather than maintaining 12 separate plugin manifests. Not adopting.
- **Superpowers' "brainstorming visual companion" (browser-based mockup tool)**:
  out of scope; A.W.I.N.O. has no UI layer to attach this to, and it is
  Claude Code-specific browser tooling.
- **muratcankoylan's `bdi-mental-states` (formal BDI ontology / RDF reasoning)**:
  genuinely interesting but far outside A.W.I.N.O.'s current mission (agentic
  workflow orchestration, not formal cognitive-architecture research). Not
  adopting; noted for awareness only.
- **muratcankoylan's `hosted-agents` (remote sandbox/warm-pool infrastructure)**:
  A.W.I.N.O. has no hosted execution layer. Would require infrastructure A.W.I.N.O.
  does not have and the mission statement does not call for. Not adopting.

## 4. Seeds created from this review

| Seed | Priority | What it does |
| --- | --- | --- |
| finish-awino-rename | High | pyproject `name`, remaining `cli.py` "Agent Smith" text, awino-first doc pass |
| document-review-loop | High | Adapt superpowers' spec/plan reviewer pattern onto `smith-discover`/`smith-rpi` output using existing `spawn.Role.REVIEWER` |
| systematic-debugging-phases | High | Add Iron Law phase gate + 3-strikes-to-architecture rule to `smith-triage` |
| tool-catalog-consolidation-check | Medium | New `smith doctor` check: flag overlapping command/skill descriptions |
| smith-delegate-worktree-isolation | Medium | Opt-in `--worktree` flag for delegated batches |

Each is filed as its own seed with acceptance criteria below, rather than one
mega-seed, per A.W.I.N.O.'s own `smith-discover` principle of decomposing work into
atomic, independently-verifiable units.
