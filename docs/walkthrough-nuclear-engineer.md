# Walkthrough: A Nuclear Engineer Uses A.W.I.N.O.

This is a concrete walkthrough, not a claim that A.W.I.N.O. already knows nuclear
engineering. A.W.I.N.O. begins as an **agentic-engineering expert and domain newcomer**.
It learns enough project context to make the user's domain expertise easier to
apply, while keeping scientific judgement with the engineer.

Grounding:

- `chapters/4-context/3-context-patterns.md` — progressive disclosure: load the
  mission and only the relevant project evidence, not the whole repository.
- `chapters/6-harnesses/4-harness-as-control-system.md` — guides before action,
  sensors after action.
- `chapters/6-harnesses/5-harness-engineering.md` — convert recurring mistakes
  into structural prevention.
- `chapters/7-patterns/1-plan-build-review.md` — separate understanding, planning,
  implementation, and verification.
- `chapters/8-practices/2-evaluation.md` — define task-specific checks before
  granting autonomy.
- `chapters/12-long-horizon-agent-state/3-memory-and-intent.md` — persist project
  intent and decisions without treating memory as ground truth.

---

## Scenario

The user is a nuclear engineer studying a catalytic process for isotope
production. Their repository contains:

```text
project/
  AGENTS.md
  README.md
  data/experimental-runs.csv
  notebooks/kinetics.ipynb
  src/model.py
  tests/test_model.py
  .seeds/
```

The engineer asks:

> Help me determine whether the observed yield change is a real catalyst effect,
> then plan the analysis without overstating what the data supports.

A.W.I.N.O. does **not** pretend to be the nuclear-domain authority. Its job is to make
the work structured, traceable, reproducible, and difficult to overclaim.

---

## Step 1 — Identify the project and its mission

```bash
awino context
awino mission
```

Expected outcome:

- A.W.I.N.O. identifies the actual project root rather than writing state into its own
  installation.
- It detects Python, the environment manager, lint command, and test command.
- It reads `AGENTS.md`, project metadata, README purpose/non-goals, and current
  tracked work.
- It reports confidence. If the mission is inferred rather than stated, it asks
  the engineer to confirm it instead of silently assuming.

Example mission that the engineer confirms:

```text
Evaluate catalyst-dependent isotope-production yield while preserving full
provenance from raw experimental runs through fitted kinetic parameters.
```

That statement becomes the constraint for later recommendations. Advice that
optimizes model sophistication but loses provenance is now visibly off-mission.

---

## Step 2 — Decide which engineering rung applies

```bash
awino plan "Determine whether the yield change is a catalyst effect without overstating the evidence"
```

Likely decision:

- **Prompt-level** for a one-off analysis question.
- **Context-level** if source provenance or experiment metadata is missing from
  what the analysis agent sees.
- **Harness-level** if the agent repeatedly pairs a yield with the wrong run,
  fabricates unsupported mechanisms, or ignores a stated uncertainty rule.
- **Loop-level** only if this analysis must recur for every new experiment and has
  an objective verifier.

This prevents the common mistake of turning a one-off scientific uncertainty into
an autonomous pipeline before the verification criteria exist.

---

## Step 3 — Clarify scientific decisions with the user

A.W.I.N.O. should use the planning/grilling posture here: one decision at a time,
reflect the answer back, and distinguish **talkable** questions from questions that
require a prototype or new experiment.

Examples:

1. **What is the comparison unit?** Individual run, batch, irradiation campaign,
   or fitted condition?
2. **What confounders are already known?** Temperature, beam current, target age,
   pressure, detector calibration, operator, or run order?
3. **What counts as a catalyst effect?** A minimum effect size, confidence
   interval, posterior probability, or mechanistic signature?
4. **Which claims are prohibited?** For example, no mechanistic attribution from
   correlation alone.
5. **What would invalidate the analysis?** Missing calibration records, insufficient
   replicates, leakage between train/test groups, or changed instrumentation.

If the engineer says “I don't know,” A.W.I.N.O. records an open decision or proposes a
small prototype. It does not fill the gap with a confident guess.

---

## Step 4 — Research the repository before planning changes

Use `awino-rpi` research mode. The research output describes what exists; it does
not propose fixes yet.

Parallel read-only assignments can inspect disjoint concerns:

| Subagent | Reads | Returns |
| --- | --- | --- |
| data scout | schemas, data dictionaries, ingest code | provenance and missing-field map |
| model scout | `src/model.py`, fit code | exact model assumptions and parameter flow |
| test scout | tests and CI | what is already checked and what is not |
| domain-evidence scout | project docs supplied by the user | claim-to-source map, no external facts |

A.W.I.N.O. may spawn these only when an authenticated runner exists. If no runner is
configured, it reports the limitation and produces the same assignment plan for a
human or the current session; it does not claim they ran.

The research artifact goes to:

```text
thoughts/research/YYYY-MM-DD-HHmm-catalyst-yield.md
```

---

## Step 5 — Produce a plan the engineer can defend

The plan is presented in plain language before implementation. Every scientific
choice is explicit:

```text
Phase 1: provenance audit
  Automated gate: every modeled row maps to one raw run ID
  Human gate: engineer confirms excluded-run rationale

Phase 2: exploratory comparison
  Automated gate: effect estimates include intervals and grouped residual checks
  Human gate: engineer confirms scientifically relevant effect threshold

Phase 3: confounder-aware model
  Automated gate: leave-one-campaign-out validation passes predefined criteria
  Human gate: no mechanistic claim beyond measured variables

Phase 4: reproducible report
  Automated gate: rerunning from the input snapshot reproduces tables and figures
  Human gate: engineer approves interpretation and stated limitations
```

The engineer can disagree, change the threshold, narrow the scope, or require a
new experiment. A.W.I.N.O. reflects the revised decisions back before implementation.

---

## Step 6 — Work under a gate

```bash
awino gate open code-change \
  "Add confounder-aware catalyst-yield analysis" \
  --scope src/model.py \
  --scope tests/test_model.py

awino gate record tested --cmd "uv run pytest tests/test_model.py -q"
awino gate record linted --cmd "uv run ruff check src tests"
awino gate check --diff-base HEAD
awino gate close
```

The gate does not certify scientific truth. It certifies that the declared
software checks ran, file scope held, and tests were not weakened. Scientific
interpretation remains an explicit human gate.

---

## Step 7 — Turn recurring failures into the harness

Suppose the analysis agent repeatedly reports a catalyst mechanism from a
correlation. A.W.I.N.O. triages it:

```text
Failure mode: UNSUPPORTED_MECHANISM
Surface: context + tools

Rejected prompt patch:
  "Never overstate mechanisms."

Structural fix:
  require every mechanism claim to carry:
  - the measured variables supporting it,
  - the source run IDs,
  - an uncertainty statement,
  - a claim class of measured / inferred / speculative.

Recurrence block:
  schema validation plus a regression case that rejects an unsupported mechanism.
```

The project-specific rule belongs in project memory, not A.W.I.N.O.'s global doctrine:

```text
.smith/memory/lessons.md
```

A.W.I.N.O.'s global memory should only retain the reusable pattern: “scientific claim
classes must be structurally distinguishable,” not the project's catalyst result.

---

## What A.W.I.N.O. learns—and what it does not

A.W.I.N.O. can learn:

- the project's stated mission and non-goals;
- its toolchain, file boundaries, and verification commands;
- user-approved scientific thresholds and prohibited claim types;
- recurring failure modes and their structural prevention;
- where project evidence lives and which artifacts are authoritative.

A.W.I.N.O. must not treat memory as evidence for:

- nuclear properties, mechanisms, cross sections, decay data, or safety claims;
- experimental facts absent from the project's authoritative sources;
- a scientific interpretation the engineer has not approved.

Those require source-grounded retrieval and domain review. A.W.I.N.O. is the workflow
and harness expert helping the nuclear engineer—not a replacement for the nuclear
engineer.

---

## Practical first session

```text
You: I am a nuclear engineer studying catalyst effects on isotope-production yield.
     Help me structure the analysis, but do not make unsupported nuclear claims.

A.W.I.N.O.:
1. Runs `awino context` and `awino mission`.
2. Reflects the mission back and asks for confirmation.
3. Runs `awino plan` on the request.
4. Asks one decision question at a time about comparison units, confounders,
   evidence thresholds, and prohibited claims.
5. Researches the repository read-only.
6. Presents a phased plan with automated and human gates.
7. Implements only after approval.
8. Refuses completion until the software gates pass.
9. Writes project-specific lessons without promoting scientific conclusions into
   global memory.
```

That is the intended value: the engineer spends attention on scientific judgement,
while A.W.I.N.O. carries the structure, provenance, decomposition, verification, and
recurrence prevention.
