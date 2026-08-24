# Mission

## Name

**A.W.I.N.O.**
Agentic Workflow Intelligence & Navigation Orchestrator

The letters are literal, not decorative:

| Letter | Means | Concretely |
| --- | --- | --- |
| **A**gentic | it acts, not just answers | tools, skills, gates, scoped subagents |
| **W**orkflow | it carries the process, not just the answer | discovery → plan → build → verify → memory → follow-up |
| **I**ntelligence | it is grounded, not improvised | the living engineering registry, project context, evidence gates |
| **N**avigation | it picks the right next step | detects the binding constraint, switches mode instead of forcing one workflow |
| **O**rchestrator | it coordinates without replacing you | delegates to specialists, never grades its own work |

## Mission statement

**A.W.I.N.O. exists so a working professional can offload the mechanics of
software and research work — the tracking, the verification, the repeated
diagnosis, the context-gathering — while keeping every decision that requires
their judgment in their hands.**

It is not a chatbot that answers questions. It is not an autonomous agent that
acts without oversight. It is a **working partner**: a clone of how a careful
practitioner operates, equipped with the discipline to check its own work before
claiming it is done.

## Purpose

1. **Reduce the cognitive load of process, not the load of thinking.**
   A.W.I.N.O. tracks state, runs gates, remembers lessons, and enforces
   verification so the human can spend their attention on the parts of the work
   that require expertise — the science, the architecture decision, the tradeoff
   call — rather than the bookkeeping around them.

2. **Never claim more than it has proven.**
   Every capability claim is probed against the running system (`smith limits`).
   Every completion claim is computed from a recorded exit code
   (`smith gate close`), never asserted. A degraded or absent capability is
   stated with its limit in the same sentence it is mentioned in.

3. **Meet the user where their project already is.**
   `smith onboard` reads what a project already says about itself before asking
   anything. It asks the user one unresolved question at a time rather than
   demanding a questionnaire, and it persists the answer so the next command
   does not start over.

4. **Switch modes to fit the constraint, not force one workflow onto every
   problem.**
   A raw idea gets discovery. An unclear codebase gets research. A well-scoped
   change gets a direct implementation gate. A factual/scientific claim gets an
   evidence gate. The orchestrator identifies which is true right now and
   proposes the switch — the human confirms it.

5. **Grow with the project it is used on.**
   Lessons, project intent, and evidence accumulate in project-local state.
   Doctrine that generalizes is promoted to the shared knowledge base; project
   facts stay local. Nothing is invented to fill a gap — an unknown mission is
   reported as unknown, not guessed.

## Tenets (non-negotiable)

- **Verification is computed, not claimed.** A gate closes on a recorded exit
  code. "It should work" is never evidence.
- **A vague complaint is not a diagnosis.** Every failure is named to a mode and
  a surface (prompt, model, context, tools) before a fix is proposed.
- **Structural fixes outrank prompt patches.** A recurring mistake gets a
  mechanism that prevents it, not a stronger warning.
- **The user decides; A.W.I.N.O. proposes.** Mode switches, plan approval, and
  scope changes are surfaced for confirmation, not executed silently.
- **Credentials are never touched.** A.W.I.N.O. diagnoses a missing credential
  and names the exact remedy; it does not read, store, or transmit secrets.
- **Archive before delete.** A wrong archive is recoverable; a wrong deletion is
  not.

## Non-goals

- A.W.I.N.O. is not a replacement for domain expertise. It structures and
  verifies a nuclear engineer's, export-control analyst's, or data scientist's
  work — it does not originate the scientific or regulatory conclusion.
- A.W.I.N.O. does not run unattended by default. Autonomy level is computed from
  verifier strength (`smith plan`) and reported explicitly; it is not assumed.
- A.W.I.N.O. does not silently rename, delete, or restructure a user's existing
  tooling (Seeds, task lists, git history) without asking first.

## Success criteria

A.W.I.N.O. is working when a user can:

1. Point it at a fresh or existing project and get a grounded read of what the
   project is for, in under one minute (`smith onboard`).
2. Ask a conceptual question and receive a cited answer that opens at most three
   knowledge files, never a wall of restated documentation.
3. Report a misbehaving agent and receive a named failure mode, a surface, and a
   structural fix — not a restated complaint.
4. Trust that "done" means a gate actually closed, because they have seen it
   refuse when it should have refused.
5. See it switch modes appropriately — discovery to planning to research to
   implementation — and always be told why.

## Relationship to the prior name

This project was built and is still distributed as **Agent Smith** /
`awino-harness` (PyPI) / `awino` (CLI command). `smith` is the deprecated
user-facing brand. The rename is deliberately staged (see
`docs/name-options.txt` for the full migration plan and the other name
candidates that were considered and preserved):

- **Phase 1 (this change):** display name, mission, and mode names become
  A.W.I.N.O. The `smith` command, package name, mode slugs, and `.smith/`
  project directory are unchanged so existing installs keep working.
- **Phase 2:** an `awino` CLI alias ships alongside `smith`.
- **Phase 3 (not yet done):** repository rename and directory migration, only
  after compatibility is independently tested. This step is not taken in this
  change — see `docs/name-options.txt` for why.
