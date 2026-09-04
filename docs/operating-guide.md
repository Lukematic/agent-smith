# Operating A.W.I.N.O.

This manual explains what A.W.I.N.O. is doing, what its status means, where it
must stop for you, and how to get useful, verifiable work from it. It is written
for users who want the operating model rather than only a command list.

## What A.W.I.N.O. is—and is not

**A.W.I.N.O.** means **Agentic Workflow Intelligence & Navigation Orchestrator**.
It is the primary human-facing controller for agentic-engineering work. It helps
establish a project's purpose, choose an appropriate workflow, route focused
skills, preserve decisions, run checks, and record whether required gates passed.

A.W.I.N.O. is best understood as a **harness-level controller with some loop and
state components**. It gives one agent a structured working environment: project
orientation, bounded knowledge retrieval, plans, checkpoints, a run ledger, and
computed completion gates.

It is **not**:

- a general-purpose authority outside agentic engineering;
- a scheduler, cron service, or background worker that starts itself;
- a promise that every task can run unattended;
- a system that silently changes your selected Kilo or Roo mode;
- a crawler that learns continuously from arbitrary sources;
- a substitute for human product, scientific, or architectural judgment; or
- proof that work is correct merely because an agent said it is.

The current verifier can execute commands, retain exit codes and output excerpts,
detect specific forms of test weakening, check declared file scope, and refuse to
close a run with missing or failing gates. Those are meaningful controls, but they
are not universal proof. Research conclusions and other judgment-heavy work remain
human-supervised, and even a closed run proves only the checks that actually ran.

## Read the status before the prose

Every response should begin with a compact status header such as:

```text
[A.W.I.N.O. | mode: architecture | loop: direct | run: none | knowledge: 0/3]
```

The header is a declaration of current operating state, not decoration.

| Field | Meaning | What it does **not** mean |
| --- | --- | --- |
| `A.W.I.N.O.` | The primary controller is speaking. | It does not prove the CLI or ledger is healthy; check the orientation and health output. |
| `mode: architecture` | The current work role or phase is architectural analysis. Other useful labels include `discovery`, `research`, `planning`, `implementation`, `testing`, and `review`. | It is not the user-selected restrictive Kilo/Roo mode. |
| `loop: direct` | The selected execution pattern is `direct`. The alternatives are `floor`, `ralph`, `graph`, `rpi`, and `delegate`; the LADDER node picks it. | It does not grant autonomy by itself. Verifier strength still limits how far work may proceed. |
| `run: none` | No active gate-ledger run is attached to the response. An active run shows its actual ID. | It does not mean there is no project, Seed, plan, or previous closed run. |
| `knowledge: 0/3` | No upstream knowledge body has been opened for this task out of a maximum of three. | It is not money, token usage, project files read, tool calls, skills, or sources supplied by you. |

### Mode is an explicit work label

The header's `mode` names **what role or phase A.W.I.N.O. is performing now**. It
should change at a visible boundary—for example, `discovery` while clarifying the
mission, `research` while documenting the existing system, `planning` while
preparing an approval-bound plan, `implementation` after approval, and `testing`
or `review` while gathering evidence.

A mode-label change should be stated explicitly with a reason. It is communication,
not magic, and current code does not implement an automatic status-mode state
machine. If the label changes without an explained work transition, ask A.W.I.N.O.
to reconcile it.

This status label is separate from a **Kilo/Roo custom mode**. Editor modes such as
**A.W.I.N.O. Consult** or **A.W.I.N.O. Plan** restrict available tool groups. Only
you select those modes. A.W.I.N.O. may recommend one, but it cannot silently switch
the editor's selected mode. Most work can stay in the primary A.W.I.N.O. controller
and route through a skill.

### The knowledge budget is a context guard

`knowledge: x/3` counts distinct upstream knowledge bodies opened for the current
task. The registry index can be searched without spending the budget. Fetching or
opening a body charges one slot; opening the same body again in that task does not
charge another slot. A cached body still counts toward the task's opened-body
budget even though it does not require another network fetch.

The budget:

- is **three bodies maximum per task**;
- resets for the next task;
- is not a financial budget;
- is not an LLM token counter;
- does not count repository files, tool calls, commands, or skills; and
- raises a structural error if a fourth distinct body is requested.

Needing a fourth body means the task is too broad. A.W.I.N.O. should stop, explain
the decomposition, and split the work into smaller tasks rather than hide another
fetch. This is progressive disclosure enforced in code, not a suggestion to “be
mindful of context.”

### Run is durable execution state

`run: none` is appropriate for a simple question or before a tracked mutation has
started. Any non-trivial work should open a run and display an identifier similar
to `20260826-173000-a1b2c3`. That run stores its task class, objective, declared
write scope, required gates, evidence, plan decisions, checkpoints, skill events,
and closure verdict.

A run ID lets `awino gate status` and `awino resume` recover durable facts instead
of relying on conversation memory. A stale pointer to a closed run is not an active
run and should be reported as stale, not quietly presented as active.

## Status header versus orientation

The one-line header answers “how is A.W.I.N.O. operating in this reply?” The
orientation block answers “where are we, what state exists, and what happens next?”
Before substantive work in a new or resumed session, expect:

```text
Project: C:/work/example
Mission confidence: confirmed
Toolchain: install=uv sync; lint=uv run ruff check src tests; test=uv run pytest
Tracker: Seeds ready; seed-123 linked
Active run: 20260826-173000-a1b2c3, code-change
Pending human decision: none
Next recommended action: run targeted tests
Route skill: awino-rpi, used
```

Every orientation field has a distinct job:

| Field | Interpretation |
| --- | --- |
| **Project** | The resolved target repository, or `unknown`. Verify this before allowing writes. |
| **Mission confidence** | `confirmed` means a human accepted the mission; `derived` means evidence suggests it but a human has not confirmed it; `unknown` means discovery is still needed. |
| **Toolchain** | Detected project commands and tools. A.W.I.N.O. should use the project's checks rather than impose a preferred stack. |
| **Tracker** | Seeds state and linked issue, or `none`. Seeds is optional. |
| **Active run** | The durable run ID and class, or `none`. |
| **Pending human decision** | The unresolved checkpoint decision, or `none`. Work must stop rather than infer a choice. |
| **Next recommended action** | One concrete action, preferably from the latest checkpoint. |
| **Route skill** | The canonical skill recommendation or `direct`, plus truthful `loaded` or `used` state when applicable. |

For an active run, a fuller status can also expose plan validity, executed versus
attested evidence, exit codes, the current blocker, and linked Seed. Unknown,
missing, failing, refused, and stale states should be named rather than smoothed
over.

## Do not confuse mode with the leverage ladder

The **status mode** says what A.W.I.N.O. is doing now. The **leverage ladder** asks
what kind of artifact will actually solve the problem.

| Ladder rung | Artifact being authored | Typical signal |
| --- | --- | --- |
| Prompt | A single prompt | One bounded interaction needs clearer instructions. |
| Context | What the agent sees each turn | History overflows, retrieval fails, or important context disappears. |
| Harness | The environment one agent runs in | A repeated mistake survives prompt instructions; permissions, hooks, validators, or gates are needed. |
| Loop | The system that prompts the agent | Work must recur on a trigger, such as nightly execution. |
| Factory | The system that builds software across projects | The request spans a fleet, organization, or many repositories. |

For example, A.W.I.N.O. can be in `mode: architecture` while concluding that a
repeated unsafe write is a **harness-rung** problem. `architecture` is the present
role; `harness` is the intervention level. The ladder runs first because polishing
a prompt cannot repair an unenforced environmental boundary.

## Choosing the loop

The loop field declares the workflow pattern selected before acting.

| Loop | Choose it when | Human/checkpoint behavior | Avoid it when |
| --- | --- | --- | --- |
| `direct` | The work is understood, small, and usually limited to one or two files, or it is a bounded question. | Proceed within scope; open a run for non-trivial mutation and report evidence. | The design is unclear or the change spans many coupled files. |
| `rpi` | The system must be researched, then planned, then implemented in one ordered workflow. | Stop after research if understanding is incomplete; obtain approval for contracts requiring a plan before implementation. | A two-line, well-understood fix would create ceremony without safety. |
| `ralph` | Work may need multiple attempts and has a machine-checkable pass/fail gate. | Iterate only within the allowed bound; stop after three failed attempts on one gate. | Correctness depends mainly on subjective judgment or no executable verifier exists. |
| `delegate` | Workstreams have disjoint file ownership and can be independently verified. | Show ownership and verification for every assignment; re-run checks rather than trust worker claims. | Agents would touch the same files, interfaces are unsettled, or verifier strength is weak. |

### Common loop transitions

Loop labels may change as evidence changes, but the transition should be explicit:

- `direct → rpi`: inspection reveals hidden coupling or an unsettled design.
- `rpi → direct`: research shows the implementation is genuinely small and
  understood; state why the reduced ceremony remains safe.
- `rpi → ralph`: the plan is approved and a difficult implementation has an
  objective iterative gate.
- `rpi → delegate`: research and design settle interfaces, ownership becomes
  disjoint, and verification is strong enough to justify fan-out.
- `delegate → direct`: parallel assignments finish; the controller integrates and
  independently verifies the whole result.
- any loop → checkpointed stop: a plan decision, scope change, failed gate, or
  human judgment is required.

The loop is not an autonomy promise. The verifier model separately limits work to
`supervised`, `checkpointed`, `bounded`, or—in the strongest computed case—
`unattended`. Current A.W.I.N.O. has no scheduler or trigger service, so an
`unattended` verifier verdict means a trigger *could* drive that work; it does not
mean A.W.I.N.O. will start running in the background.

## End-to-end operating flow

### 1. Bootstrap only with permission

Plugin installation does not create project state. In an unfamiliar project,
A.W.I.N.O. should inspect context and ask before initialization. If you approve:

```bash
awino work-init
awino work-init --confirm
awino onboard
```

`work-init` initializes optional Seeds tracking after preview/confirmation.
`onboard` captures project, mission, toolchain, tracker state, and the next mission
question. It does not create a target Python environment. If the mission is
derived or incomplete, answer one frontier question at a time, then confirm:

```bash
awino onboard --set primary_user="library maintainers"
awino onboard --confirm
```

### 2. Select or state the work

If the project uses Seeds, `awino work` lists ready issues. A **Seed** is a Git-native
tracked issue; dependency resolution determines whether it is ready or blocked.
A.W.I.N.O. works without Seeds and must not create a competing hidden worklist.

You can also state the objective directly. Include desired outcome, constraints,
allowed scope, and the evidence you expect.

### 3. Analyze before committing to a workflow

```bash
awino plan "add retry behavior without changing the public API" --class code-change
```

This command evaluates leverage rung, current bottleneck, verifier strength,
autonomy, and fan-out readiness. It does **not** create the written plan that a
gated run may require.

### 4. Review and approve the written plan

`code-change`, `refactor`, and `authoring` runs require an approval-bound plan.
Review its phases, write scope, compatibility decisions, tests, rollback, and
acceptance criteria before approving it:

```bash
awino gate open code-change "add retry behavior" \
  --scope src/client.py --scope tests/test_client.py \
  --plan thoughts/plans/retry.md --issue seed-123
awino gate plan approve --by "project owner" --reason "scope and checks accepted"
```

Approval is bound to the plan's SHA-256, path, and declared scope. Editing any of
those invalidates approval. Use `hold` to request changes or `reject` to decline
the approach; declining is a valid outcome. A.W.I.N.O. must not represent itself as
the human approver.

### 5. Load and record focused skills

A skill is a procedure selected for the task; it is not an editor mode. Routing,
loading, and use are separate facts:

```bash
awino skills --route "refactor request routing across six files"
awino gate skill awino-rpi --state loaded --reason "complex multi-file change"
awino gate skill awino-rpi --state used --reason "research and plan phases followed"
```

Canonical current capabilities include discovery, consultation, triage, evidence
review, reproducibility, RPI, Ralph, delegation, agent/tool authoring, memory,
self-update, visualization routing, bootstrap, debugging (`awino-debug`, with the
`awino debug` command family), and configuration review (`awino-config-review`,
`awino config-review`). A routing recommendation is not proof that the skill was
loaded or used. Completion is the gate-ledger verdict plus the independent `gate
review` provenance record; it is not a separate completion agent and not a
universal correctness oracle.

**Planned, not current:** running `rpi` and `delegate` node-by-node inside `awino
best` (today they are declared loops the machine stops at OPEN for), and deleting the
legacy executors `gate graph` / `gate loop` / `dispatch` (Seed e860). Do not read a
loop name in the header as proof the machine walked it.

### 6. Work in explicit phases and checkpoint

After approval, A.W.I.N.O. should begin execution rather than repeatedly summarize
the assignment. At every phase boundary, before compaction, before pausing, and
before a handoff, save durable continuation state:

```bash
awino gate checkpoint --phase implementation \
  --summary "parser complete; targeted tests pending" \
  --next "run parser tests"
```

When judgment is required, create one bounded pending decision and stop:

```bash
awino gate checkpoint --phase design \
  --summary "two storage choices remain" \
  --next "implement selected choice" \
  --pending "Choose storage" --option sqlite --option postgres
awino gate decide sqlite --by "project owner"
```

Only one unresolved checkpoint decision may exist. This prevents new work from
accumulating on top of an unanswered design choice.

### 7. Resume from the ledger, not conversational memory

```bash
awino resume
```

Resume should report the active run and objective, linked Seed, plan validity,
latest checkpoint, pending decision, and one next action. Revalidate the plan
before continuing. If no checkpoint exists, inspect `awino gate status`; do not
invent progress from a partial chat transcript.

### 8. Execute tests and independent checks

Use commands detected from the project. Examples:

```bash
awino gate record tested --cmd "uv run pytest"
awino gate record linted --cmd "uv run ruff check src tests"
awino gate check --diff-base HEAD
```

The ledger executes each command and records its exit code, duration, attempt,
output hash, and an output excerpt. `gate check` independently inspects the Git
diff for declared-scope violations and recognizable test weakening. It fails if a
usable diff cannot be produced; absence of evidence is not treated as a clean diff.

For delegated work, each assignment must declare its objective, exclusive file
scope, context, exact verification command, and completion signal. The controller
must re-run verification. A subagent's success message is not evidence.

### 9. Clean up without destroying evidence

Before beginning unrelated work in a directory, inspect clutter:

```bash
awino tidy --dry-run
awino tidy
awino clean
```

`tidy` archives recoverable clutter. `clean` removes only regenerable artifacts.
Do not use cleanup to erase run evidence, hide a failure, or silently alter files
outside the approved scope.

### 10. Let the ledger decide completion

```bash
awino gate status
awino gate close
awino doctor --fast
```

`gate close` computes missing, failing, exceeded, satisfied, and attested-only
gates. It refuses closure when required proof is absent or failing. A successful
close supports the statement that the declared contract's recorded gates passed;
it does not prove every untested behavior or subjective requirement.

If a linked Seed exists, close it only after the run:

```bash
awino work-close
```

### 11. Record durable memory

End non-trivial work by recording a durable rule if one was earned. Binding lessons
are append-only; revisions supersede earlier entries rather than erasing history.
Session logs preserve attempts, while expertise memory holds classified patterns,
decisions, failures, and conventions. Knowledge cache contents are disposable and
are not project memory.

If nothing durable was learned, say so explicitly rather than manufacture a lesson.

## Task classes and fixed gates

The task class is a contract. It determines required gates; neither the user nor
the agent should quietly remove an inconvenient gate.

| Task class | Required gates | Practical meaning |
| --- | --- | --- |
| `question` | none | Answer directly; use sources and the knowledge budget when book claims are needed. No run is normally necessary for a simple answer. |
| `research` | `researched` | Record the research artifact or evidence. Because this can be attested and judgment-heavy, it remains supervised. |
| `code-change` | `planned`, `tested`, `linted`, `tests_not_weakened`, `scope_respected` | Obtain hash-bound plan approval, execute project checks, and inspect the diff independently. |
| `bugfix` | `researched`, `tested`, `linted`, `tests_not_weakened`, `lesson_recorded` | Reproduce or diagnose before fixing, preserve the test bar, and record the prevention lesson. |
| `refactor` | `planned`, `tested`, `linted`, `tests_not_weakened`, `scope_respected`, `reviewed` | Preserve behavior, remain in scope, and obtain independent review evidence. |
| `authoring` | `planned`, `validated`, `lesson_recorded` | Approve the agent/skill/tool plan, run the relevant validator, and retain the authoring lesson. |

`planned`, `researched`, and `lesson_recorded` often involve attestations because
human judgment is intrinsic to them. Executable gates should use command-backed
evidence rather than attestations. The verifier lowers autonomy when an executable
check is merely asserted.

After three failed command-backed attempts on one gate, stop and escalate. Report
the command, all outcomes, changes between attempts, current hypothesis, and the
decision or evidence needed. Do not weaken the test or raise the retry ceiling.

## How to interact effectively

### Give outcome, boundaries, and proof

A strong request says:

1. what should be true when finished;
2. what must not change;
3. which files or systems are in scope;
4. who approves judgment calls; and
5. what evidence would convince you.

You do not need to prescribe the loop. Ask A.W.I.N.O. to recommend one and explain
why. If it selects a ceremony-heavy loop for trivial work—or direct editing for a
large refactor—challenge the mismatch.

### Approve, tweak, reject, or pause

- **Approve:** “Approve this exact plan and scope. Begin the first implementation
  action and checkpoint at the stated boundary.”
- **Tweak:** “Hold the plan. Add rollback steps and a compatibility test; show me
  the changed scope before requesting approval again.”
- **Reject:** “Reject this plan because it changes the public API. Preserve the
  run and give me the resume command.”
- **Pause:** “Checkpoint now with completed facts, one next action, and any pending
  decision. Do not continue.”

Editing an approved plan invalidates approval by design. Treat reapproval as a
safety feature, not paperwork.

### Demand evidence precisely

Useful prompts include:

- “Show the exact command, exit code, and relevant output for every required gate.”
- “Separate executed evidence from attestations.”
- “Run the independent diff check and list every changed file against scope.”
- “State what remains unverified even if the run closes.”
- “Do not accept a delegated worker's claim; re-run its verification.”
- “Show `awino gate status` and the real `awino gate close` result before using
  ‘done,’ ‘fixed,’ ‘passing,’ or ‘complete.’”

## Common scenarios and exact prompts

### Ask an agentic-engineering question

```text
Explain when a tool restriction is better than another prompt rule. Use A.W.I.N.O. consult routing, cite any fetched book bodies, show the knowledge count, and mark inferences.
```

Expected shape: `loop: direct`, usually `run: none`, at most three upstream bodies,
and explicit citations or `[inferred]` labels.

### Fix a bug

```text
Reproduce and fix the duplicate-request bug without changing the public API. Use task class bugfix, declare the write scope before editing, add a regression test, run the project test and lint commands, check that tests were not weakened, record the lesson, and do not claim completion unless gate close succeeds.
```

Expected shape: diagnosis before mutation, a bugfix run, executed evidence, and a
three-strikes stop if one gate repeatedly fails.

### Implement a multi-file feature

```text
Add resumable uploads across the client, service, and tests. Use RPI: research the current interfaces, stop for my approval of a written plan and exact scope, then implement in phases with checkpoints. Do not switch to delegation unless ownership is disjoint and verifier strength supports fan-out.
```

Expected shape: `loop: rpi`, research before design, hash-bound approval, explicit
mode transitions, and no implementation before approval.

### Conduct research

```text
Research whether our retrieval design supports claim-level citations. Use the evidence skill, distinguish source-supported findings from inference, record provenance, stop if evidence is insufficient, and state what a domain expert must decide.
```

Expected shape: supervised research, transparent insufficiency, and no claim that
an attested research gate licenses unattended synthesis.

### Run a reproducible pipeline

```text
Run the ingestion pipeline once with a durable run ID. Snapshot inputs, configuration, prompt and model versions, retries, errors, and output checks. Use the project's real command, checkpoint before any human decision, and report exact evidence plus anything not reproduced.
```

Expected shape: reproducibility routing, explicit run artifacts, and no background
or recurring-run promise.

### Resume interrupted work

```text
Resume from A.W.I.N.O.'s durable ledger, not chat memory. Show the active run, linked Seed, plan validity, latest checkpoint, pending decision, and one next action before using tools.
```

Expected shape: `awino resume` first, with stale or missing state reported honestly.

### Handle a blocked decision

```text
Checkpoint the current phase and stop. Record the blocker as one pending decision with bounded options, explain the consequence of each option, and do not infer my choice or start new subtasks.
```

Expected shape: one pending decision and no further implementation until an
authorized `awino gate decide` selection is recorded.

## Quick reference

| Need | Prompt or command |
| --- | --- |
| Orient in a project | `awino onboard` |
| Inspect paths and tools | `awino context` |
| Check health | `awino doctor --fast` |
| Analyze approach | `awino plan "<request>" --class <task-class>` |
| Route a skill | `awino skills --route "<request>"` |
| List ready Seeds | `awino work` |
| Open tracked work | `awino gate open <class> "<objective>" --scope <path> ...` |
| Review exact plan | `awino gate plan status` |
| Approve exact plan | `awino gate plan approve --by "<approver>"` |
| Save progress | `awino gate checkpoint --phase <phase> --summary "..." --next "..."` |
| Resume | `awino resume` |
| Record a real check | `awino gate record <gate> --cmd "<project command>"` |
| Check diff independently | `awino gate check --diff-base HEAD` |
| Inspect blockers | `awino gate status` |
| Compute completion | `awino gate close` |
| Pause safely | “Checkpoint now and do not continue.” |
| Demand proof | “Show exact commands, exit codes, output, attestations, and remaining unknowns.” |

## Glossary

**Attestation**  
A recorded assertion used when no meaningful command can decide the claim. It is
visible as weaker evidence and must not masquerade as an executed check.

**Checkpoint**  
Durable phase, summary, one next action, and optionally one unresolved decision
with bounded choices.

**Gate**  
A required obligation such as `tested`, `linted`, or `reviewed`. Gates come from
the task-class contract.

**Harness**  
The environment around an agent: tools, permissions, hooks, context assembly,
validators, state, and checks that make behavior more reliable than prose alone.

**Knowledge body**  
An upstream document opened after index routing. At most three distinct bodies may
be opened per task.

**Leverage ladder**  
The prompt → context → harness → loop → factory model used to identify the right
artifact to change. It is not the status `mode`.

**Loop**  
The loop the ladder chose for the run: `direct`, `floor`, `ralph`, `graph`, `rpi`, or `delegate`.

**Mode (status)**  
An explicit label for A.W.I.N.O.'s present role or phase, such as `research` or
`testing`. It is descriptive rather than an automatic state machine.

**Mode (Kilo/Roo)**  
A user-selected editor preset that structurally restricts tool groups. A.W.I.N.O.
cannot silently change it.

**Plan approval**  
A human decision bound to the exact plan bytes, path, and declared scope. Any
change invalidates it.

**Run**  
A durable unit of work containing the task contract, scope, evidence, checkpoints,
skills, and verdict.

**Seed / Seeds**  
An issue / the optional Git-native issue tracker used to identify ready and blocked
work. A.W.I.N.O. can operate without it.

**Skill**  
A focused procedure that can be recommended, loaded, and used. Those three states
are distinct and auditable.

**Verifier strength**  
The quality and independence of available evidence. It limits autonomy regardless
of model confidence or selected loop.

## Related guides

- [User guide](user-guide.md) — installation, commands, and paired workflow
- [Agent and harness guide](agent-guide.md) — controller and integration contract
- [Gate enforcement](enforcement.md) — ledger mechanics
- [Canonical skill catalog](skills.md) — currently installed skills
