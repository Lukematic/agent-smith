# The Harness

What Agent Smith's harness *is*, which mental model fires *when*, and why.

This document exists because "we have mental models" is not a design. A model
that does not name its trigger never fires. A harness that is not inventoried
cannot be improved.

---

## 1. Do we have a harness? Yes. Here it is.

A harness is a control system. Fowler's decomposition splits it in two:

- **Guides** are feedforward. They intervene *before* the agent acts, shaping the
  action space so the good action is the path of least resistance.
- **Sensors** are feedback. They observe results *after* an action and steer what
  happens next.

Both are further split by whether the mechanism is **computational**
(deterministic code) or **inferential** (a model judging). Computational beats
inferential wherever it is available, because it does not depend on
instruction-following.

(book: chapters/6-harnesses/4-harness-as-control-system.md)

### Guides: what Smith prevents before it happens

| Guide | Kind | Mechanism | Prevents |
| --- | --- | --- | --- |
| Fetch budget | computational | `BudgetExceeded` raised on the 4th file | `CONTEXT_BLOAT` |
| Gate contract | computational | task class fixes the gates, agent never selects them | `PREMATURE_COMPLETION` |
| Scope declaration | computational | `--scope` recorded at run open, before any write | `FILE_SCOPE_VIOLATION` |
| Artifact validator | computational | `smith validate` exits 1 on a malformed skill | broken discovery, missing gates |
| Root allowlist | computational | `ROOT_ALLOWED` in `tidy.py` | clutter accumulation |
| Registry-only routing | computational | routing reads the index, never the corpus | `UNGROUNDED_CLAIM` |
| Rung detection | inferential | `smith plan` classifies the request's real rung | solving a loop problem with a prompt |
| Constraint location | inferential | upstream-first check ordering | building against a moving design |

### Sensors: what Smith observes after the fact

| Sensor | Kind | Mechanism | Catches |
| --- | --- | --- | --- |
| Exit-code capture | computational | `subprocess` result recorded, not described | a false "tests pass" claim |
| Test-weakening detector | computational | diff parsed for deleted asserts, added skips | fixing the test instead of the code |
| Scope reconciler | computational | `git diff --name-only` vs declared scope | silent scope creep |
| Attempt counter | computational | three strikes per gate | retry spirals |
| Project doctor | computational | 12 checks over structure, docs, env, memory | repo decay |
| Drift report | computational | registry diffed against the live upstream tree | `KNOWLEDGE_ROT` |
| Staleness hook | computational | `SessionStart` prints cache age | acting on stale knowledge |
| Verifier assessment | inferential | executed vs attested evidence scored | `OPEN_LOOP` |

**The asymmetry that makes this work:** the agent names the command; the harness
observes the exit code. A model can write "all tests passed". It cannot produce a
zero exit code from a failing suite.

### What the harness does not have yet

Honest gaps, not aspirations:

| Missing | Why it matters | Rung it belongs to |
| --- | --- | --- |
| Triggers (cron, CI) | without them Smith is harness-level, not loop-level | loop |
| Worktree isolation | parallel agents cannot yet be given disjoint checkouts | loop |
| Independent reviewer subagent | `reviewed` is satisfied by `doctor`, not a second model | loop |
| Telemetry after merge | no production feedback, only pre-merge checks | factory |

Smith currently sits at **harness** on the ladder, with the state and skill
components of a loop already built. Triggers and worktrees are what would promote
it. Claiming loop-level today would be `COGNITIVE_SURRENDER` by way of wishful
labelling.

---

## 2. Which model fires when

Four models, four distinct triggers. Each answers a different question, and each
is a function in `src/smith/models.py`, not a passage to read.

| Model | Fires when | Question it answers | Command |
| --- | --- | --- | --- |
| **Leverage ladder** | a request arrives | am I about to author the wrong artifact? | `smith plan`, `smith ladder` |
| **Design as bottleneck** | before spending effort | where is the scarce resource right now? | `smith plan` |
| **Verifier strength** | before granting autonomy | how far may this run unattended? | `smith plan` |
| **Pit of success** | when adding a rule | will this hold without vigilance? | `smith pit` |

### The order is not arbitrary

They run in a fixed sequence because each one can invalidate the next:

```
1. LADDER      wrong rung?        -> reframe, stop. Nothing downstream matters.
2. CONSTRAINT  wrong effort?      -> redirect effort, do not fan out.
3. VERIFIER    autonomy safe?     -> cap autonomy at what evidence supports.
4. PIT         rule will decay?   -> restructure before adding the rule.
```

Optimising execution of the wrong artifact is the most expensive mistake
available, so the ladder goes first. `smith plan` short-circuits on rung
misalignment for exactly this reason.

### Worked example: the request that looks like a prompt problem

```bash
smith plan "the agent keeps writing to the wrong directory every time"
```

```
LEVERAGE LADDER
  actual rung: harness (a repeated behaviour, which is an environment property)
  This reads as a prompt problem but behaves like a harness problem.
  Author the environment one agent runs in instead of a single prompt.
NEXT      Reframe first.
```

The reflex fix is a prompt line: "always write to /workspace". The ladder rejects
it before any effort is spent, because a *repeated* behaviour is an environment
property. The fix is a path allowlist, which is a guide.

### Worked example: thirty agents

Your question. Three separate refusals, each from a different model:

```bash
# design unsettled
smith plan "parallelise the migration across 30 agents" --understood
  -> CONSTRAINT design: every unit built against a moving interface is rework
  -> FAN OUT no

# design settled, units share files
smith plan "..." --understood --interfaces-settled
  -> CONSTRAINT decomposition: parallel agents would overwrite each other
  -> FAN OUT no

# all settled, but gates were attested rather than executed
smith plan "..." --understood --interfaces-settled --units-disjoint
  -> max autonomy checkpointed
  -> FAN OUT no
```

That third refusal is the important one. Design readiness alone does not license
parallelism: **verification strength does**. Thirty agents under a weak verifier
produce thirty unverified changes, and the faster they run the more confidently
wrong work exists before anyone notices.

Fan-out is permitted only when all three hold:

```python
constraint is IMPLEMENTATION      # design settled, units disjoint
and max_autonomy >= BOUNDED       # an executed objective check exists
and not anti_patterns             # loop is not already rotting
```

---

## 3. Autonomy is computed, not chosen

The loop-engineering claim is that verification, not model capability, bounds
autonomy. That is only actionable if verifier strength is measurable, so Smith
reads the ledger.

| Evidence state | Max autonomy | Meaning |
| --- | --- | --- |
| a gate has no passing evidence | `supervised` | human reviews every step |
| only attestations, no executed check | `supervised` | `OPEN_LOOP`, agent grades itself |
| an executable gate was attested instead | `checkpointed` | human approves at phase boundaries |
| all executed, nothing independent | `bounded` | fixed iterations, then report |
| all executed plus an independent check | `unattended` | a trigger may drive it |

One deliberate subtlety: `planned`, `researched`, and `lesson_recorded` are
**inherently attested**. A plan document either exists or it does not, and no
command judges whether it is a good plan. Penalising those attestations would cap
every task at `checkpointed` forever, which makes the measure useless. Only an
attestation standing in for a gate that *could* have been executed is a weakness.

A second consequence, which reads as a bug and is not: `research` can never
exceed `supervised`, because its contract has no executable gate. Work whose
correctness only a human can judge is precisely the work that must not loop
unattended.

---

## 4. The three ways a loop rots

Each is invisible from inside the loop, which is why each needs a check outside
it. `smith plan` reports all three.

| Anti-pattern | Detected by | Fix |
| --- | --- | --- |
| `OPEN_LOOP` | no executed objective check | close the loop with an independent check |
| `KNOWLEDGE_ROT` | cache age against the staleness policy | `smith update`, reconcile lessons |
| `COGNITIVE_SURRENDER` | autonomy at `bounded`+ with no recorded human inspection | inspect what the loop does, not just that it runs |

`COGNITIVE_SURRENDER` deliberately does not fire under `supervised` autonomy: a
human is in every turn, so nothing has been surrendered.

---

## 5. Why models are functions and not skills

A skill is a procedure you follow. A mental model is a lens that changes a
decision. Written as prose, a model competes with every other line in the prompt
and loses, which is the same dilution that makes
`verification-before-completion` decay as an instruction.

Written as a function it changes the *plan*:

| As prose | As a function |
| --- | --- |
| "consider whether this is really a prompt problem" | `detect_rung()` returns `HARNESS` and short-circuits |
| "make sure verification is strong before automating" | `assess_verifier()` caps autonomy at `supervised` |
| "design is usually the bottleneck at scale" | `locate_constraint()` refuses fan-out |
| "prefer designs where the easy path is correct" | `smith pit` exits 1 |

This is the same move as the gate ledger, one level up: replace an instruction
with a mechanism.

---

## 6. Applying it to Smith itself

The harness caught its own author four times during construction, which is the
only evidence that matters:

| What was claimed done | What the harness found |
| --- | --- |
| the restructure was finished | `docs` gate: README linked a file that did not exist |
| all skills were valid | `artifacts` gate: one skill's Completion demanded no evidence |
| the lessons ledger was clean | `memory` gate: a format template read as a malformed lesson |
| the installers were added | `structure` gate: two new root files were not allowlisted |

Each was a real defect in work already declared complete. That is the argument for
building the sensor before trusting the output.

---

## 7. Reading order

1. `src/smith/models.py` for the four decision functions
2. `tests/test_models.py` for the assertion that each one changes a decision
3. `src/smith/enforce.py` for the gate contracts and `adjudicate()`
4. `src/smith/health.py` for the project-level guides and sensors

Grounding: chapters/6-harnesses/4-harness-as-control-system.md,
chapters/6-harnesses/5-harness-engineering.md,
chapters/9-mental-models/8-loop-engineering.md,
chapters/9-mental-models/6-design-as-bottleneck.md,
chapters/9-mental-models/1-pit-of-success.md,
chapters/11-agent-readiness/1-the-four-surfaces.md
