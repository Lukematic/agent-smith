# A.W.I.N.O. User Guide

## Purpose and audience

This guide is for people who want to pair with an AI agent without becoming experts
in agent frameworks. It explains what A.W.I.N.O. shows, when it waits for approval,
how it proves work, and how to recover safely.

For the detailed explanation of every status field, the knowledge budget, mode
versus loop selection, task-class gates, and copy-ready interaction prompts, read
the [operating guide](operating-guide.md).

## What A.W.I.N.O. is

A.W.I.N.O. stands for **Agentic Workflow Intelligence & Navigation Orchestrator**.
It is the default, primary human-facing controller. It does not merely ask an agent
to be careful. It stores the objective and scope of a run, executes verification
commands, records exit codes, and computes whether the work may be called complete.

The primary controller can consult, discover requirements, research, plan, implement,
or delegate by loading a focused skill or using an isolated subagent. You do not need
to change modes for these capabilities.

### Optional specialist modes

Kilo and Roo can expose manual least-privilege modes. They are useful when you want
the editor itself to enforce a narrower boundary:

| Mode | Permission boundary |
| --- | --- |
| A.W.I.N.O. | Read, edit, command, and MCP access |
| A.W.I.N.O. Consult | Read and MCP only |
| A.W.I.N.O. Plan | Read, MCP, and Markdown-only edits; no commands |
| A.W.I.N.O. Discover | Read and MCP only |
| A.W.I.N.O. Research | Read and MCP only |
| A.W.I.N.O. Brain | Read, MCP, `awino brain` commands, and Markdown-only edits |
| A.W.I.N.O. Deep Plan | Read, MCP, `awino deepplan` commands, and Markdown-only edits |

A.W.I.N.O. cannot silently change the mode selected in your editor. The primary
controller can recommend a specialist mode, but it can continue by routing to the
matching skill unless you choose to switch.

### Brain mode

Brain mode is a thinking partner for a messy problem: a sponsor's ask in a
domain you don't know yet, or a new project space. Give it everything you have
and it walks seven stages, each a Markdown file with a structural check:

| Stage | What you get |
| --- | --- |
| problem | The problem in one plain sentence, facts vs assumptions, in and out of scope. **Pauses for your yes.** |
| you | What you bring, what's new to you, your blindspots, where your interests fit |
| options | What others have done, glaring holes, two or three options, a recommendation. **Pauses for your pick.** |
| plan | A chain of three to six steps ("if we do A, we can do B"), each with a check someone else can verify |
| report | `report.md`: two to four pages for a non-technical reader, with analogies and a clear ask |
| notes | `speaker-notes.md`: your 30-second version, slides with what to say, where they'll get lost |
| grow | What you learned and one blindspot to work on, added to your profile |

Call it with the 🧠 A.W.I.N.O. Brain mode in Kilo, the `/awino-brain` skill in
Claude Code, or directly:

```bash
awino brain start "Acme intake triage" --brief notes.md
awino brain              # where the session stands and the next stage's prompt
awino brain record problem
awino brain confirm problem --note "yes, but add the vendor question"
awino brain export ~/Documents/acme
```

The first time, Brain interviews you for a short profile (background,
strengths, interests, what you want to get better at). It lives only at
`~/.awino/brain/me.md` on your machine. Sessions live in `.awino/brain/`, which
ignores itself in git.

### Deep Plan mode

Deep Plan is for thinking a change through before anything is built, and it is a
challenge for both of you: what is the problem, how will we know it is done, what
are its parts, how do we get there, and what is needed. Then it executes a plan
the harness holds the work to, with the steps tracked in Seeds. Brain is for a
problem space and a report for other people; Deep Plan is for work you are about
to do in this codebase.

| Stage | What happens |
| --- | --- |
| frame | **Define the problem**: what is wrong today, with `Evidence:` (a problem, not a fix in disguise); the goal; `Done when` criteria `C1:`, `C2:`...; the problem broken into parts; what's needed (people, access, decisions, data); constraints; out of scope |
| grill | One question at a time, each with the agent's recommended answer and why it matters, and at least one **challenge** (a vague answer, a solution posing as the problem, scope creep, the riskiest assumption). It reads the code instead of asking when it can (`learn` needs a real `file:line`). At least three of your answers, or a stated reason why fewer is enough |
| directions | At least five variations through named lenses, two or three real directions, a recommendation. **Pauses for your pick.** |
| recon | At least three real `file:line` references, what to reuse, the test command that runs today, the risks |
| slices | **Steps by priority**: two to twelve thin steps, P0 (done needs it), P1 (next), P2 (later), listed P0 first, each with its files (five at most), the earlier steps it depends on, a `Verify:` command that runs here, and the criteria it covers. Every criterion needs a P0 step |
| redteam | Three ways it fails (warning sign and mitigation each), the strongest objections with answers, and the rollback |
| plan | `plan.md` in the format the harness checks, problem and priorities first. **Pauses for your approval.** |
| seeds | `awino deepplan seeds`: an epic, one seed per step with its priority and dependencies, linked as a Seeds plan, so `sd ready` shows what can start; each verified step closes its seed, and the epic closes when every step is verified (`--init` creates a tracker if you want one) |

```bash
awino deepplan start "per-user notes" --brief notes.md
awino deepplan                 # where it stands, the next stage, or the open question
awino deepplan ask "Should notes be private by default?" \
  --recommend "Yes, private; sharing is a later slice" --why "decides the data model"
awino deepplan answer "private, and admins can't read them either"
awino deepplan ask "You said 'fast'. Under what number, for whom?" --challenge \
  --recommend "under 200 ms for the list page" --why "decides whether we need a cache"
awino deepplan answer "200 ms is fine"
awino deepplan grill-done
awino deepplan record directions
awino deepplan confirm directions --note "go with B, skip the export"
awino deepplan compile
awino deepplan seeds           # the steps in Seeds, with priorities and dependencies
awino deepplan go --by "Luke" --note "approved, start with slice 1"
awino deepplan done 1          # after building phase 1: runs its check, ticks it and closes its seed
```

`go` opens a gated run bound to the exact bytes of `plan.md` and to the files the
slices name. `done <n>` runs a phase's own success command as gate evidence,
ticks it off in `progress.md` and closes its seed only when it passes; a phase
waits for what it depends on, and P0 phases come before P1 and P2. Done is every
P0 phase verified: `awino gate close` refuses before that and lists open P1/P2
phases as follow-ups. Anything outside the scope goes to a follow-up list in
`progress.md`, never into the run.

When reality breaks the plan mid-run, the agent stops and asks instead of
loosening a check. Re-recording a stage withdraws the approval; after you approve
the changed plan, `go` pauses the old run, and steps that were already verified
and did not change are checked again on the new run before they count.

Sessions live in `thoughts/plans/` so the reasoning can be committed with the work.

## Install and verify

### Native Claude Code plugin

Run the installation action yourself in Claude Code:

```text
/plugin marketplace add Lukematic/agent-smith
/plugin install awino@awino
/reload-plugins
```

Or use the native terminal commands:

```bash
claude plugin marketplace add Lukematic/agent-smith
claude plugin install awino@awino
```

This installs and enables the `awino` agent and exactly 14 canonical `awino-*`
skills. A URL pasted into chat is not installation authorization: Claude Code's
plugin trust boundary requires the user to perform one explicit install action.
The agent must not replace that action with a global Bash mutation.

Plugin installation does not create `.seeds`, `.awino`, or any project-local state.
In a new project, A.W.I.N.O. asks for approval before using `awino work-init`.

The agent and skills need no Python dependency. The deterministic ledger CLI needs
`uv`. The cross-platform launcher runs `uv run --frozen --no-dev`, which creates or
refreshes a version-specific locked `.venv` automatically after plugin installs and
updates. If `uv` is absent or preparation fails, it prints `DEGRADED`; agent and
skill guidance remains available, but command-backed ledger claims do not.

The launcher isolates that backend environment from the current project's activated
environment; it does not deactivate or alter the parent shell. Target commands still
run from the target root. For a target with committed `pyproject.toml` and `uv.lock`,
`uv run` selects the target's `.venv` from that working directory, so `--active` and
manual activation are unnecessary. `awino env` reports the detected manager and
optional activation commands. `awino setup --dry-run` reports required setup without
creating anything; only an explicit real `awino setup` may create `.venv`.

`Python` is the interpreter, `venv` is an isolated directory for one project's
packages, `pip` installs packages into an interpreter environment, and `uv` manages
locked dependencies and runs commands in the environment selected by the project
directory. A.W.I.N.O. does not create a target environment during onboarding.

Update or uninstall through the same trusted plugin interface:

```text
/plugin
```

Or run `claude plugin update awino@awino` and
`claude plugin uninstall awino@awino`. Run `/reload-plugins` after an update.

Claude Code 2.1.186 or later is required for the plugin `settings.json` default
agent used by this release. Older clients may still discover components but must
select the `awino` agent manually.

### Prerequisite

The standalone CLI path requires Git. Its installer can provision `uv` and the isolated Python
environment. Optional tools do not block installation.

### From a clone

```bash
git clone https://github.com/Lukematic/agent-smith.git awino
cd awino
./install.sh
```

On Windows, run `./install.ps1` in PowerShell. To skip optional tooling, use
`./install.sh --no-tools` or `./install.ps1 -NoTools`.

The installer also writes `~/.local/bin/awino.ps1` (POSIX: `~/.local/bin/awino`
or your existing shell `PATH` entry from `bin/awino`), a small wrapper that
forwards to this clone's own launcher. After this, run `awino start`, `awino
best`, and `awino update` from **any** project directory - the wrapper always
uses this clone's locked `.venv` and treats your current directory as the
target project. It never creates a Python environment in that project.

Verify the persona, skills, modes, and project health:

```bash
awino install-status
awino mode-status
awino doctor
```

Install or refresh selectable modes when needed:

```bash
awino install-mode
awino install-mode --editor kilo
```

Reload the editor window after installing modes.

## Safe updates and rollback

`awino start` reports cached freshness against the upstream branch
(`ahead=N behind=M`) using only local Git metadata; it never pulls on its own,
so starting a session cannot change the code underneath an active run.

### Staying current automatically

`awino update` is the one command. It updates everything this machine has:
the Claude Code plugin (if installed), the clone that Kilo, Roo and the terminal
run from, the skill copies in every harness, this project's Kilo agent, and
A.W.I.N.O.'s own Kilo/Roo modes. After pulling, it hands the rest of the update to
the code it just pulled, so a release that changes how updating works takes
effect in that same run.

The first `awino update` also turns on **automatic updates**: each new session
checks GitHub at most once a day and, when a new version is waiting, installs it
(the same as running `awino update`) and says `AUTO_UPDATED`. It never updates
while a run is open in the project, and local changes in the clone are set aside
and put back. Choose how much you want:

```bash
awino update --auto on       # check daily and install (the default after the first update)
awino update --auto notify   # check daily and only say UPDATE AVAILABLE
awino update --auto off      # never contact GitHub on startup
awino update --check         # look right now, change nothing
```

Each new chat also refreshes its project's `.kilo/agent/awino.md` when it is out
of date and still exactly as A.W.I.N.O. wrote it, so every project catches up as
you use it. A.W.I.N.O.'s own modes are rewritten to the new version (the modes
file is backed up first); your own modes are never touched.

**Claude Code plugin:** Claude Code can also update the plugin on its own: run
`/plugin`, open **Marketplaces**, select **awino**, and choose **Enable
auto-update**. Claude Code loads the new version in your next session (or after
`/reload-plugins`).

If your clone has local changes, `awino update` refuses and names the files.
They are safe. `awino update --keep-local` backs up, sets them aside, updates,
and puts them back; if one conflicts with the new version, the clone stays on
the new version and your change stays in `git stash list`, never lost. When both
the Claude Code plugin and a clone are installed, `awino update` updates both.


To update A.W.I.N.O. itself, say "update A.W.I.N.O." or run:

```bash
awino update
```

It detects whether you are using the Claude Code plugin or a standalone
clone, and runs the correct procedure for either automatically - the Claude
plugin's own update commands, or a backup-first fast-forward git pull. It
always prints the resulting version at the end, so you never need a separate
version check.

For a standalone clone specifically, the preflight command is available on
its own instead of pulling directly:

```bash
awino update-preflight
```

It first prints a `BACKUP` path. It then refuses to pull if the source clone is
dirty, has local commits ahead of upstream, lacks an upstream branch, cannot be
compared with upstream, or cannot fast-forward. It never performs a merge update.
The backup includes recognized project run, plan, mission, memory, specification,
and Seeds state plus detected harness files.

Create a backup without fetching or pulling:

```bash
awino update-preflight --no-pull
```

After an update, verify health and refresh the knowledge cache:

```bash
awino doctor
awino knowledge-update
```

Restore project-owned state from the exact printed path:

```bash
awino rollback <BACKUP>
```

Project state is the safe default. Restore detected editor persona and mode files
only when that is intentional:

```bash
awino rollback <BACKUP> --include-harness
```

Rollback restores files contained in the snapshot; it is not a Git source-code
rollback.

## Your first session

In a new project, first approve tracker initialization if wanted:

```bash
awino work-init
awino onboard
```

The primary controller should display this orientation before substantive work:

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

`awino onboard` combines project context, mission, toolchain, tracker state, and
skill selection. If intent is incomplete, it asks one frontier question and gives
the exact `awino onboard --set key=value` form to answer it. Partial answers persist.
When every required field is present, confirm them:

```bash
awino onboard --confirm
```

A derived mission is evidence-based but not human-confirmed. Expect A.W.I.N.O. to
seek confirmation before treating it as authoritative.

## The pair workflow

Start every session with one command:

```bash
awino start
```

`start` composes `context`, `mission`, `doctor --fast`, `resume`, and skill
routing into a single report: project, mission confidence, toolchain, tracker,
active run, pending human decision, next recommended action, and route skill.
It is read-only unless `--fix` is passed, and even then only mechanical
repairs run. Use `context`, `mission`, `doctor`, and `resume` individually
when you need one of those reads in isolation.

Use this sequence for non-trivial work:

1. **Orient.** Run `awino start`; use `awino context`, `awino mission`, or
   `awino doctor` when you need to inspect one of those reads in isolation.
2. **Select work.** Run `awino work` if the project uses Seeds, or state the task
   directly if it does not.
3. **Assess the approach.** Run `awino plan "<request>"`. This is an analysis of
   leverage, constraints, verifier strength, autonomy, and fan-out readiness; it
   does not create the approval-bound plan file used by a gated run.
4. **Review a written plan.** For task classes that require planning, create the
   plan document before opening the run.
5. **Open the run.** Declare the objective, write scope, plan path, and optional
   Seeds issue.
6. **Approve the exact plan.** A human approval records the plan hash and scope.
7. **Work in phases.** Record skills honestly and checkpoint durable progress.
8. **Verify.** Execute each required gate command and run independent diff checks.
9. **Close.** Let the gate ledger decide whether the run and linked issue may close.

Example:

```bash
awino gate open code-change "add retry behavior" --scope src/client.py --scope tests/test_client.py --plan thoughts/plans/retry.md --issue seed-123
awino gate plan approve --by "project owner"
awino gate skill awino-rpi --state loaded --reason "multi-file planned change"
awino gate skill awino-rpi --state used --reason "executed approved phases"
awino gate record tested --cmd "uv run pytest"
awino gate record linted --cmd "uv run ruff check src tests"
awino gate check --diff-base HEAD
awino gate close
awino work-close
```

Use commands appropriate to your project; `uv run pytest` and Ruff are examples,
not universal requirements.

## Plans and hash-bound approval

`code-change`, `refactor`, and `authoring` contracts require the `planned` gate.
Such runs must open with `--plan`. Approve, hold, reject, or inspect the current
plan decision with:

```bash
awino gate plan approve --by "name" --reason "scope and checks accepted"
awino gate plan hold --by "name" --reason "add rollback steps"
awino gate plan reject --by "name" --reason "wrong approach"
awino gate plan status
```

Approval binds three things: the exact plan bytes by SHA-256, the plan path, and
the declared write scope. Editing the plan, changing its path, or changing scope
invalidates approval. A.W.I.N.O. then refuses later evidence until the plan is
reviewed again.

## Seeds glossary

Seeds is an optional Git-native issue tracker accessed through the `sd` command.
A.W.I.N.O. works without it and will not initialize it without permission.

| Term | Meaning |
| --- | --- |
| **seed** | One tracked issue: a task, bug, feature, or verification request. |
| **ready** | An open seed with no unresolved blockers; `awino work` lists these. |
| **blocked** | A seed whose unresolved dependencies prevent it from being ready. |
| **in-progress** | A linked seed after the run executes its first command-backed gate. |
| **close** | Finish a seed with an auditable reason derived from gate evidence. |
| **dependencies** | Relationships that must be resolved before a seed appears as ready. Seeds owns this graph; A.W.I.N.O. reads its ready result. |

Initialize Seeds only after reviewing the stated repository changes:

```bash
awino work-init
awino work-init --confirm
```

`awino work-close` refuses closure when gates are unmet or all evidence is merely
attested. `--force` exists, but records the shortfall in the close reason.

## Gates and evidence

The task class chooses the required gates. Inspect the contracts:

```bash
awino gate contracts
```

Record command-backed evidence whenever a command can test the claim:

```bash
awino gate record tested --cmd "<project test command>"
```

A.W.I.N.O. stores the real exit code, duration, attempt number, command, and output
excerpt. `--attest` is available for claims with no executable check, but the status
and close output identify attestations as weaker evidence.

```bash
awino gate record researched --attest "sources and findings recorded in report.md"
```

Use `awino gate check --diff-base <git-ref>` to detect test weakening and files
outside declared scope. If Git cannot produce the diff, the check fails rather than
treating an empty result as proof.

`awino gate close` refuses if evidence is missing or failing. Do not treat a draft,
agent claim, or successful-looking output as completion.

## Skills: recommended, loaded, and used

These states are deliberately different:

- **Recommended:** `awino skills --route "<request>"` selected a likely skill. If a
  run is active, the recommendation is recorded automatically.
- **Loaded:** the agent actually loaded the canonical skill instructions.
- **Used:** the agent followed that skill in the work.

List or route canonical skills:

```bash
awino skills
awino skills --route "design a multi-agent handoff"
```

Record only truthful state transitions:

```bash
awino gate skill awino-delegate --state loaded --reason "parallel disjoint work"
awino gate skill awino-delegate --state used --reason "assignments dispatched"
```

A recommendation is not proof that a skill was loaded or used.

## Proof packs and continuous hygiene

Two mechanisms keep claims honest after the work is done.

**Proof packs.** `awino proof export` bundles the mission, the hash-bound
approved plan, the ledger trail, the test evidence, the outcome verdicts, and
the stakeholder brief into one directory with a SHA-256 index of every file.
`awino proof verify <pack-dir>` re-checks the whole bundle from the pack alone —
no project access needed. It fails and names the file when a hash does not
match, when the trail is out of order, when a verdict names a loop the trail
never ran, or when the brief claims something the pack does not contain.

```bash
awino proof export --out ./proof-pack
awino proof verify ./proof-pack   # PROOF OK, or PROOF INVALID naming the file
```

**Continuous hygiene.** `awino buddy` reports state health; it now also reports
repo health on three lenses: dead code, docs coverage (every command and every
skill documented), and docs drift (the generated command reference still
matches live `--help`). Two tiers for dead code:

- **Fast (default):** static. Ruff F401 over the project — unused imports are
  the cheapest reliable dead-code signal. Seconds, no test run.
- **Deep (`awino buddy health --deep`):** runs the full suite under coverage and
  flags modules and functions the suite never executed. Minutes, and a
  candidate is not a conviction: entry points, plugin hooks, and human
  vocabulary can be intentionally unreferenced.

`awino buddy --fix` regenerates `docs/commands.md` from live `--help` when the
reference drifts — the content comes from the code, never invented; commands
with no help text are marked `[DRAFT]` for a human to describe. It never
deletes code and never invents prose: dead-code findings stay prompts for a
human. Honest limit: the drift check compares the generated reference against
live help, so it catches wording changes and missing rows, not behavioral
changes the help text does not describe.

## Checkpoint and resume

Persist a phase boundary before stopping or compacting a session:

```bash
awino gate checkpoint --phase implementation --summary "parser complete; tests pending" --next "run parser tests"
```

If a human decision blocks progress, declare the question and allowed choices:

```bash
awino gate checkpoint --phase design --summary "two storage choices remain" --next "implement selected choice" --pending "Choose storage" --option sqlite --option postgres
awino gate decide sqlite --by "project owner"
```

Only one unresolved checkpoint decision may exist. Resume from durable state with:

```bash
awino resume
```

It reports the active run, objective, linked issue, plan validity, latest checkpoint,
pending decision, and next action. A closed run may appear as stale rather than active.

## Expected communication

Expect the controller to:

- lead with `[A.W.I.N.O. | mode: ... | run: ... | knowledge: ...]`;
- show the first-session orientation fields;
- state the route or loop and why it fits;
- distinguish confirmed facts, sourced claims, and inference;
- name a blocker and its remedy instead of hiding it;
- expose pending decisions with bounded options;
- report commands and actual evidence, not confidence language; and
- refuse to say work is complete until `awino gate close` succeeds.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Wrong project or toolchain | Run `awino context`; start inside the intended project before opening a run. |
| Mission is unknown or derived | Run `awino onboard`, answer the next field with `--set`, then `--confirm`. |
| Persona or skills missing | Run `awino install-status`, then `awino install --force` if a refresh is intended. |
| Modes missing | Run `awino mode-status`, `awino install-mode`, then reload the editor. |
| Health refuses | Run `awino doctor` without `--fast`, follow each printed remedy, and rerun it. |
| Seeds unavailable | Continue without tracking, or install `sd` and run `awino work-init` with approval. |
| Plan suddenly invalid | Run `awino gate plan status`; review and approve the changed bytes/path/scope again. |
| Gate fails | Fix the cause and record it again. Stop and escalate after the third failure on that gate. |
| Session lost context | Run `awino resume`; if no checkpoint exists, inspect `awino gate status`. |
| Update refused | Preserve the printed backup. Clean or reconcile the source clone; do not bypass fast-forward-only safety. |
| Need to undo restored project state | Use project version control; `awino rollback` only copies snapshot contents back. |

## Concise command reference

| Command | Purpose |
| --- | --- |
| `awino --help` | List every supported command. |
| `awino start` | Single startup command: project, mission, toolchain, tracker, active run, next action, route skill. |
| `awino onboard` | Run the mission-first project handshake. |
| `awino context` | Show resolved home, project, and toolchain. |
| `awino doctor` | Check health and refuse on blocking failures. |
| `awino install-status` | Show persona and skill installation status. |
| `awino mode-status` | Show selectable mode installation status. |
| `awino plan "<request>"` | Analyze approach, verifier strength, autonomy, and fan-out readiness. |
| `awino skills [--route "<request>"]` | List or recommend canonical skills, each with purpose and one-line when-to-use. |
| `awino work` | List ready Seeds work when available. |
| `awino gate open ...` | Open a run with class, objective, scope, plan, and optional issue. |
| `awino gate plan ...` | Approve, hold, reject, or inspect an exact plan. |
| `awino gate record ...` | Execute and record evidence, or add an attestation. |
| `awino gate check --diff-base <ref>` | Check test weakening and declared scope. |
| `awino gate checkpoint ...` | Persist phase, summary, next action, and optional decision. |
| `awino resume` | Display durable continuation state. |
| `awino gate status` | Show gate progress and blockers. |
| `awino gate close` | Compute whether the run can close. |
| `awino proof export [--out <dir>]` | Export a FAIR proof pack: mission, approved-plan hash, ledger trail, test evidence, verdicts, brief, SHA-256 index. |
| `awino proof verify <pack-dir>` | Re-verify a proof pack from the pack alone; names the file on failure. |
| `awino buddy health [--deep]` | Repo hygiene: dead code, docs coverage, docs drift. `--deep` runs the suite under coverage. |
| `awino work-close` | Close a linked seed using gate evidence. |
| `awino update-preflight` | Snapshot state and attempt a safe fast-forward update. |
| `awino rollback <BACKUP>` | Restore project state from a preflight snapshot. |

## References

- [Operating guide](operating-guide.md)
- [Agent and harness guide](agent-guide.md)
- [Detailed installation](install.md)
- [Gate enforcement](enforcement.md)
- [Canonical skill catalog](skills.md)
