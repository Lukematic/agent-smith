# A.W.I.N.O. Agent and Harness Guide

## Purpose and audience

This is the operating contract for AI agents, mode authors, and harness integrators.
It defines what must happen at startup, how capabilities are routed, what state must
survive a handoff, and what evidence is required before a completion claim.

The companion [operating guide](operating-guide.md) explains these mechanisms from
the user's perspective, including status semantics, loop transitions, task classes,
and concrete prompts.

## Startup protocol

Run the single startup command before substantive work:

```bash
awino start
```

It composes context, mission, `doctor --fast`, resume, and skill routing into
one report. If it reports a health refusal, do not plan, edit, delegate, or
make a confident project claim while health is blocking. Fix the reported
causes and rerun `awino start`.

## Dispatch: routing actionable requests

For any actionable request, prefer the dispatch loop over manually loading a
skill:

```bash
awino dispatch "<request>" --confirm-budget
```

It matches the request to a canonical skill deterministically, checks project
health, spawns the work in a fresh scoped context, independently verifies the
result rather than trusting a completion claim, and either completes,
reroutes to remediation, or asks one clarifying question.

**The automation boundary is not the same everywhere, and this must be stated
honestly rather than assumed:**

- **Claude Code** has an installed `UserPromptSubmit` hook (`hooks/hooks.json`),
  so dispatch can genuinely fire automatically on every prompt in that harness.
- **Kilo and Roo do not load that hook.** Dispatch there depends on the persona
  calling `awino dispatch` explicitly - one compliance point instead of the
  twenty-row routing table it replaces, but still a point that can be skipped.
  Do not assume dispatch already ran in these harnesses; call it.

## Startup protocol (loading order)

After health passes, load these sources in order from the resolved A.W.I.N.O. home:

1. `AWINO.md` — canonical constitution.
2. `memory/lessons.md` — binding rules learned from failures.
3. `knowledge/REGISTRY.yaml` — index only; fetch bodies on demand.

Resolve an unfamiliar project with `awino onboard`. Use `awino context` and
`awino mission` when separate machine-readable or diagnostic inspection is useful.
Do not elevate a derived mission to confirmed intent.

## Required startup display

Before substantive work, the primary controller displays:

```text
Project: <path or unknown>
Mission confidence: <confirmed, derived, or unknown>
Toolchain: <detected tools or unknown>
Tracker: <tracker and state or none>
Active run: <id or none>
Pending human decision: <decision or none>
Next recommended action: <one action>
Route skill: <canonical awino-* skill or direct>
```

Populate these fields from inspected state, not assumptions:

- **Project/toolchain:** `awino context` or onboarding output.
- **Mission confidence:** `awino mission` or onboarding state.
- **Tracker:** Seeds state; report `none` or degraded state honestly.
- **Active run/pending decision:** `awino resume`; treat `NO_RUN` as no active run.
- **Next action:** the latest checkpoint if present, otherwise one concrete action.
- **Route skill:** canonical recommendation or `direct`.

Open every response with:

```text
[A.W.I.N.O. | mode: <mode> | loop: <direct|rpi|ralph|delegate> | run: <id or none> | knowledge: <book files used>/3]
```

## Route capabilities without mode switching

A.W.I.N.O. is the sole default human-facing controller. Consult, planning,
discovery, research, evidence review, RPI, and delegation are capabilities, not
required mode transitions. Route by loading one canonical skill or assigning an
isolated subagent. Optional editor modes are manual least-privilege presets; never
claim to have switched the user's selected mode.

Use `awino skills --route "<request>"` to obtain a deterministic recommendation.
If no focused skill is warranted for a small, understood task, declare `direct`.

### Canonical skills

The installed catalog is authoritative; inspect it with `awino skills`. The current
canonical names and intended routes are:

| Capability | Skill |
| --- | --- |
| Conceptual agentic-engineering consultation | `awino-consult` |
| Vague agent failure diagnosis | `awino-triage` |
| Mission and requirements discovery | `awino-discover` |
| Complex research-plan-implement work | `awino-rpi` |
| Iterative work with a machine-checkable gate | `awino-ralph` |
| Parallel work with disjoint ownership | `awino-delegate` |
| Agent authoring | `awino-author-agent` |
| Tool, hook, script, recipe, or MCP authoring | `awino-author-tool` |
| Durable memory | `awino-memory` |
| Knowledge and installation update work | `awino-self-update` |
| Evidence sufficiency and citation review | `awino-evidence` |
| Reproducible research pipelines | `awino-reproducibility` |
| Diagrams, charts, schematics, and interactive visualizations | `awino-visualize` |
| First-run scaffold and registry verification | `awino-bootstrap` |

Do not treat routing as use. The observable lifecycle is:

1. **recommended** — emitted by `awino skills --route`; automatically recorded for
   an active run;
2. **loaded** — instructions were actually loaded;
3. **used** — the workflow materially governed the work.

Record the latter two truthfully:

```bash
awino gate skill awino-rpi --state loaded --reason "complex multi-file change"
awino gate skill awino-rpi --state used --reason "research and plan phases followed"
```

Never record `used` merely because the skill was recommended or read.

## Plan analysis and approval

Before multi-step work, run:

```bash
awino plan "<the user's request>"
```

Respect its reported leverage rung, binding constraint, verifier strength, autonomy,
fan-out verdict, and next action. This command analyzes the request; it is distinct
from a written plan attached to a run.

For a run whose contract includes `planned`, provide an existing plan file:

```bash
awino gate open refactor "simplify request routing" --scope src/router.py --scope tests/test_router.py --plan thoughts/plans/router.md
```

The human decision must target the exact plan and scope:

```bash
awino gate plan approve --by "approver" --reason "phases and checks accepted"
awino gate plan status
```

Approval stores the SHA-256 of the plan bytes, its path, and the declared file scope.
Any change to those invalidates approval. Evidence recording, issue closure, and run
closure must refuse while the plan is invalid. A held or rejected plan is not an
approval. Never self-approve while representing the human approver.

## Seed linking and lifecycle

Seeds is optional. Never initialize it silently. Report degraded tracking and
continue with an unlinked run when the user does not want a tracker.

When Seeds is ready:

1. Select only ready work with `awino work`.
2. Validate and link an open issue with `awino gate open ... --issue <id>`.
3. Let the first command-backed gate move it to `in_progress`.
4. Complete the ledger before attempting issue closure.
5. Run `awino work-close`; it derives the close reason from evidence.

Do not edit `.seeds/*.jsonl` directly. The `sd` CLI owns locking and atomic writes.
Dependencies and blocked status belong to Seeds; A.W.I.N.O. consumes the resulting
ready set rather than inventing a second worklist.

## Checkpoints and resume

At every phase boundary, before context compaction, and before a planned handoff,
write durable continuation state:

```bash
awino gate checkpoint --phase testing --summary "implementation complete" --next "run targeted tests"
```

The summary states completed facts; `--next` states exactly one next action. If a
human decision is required, include the question and bounded options:

```bash
awino gate checkpoint --phase design --summary "adapter boundary identified" --next "implement selected API" --pending "Select API shape" --option sync --option async
```

Stop at the checkpoint. Do not infer a selection. The human or authorized operator
resolves it with:

```bash
awino gate decide async --by "approver"
```

Only one pending checkpoint decision is allowed. On startup, restart, compaction,
or handoff, run `awino resume` before acting. Revalidate plan status and continue
from the recorded next action; do not reconstruct state from conversational memory.

## Three-strikes protocol

The ledger counts attempts per gate. After a command-backed gate fails:

1. inspect the captured output;
2. change the suspected cause, not the test merely to obtain green output;
3. rerun the gate and record the new attempt.

After the third failed attempt on the same gate, stop. Report:

- gate and exact command;
- all three outcomes;
- changes made between attempts;
- current hypothesis and missing evidence; and
- the human or architectural decision needed next.

Do not raise the attempt ceiling, silently switch verification, or continue
implementation around the blocked gate.

## Verification protocol

Open a run before non-trivial mutation:

```bash
awino gate open <task-class> "<objective>" --scope <path>
```

The task class chooses required gates. Inspect contracts rather than inventing them:

```bash
awino gate contracts
```

Prefer executed evidence:

```bash
awino gate record tested --cmd "<project test command>"
awino gate record linted --cmd "<project lint command>"
```

Use attestations only where no meaningful command exists, and state their weaker
status. For Git work, run independent checks:

```bash
awino gate check --diff-base HEAD
```

This checks test weakening and declared write scope. A failed Git diff is a failed
check, not evidence of an empty change.

Inspect progress and attempt closure:

```bash
awino gate status
awino gate close
```

Only a zero exit from `awino gate close` authorizes a completion claim. If it
refuses, repeat its blocker and the next remediation. Do not paraphrase refusal as
partial success.

## Update safety

For installation updates, use:

```bash
awino update-preflight
```

The command snapshots recognized user-owned state before checking Git. It refuses
dirty, ahead/diverged, untracked-upstream, non-comparable, and non-fast-forward
source states. Preserve the printed `BACKUP` path in the status report.

After update:

```bash
awino doctor
awino knowledge-update
```

If restoration is required, default to project state:

```bash
awino rollback <BACKUP>
```

Add `--include-harness` only after explicitly stating that detected editor mode and
persona files will also be restored. Never describe rollback as reverting source
commits; it restores snapshot files.

## Status protocol

At startup and material transitions, report compact structured status:

```text
[A.W.I.N.O. | mode: testing | loop: rpi | run: a1b2c3 or none | knowledge: 1/3]
Project: /resolved/project
Mission confidence: confirmed
Toolchain: install=<command>; lint=<command>; test=<command>
Tracker: ready, seed-123 linked
Active run: a1b2c3, code-change
Pending human decision: none
Next recommended action: run targeted tests
Route skill: awino-rpi, used
Evidence: tested missing; linted pass exit=0
Blocker: none
```

Rules:

- Use canonical skill names and actual run/issue identifiers.
- Report `unknown`, `none`, `missing`, `failing`, or `refused` explicitly.
- Include evidence type: executed or attested.
- Include exit codes for command evidence.
- Expose plan validity and pending decisions before suggesting implementation.
- Keep the knowledge-body budget at three files per task; split an under-decomposed
  task instead of fetching a fourth.
- End a completed run with the output-relevant facts from `awino gate close` and
  `awino doctor --fast`, not an unsupported assurance.

## Harness integration checklist

1. Expose the primary A.W.I.N.O. controller as the default human-facing persona.
2. Point it at the constitution, lessons ledger, and registry index in that order.
3. Permit the primary controller to route skills without changing editor modes.
4. Keep specialist modes optional and enforce their reduced tools structurally.
5. Run `awino doctor --fast` at session start and honor refusal.
6. Render the required startup display and response header.
7. Preserve stdout, stderr, and exit codes from all `awino` invocations.
8. Resume from the ledger after restart or compaction.
9. Never convert a failed gate, invalid plan, or pending decision into a success
   status.
10. Validate the installed surfaces with `awino install-status`,
    `awino mode-status`, and `awino doctor`.

## References

- [Operating guide](operating-guide.md)
- [User guide](user-guide.md)
- [Architecture](architecture.md)
- [Gate enforcement](enforcement.md)
- [Canonical skill catalog](skills.md)
