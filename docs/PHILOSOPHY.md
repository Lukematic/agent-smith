# PHILOSOPHY.md — the owner's working philosophy, baked into the product

Docs describe. The code below enforces. Each tenet names the mechanism that
makes it real, so a reader can check the claim against the implementation.

## 1. First principles

Break the problem down, challenge assumptions, then be inventive, precise, and
curious. Work with what's available; when stuck, reframe the problem from a
different angle.

**Enforced by:** the RPI research validator (`src/awino/loops.py`,
`ResearchPhase.validate`). A research artifact must show its work — a problem
breakdown, assumptions challenged (named explicitly), and angles considered —
before any solution. An artifact that jumps straight to a solution fails
validation with the missing section named. The research prompt in
`skills/awino-rpi/SKILL.md` carries the same section requirements.

## 2. Honda first

Build what was actually asked for. Then recommend what else could be built —
with level of effort. Never build the Bugatti unasked.

**Enforced by:** the pairing-brief validator (`PairPlanPhase.validate`). Every
candidate approach must carry an explicit level-of-effort marker; an approach
without one fails validation, naming it. Exactly one approach must be marked
the default recommendation — the one that delivers precisely what was asked,
the Honda. Bigger alternates are presented as labeled recommendations with
effort estimates: options, never the plan, never built silently. The plan's
decisions section records which approach was chosen and why: a decision that
chooses an approach must say whether it followed the default recommendation
or overrode it, with a reason (`_validate_decision_trace`).

## 3. Mission-driven and measurable

Every mission states how we'll know we've reached the goal vs. not. Missions
are living — update them as they change. Keep the objective in mind, measure
it, communicate it.

**Enforced by:** the mission validator (`src/awino/heilmeier.py`,
`validate_mission`). A mission requires `objective` and `success_criteria` —
explicit, measurable statements of how we'll know we've reached the goal vs.
not. `awino mission --set` refuses a mission that claims completeness without
them, naming exactly what's missing. `awino buddy` flags existing projects
whose mission lacks criteria; `buddy --fix` scaffolds the missing sections
from the project's stated goals, marked as draft for human review — never
presented as final.

## 4. Revisit the mission at multiple points — not once

Session start, phase boundaries, loop close.

**Enforced by:** three separate mechanisms, each tested:

- **Session start:** the `best` chain's session-start order prints the mission
  and its success criteria (`src/awino/playbook.py`, `mission-criteria` step).
- **Phase boundaries:** the mid-loop mission check evaluates each validated
  artifact against the mission's success criteria — met / unmet / unjudgeable —
  as a named check result alongside the existing drift flag
  (`LoopDriver._check_success_criteria`, `success_criteria_evaluated` event).
- **Loop close:** the outcome verdict is recorded against those criteria, and
  the verdict event detail carries which criteria were met, unmet, or
  unjudgeable (`awino loop close`).

Each rendering is a few lines, not a dump.

## 5. Proof, not claims

Tests prove the machine runs; only outcomes prove the work mattered.

**Enforced by:** the outcome verdict (`awino loop close` records yes / partial /
no against the mission's success criteria, and `awino buddy` reports outcome
rates from those verdicts). And by the mission-is-living rule: the driver
hashes the success criteria when a loop starts, re-records the hash at every
phase boundary where validation succeeds, and at close, if the criteria
changed since the work was last examined, it prompts the human to update the
mission and re-examine first rather than judging against stale criteria.

## What is enforced vs. what is detected

Not everything above is a hard "no". The honest contract:

**ENFORCED — the code refuses to continue:**

- No valid research artifact → no plan phase (research validator).
- No human approval → no implementation (approval gate).
- Overlapping file ownership → the delegation is rejected before any process
  starts (`spawn.check_ownership`).
- A worker's completion claim → the closer re-runs the verification command
  itself; a claim alone is never trusted (`dispatch.close_floor`).
- No success criteria → the mission is invalid; `mission --set` refuses to
  call it complete (`heilmeier.validate_mission`).

**DETECTED — caught after the fact, attributed in the ledger:**

- Stance critic verdicts on response files.
- Buddy audit findings.
- Mission-drift flags on validated artifacts.

Detection names what happened and where; it does not stop the machine.

**HUMAN-JUDGED — only a person can decide:**

- Quality of thought. No validator can tell rigorous reasoning from a
  well-formatted performance of it.
- Whether the outcome was actually accomplished. This is the approval gate
  and the outcome verdict — a human reads the work and says yes / partial /
  no.

Code enforces process and evidence; it cannot enforce good thinking. The
machine says "no" at the gates above. Everywhere else it says "I saw that" —
and leaves the judgment to a human.

## 6. FAIR: a claim you can re-check beats a claim you must trust

A result nobody can reproduce is a rumor. When the work matters, the proof
travels with it: mission, approved plan (hash-bound), the ledger trail, the
test evidence, the outcome verdicts, and the brief — one directory, one
SHA-256 index. `awino proof export` builds the bundle; `awino proof verify`
re-checks it from the bundle alone, with no access to the original project.
A pack that fails verification says which file failed and why; a pack with
no approved plan says so instead of faking one.

**Enforced by:** the verifier (`awino/proof.py`), which rejects hash
mismatches, out-of-order trails, verdicts for loops the trail never ran, and
briefs whose claims are not in the pack.

## 7. One clean: the repo stays greppable

Dead code is a lie the codebase tells its readers — "this matters" about
something nothing uses. Undocumented commands are the same lie in reverse:
real behavior with no witness. Two tiers keep it honest without slowing the
day down: the fast tier (`buddy health`) runs static checks — ruff F401 for
dead imports, docs coverage for every command and skill, drift between the
generated command reference and live `--help`. The deep tier
(`buddy health --deep`) runs the suite under coverage and flags
modules/functions the suite never executed — candidates, not convictions,
because entry points and plugin hooks are intentionally unreferenced.
`buddy --fix` regenerates what is derivable (the command reference, from the
code itself) and marks the rest `[DRAFT]` for a human. It never deletes code
and never invents prose: removal and description are judgment, and judgment
stays human.

**Enforced by:** the hygiene checks (`awino/hygiene.py`), surfaced in the
default buddy report and on demand.

## 8. Engineering principles: SOLID, enforced by review and health checks

Principles nobody checks are decoration. Ours are checked the way the rest
of the philosophy is: by the machine where it can, by a human where only a
human can.

- **Single responsibility:** one module, one reason to change — the `owns:`
  declaration at the top of every CLI module says what it owns, and the
  layout test fails when a command is registered twice or nowhere.
- **Open/closed:** new task classes, skills, and loop kinds are added by
  adding files, not by editing the router; the skill registry is parsed from
  `SKILL.md` at load time, never hardcoded.
- **Substitution and interface segregation:** commands consume narrow
  helpers (`_echo`, `_paths`, `_workspace`), not the whole CLI object; the
  proof verifier depends only on the pack directory, never on project state.
- **Dependency direction:** doctrine flows one way — `MEMORY.md` records
  decisions, `PHILOSOPHY.md` states principles, and the code cites both; no
  module reaches around another's stated contract.

Ruff and the health gates catch the mechanical half (unused code, unsorted
imports, drift). The structural half is review: a change that edits the
router to add a leaf, duplicates a parser instead of sharing it, or hardcodes
what discovery already provides is rejected the same way a failing gate is —
with the reason named.

**Enforced by:** health checks and lint for the mechanical half; human review
for the structural half.

## The core loop (owner's words)

Mission → plan → challenge assumptions → understand (can explain and defend)
→ know the real problem → what's required (the Honda) → what else we can do
(beyond-Honda, effort-labeled) → capture everything → work toward the task.

This sits alongside the operational loop — mission → plan → approval →
execution → proof → outcome verdict — as the human-readable statement of the
same spine.
