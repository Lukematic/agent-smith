---
name: awino-discover

description: Mission-first discovery for a raw idea or sparse repository. Reflects the project purpose, asks one frontier question at a time, captures goals tenets expectations non-goals and success criteria, and refuses to rush into a spec before the user confirms intent.
---

# A.W.I.N.O. Discover

Use before `awino-rpi` when the user has an idea but the product mission and
acceptance boundary are not yet explicit.

This composes the strongest portable parts of the local `idea-refine`,
`requirements-analyst`, and `plan-interrogate` skills without copying their
framework-specific paths or turning A.W.I.N.O. into a giant always-loaded prompt.

## Principles

- Start with the user experience and work backwards to technology.
- Ask **one question at a time**. Later questions must build on answers already
  settled.
- Restate each decision and allow correction. A silent interpretation is not a
  confirmed requirement.
- “I don't know” is valid. If a question needs something to react to, stop asking
  and propose the smallest disposable prototype.
- Simplicity wins: identify the smallest result that proves usefulness.
- A refined “no” is more valuable than a forced build.

## Process

### 1. Detect before asking

Run:

```bash
awino context
awino mission
awino onboard
```

Do not ask for facts already stated in project instructions, metadata, README,
non-goals, or tracked work. Reflect the mission draft and its source/confidence.

### 2. Resolve the frontier

The required frontier is:

1. Mission — what outcome should this create?
2. Primary user — who exactly, and what do they do today?
3. Goals — smallest useful outcomes, not a feature inventory.
4. Tenets — what must never be violated?
5. Expectations — cost, quality, privacy, deployment, usability.
6. Success metric — what observation proves v1 is useful?

Ask one unresolved question at a time, but continue until the planning frontier is
actually resolved. A single question followed by shallow planning is
`UNDER_INTERVIEWED_PLAN`. Persist each answer with:

```bash
awino onboard --set key="answer"
```

For list fields (`goals`, `tenets`, `expectations`, `non_goals`), separate items
with semicolons.

### 3. Diverge briefly

After the mission is understood, present exactly three directions:

- **Minimal** — smallest thing that proves the mission.
- **Bold** — higher upside, naming its hardest assumption.
- **Adjacent** — solves the same pain from another angle.

For each, name the current workaround it competes with. Do not list technology
unless technology is itself a constraint.

### 4. Converge with the user

Evaluate directions against:

- user value and frequency;
- feasibility and novel-hard dependencies;
- differentiation from the current workaround;
- minimum evidence that would prove the idea;
- what the user does when it fails.

Ask the user to choose, combine, or reject. Do not choose silently.

### 4a. Grill the plan adaptively

After the direction is chosen, run a pair-programming interview before writing a
spec or plan. Ask one question per turn, but cover every relevant category:

1. desired behavior and concrete example;
2. current behavior or failure reproduction;
3. edge cases and failure handling;
4. affected users, systems, and data;
5. compatibility, security, privacy, and performance constraints;
6. acceptance checks and what the human will inspect first;
7. explicit non-goals and deferred work;
8. project workflow prerequisites such as issue, branch, and changelog rules.

Skip a category only when repository evidence or a prior confirmed answer settles
it. Summarize decisions every 3-4 answers and let the user correct them. Do not stop
because the model feels ready; stop when the decision record has no material open
question or the user explicitly says to proceed.

### 5. Confirm intent

Once required fields are present:

```bash
awino onboard --confirm
```

This writes `.smith/project.yaml`, the project-local source of truth for confirmed
intent. Then hand off to `awino-rpi` for repository research.

## Failure Modes

| Mode | Definition |
| --- | --- |
| `MISSION_FABRICATION` | treating an inferred purpose as human-confirmed |
| `QUESTION_DRIP` | asking a long questionnaire instead of one frontier question |
| `PLAN_RUSH` | converting the first idea into a plan before intent is settled |
| `TECHNOLOGY_FIRST` | choosing frameworks before the user outcome |
| `PASSIVE_AGREEMENT` | the user never pushes back because alternatives were never exposed |
| `UNDER_INTERVIEWED_PLAN` | planning starts after one or two questions while material decisions remain |
| `UNGRILLABLE_SPIRAL` | repeatedly asking about something that needs a prototype |
| `VITAMIN_BUILD` | building a low-frequency nice-to-have without naming why it matters |

## Completion

Done when:

- `.smith/project.yaml` exists with `source: confirmed`;
- mission, primary user, at least one goal, at least one tenet, and a success
  metric are present;
- the user chose or rejected the recommended direction;
- explicit non-goals are recorded;
- no implementation file was changed.

Verify with:

```bash
awino onboard --json
```

Grounding: chapters/4-context/3-context-patterns.md,
chapters/7-patterns/1-plan-build-review.md,
chapters/9-mental-models/3-specs-as-source-code.md,
chapters/12-long-horizon-agent-state/3-memory-and-intent.md
