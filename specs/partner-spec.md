# Partner Spec — from tool to partner, with nothing silent

Status: **awaiting approval**. No implementation started. Every claim below was
measured on 2026-09-03 with the command shown; nothing is remembered.

Rule for this whole spec: **a step is done when the live command is pasted, not when
a test asserts.** Every phase has two proofs: a pytest (regression guard) and a live
run in a throwaway repo (the actual claim). Missing either = not done.

---

## 0. Defect list — measured, with the fix for each

| # | Defect | How I measured it | Fix | Phase |
| --- | --- | --- | --- | --- |
| D1 | **Dispatched worker never receives the skill it was routed to.** `_build_assignment` puts the skill *name* in the objective and passes no files. The elevator presses the button; the floor is empty. | `Select-String dispatch.py context_paths` → **0** | `context_paths=[smith_home/skills/<skill>/SKILL.md]`; objective says "follow this skill's procedure" | 1 |
| D2 | **Default `--verify` accepts any claim.** `python -c "raise SystemExit(0)"` always passes; a warning was added, which is a prompt patch. | `cli.py:3173` | `--verify` required, exit 2 if absent. Default *discovery*: use `just test` / `make test` / `pytest` when found, print which; refuse if none | 2 |
| D3 | **No integration test spawns a real `claude`.** Every S3/S4 test faked `Runner.command`. The one boundary that fails in production was never crossed. | reviewer R-list; grep tests for `monkeypatch.setattr(Runner, "command"` | `tests/integration/test_real_dispatch.py`, `@pytest.mark.integration`, skipped if `claude` absent | 3 |
| D4 | **`awino start` in a bare repo does nothing.** Reports `Tracker: none (run 'sd init' yourself)`, exit 0, creates neither `.smith/` nor `.venv`. `--fix` only calls `fix_scaffold(paths)` — on A.W.I.N.O.'s own home, not the project. | live T6: `.smith created: False`, `.venv created: False` | `start --fix` scaffolds project `.smith/`, creates venv when `pyproject.toml` exists, **asks one question** for each decision it cannot make alone | 4 |
| D5 | **Missing `pyproject.toml` / `requirements` is treated as "not my problem."** Nothing offers to create one. | `project-bootstrap` output: `setup command: unavailable` | `start --fix` offers `uv init` when no project file exists; on yes, runs it and re-checks | 4 |
| D6 | **`awino update` preserves `project.yaml`, `memory/`, `run/` but does not re-provision.** A repo set up under v0.1 that lacks `.venv`/`pyproject` stays that way after update. | `updater.py` snapshot list has state only, no provisioning step | after restore, `update` calls the same provisioning routine as `start --fix` and prints each action | 4 |
| D7 | **No stance concept exists anywhere.** Your seven modes (expert, friend, advisor, einstein, first-principles, teach-back, research-intake) are not in code, persona, or modes. | grep `stance` in src+persona → 13 hits, all `isinstance` | `stance.py`: fixed catalog of stances with triggers; persona block per stance; `--stance`; auto-switch on trigger | 5 |
| D8 | **Advisor stance is prose the model can ignore.** Persona still contains apology vocabulary. | grep `sorry\|you're right` in `agents/awino.md` → 2 | remove; add `_apology_lint` in `awino hook prompt` that flags the assistant's last reply if it opens with a validation phrase (Claude only; Kilo/Roo documented as persona-dependent) | 5 |
| D9 | **Claude `UserPromptSubmit` hook does not call dispatch.** Docs now say so honestly; the capability is still unbuilt. | `hook_prompt` body contains no `dispatch.` | hook runs `dispatch.decide` + stance trigger detection and **injects** `MATCHED <skill> / STANCE <name>` into context; never spawns | 6 |
| D10 | **"Autonomous execution" is a spec, not code.** | no `auto` command in `awino --help` | `awino auto --max-seeds N --confirm-budget` | 7 |
| D11 | **Knowledge header `0/3` reports my chapter reads, not skill use.** Correct but uninformative to you. | header format | header gains `skill: <name>` and `stance: <name>` when active | 1, 5 |

---

## 1. Definitions that stop the arguing later

- **Skill** — a procedure a *worker* follows. Lives in `skills/`. Loaded into the spawned
  agent's prompt (D1), never into the controller.
- **Stance** — how the *controller* talks to you. Lives in `stance.py` + persona. Costs
  no knowledge budget. Switches on trigger words in *your* message, or on phase.
- **Silent** — any write, install, init, or switch that produced no printed line
  naming what changed. Forbidden everywhere in this spec.
- **Ask one question** — print exactly one `QUESTION  …` line with the concrete
  options and exit 3. Never a wall of options.

---

## 2. Phases

### Phase 0 — One real dispatch trip (blocks everything)

**Live proof (paste all of it):**
```powershell
cd .smith
git checkout -b field-test-p0
uv run awino gate open code-change "add a marker line to a scratch file" --scope .smith/scratch/marker.txt --plan specs/partner-spec.md --by awino
uv run awino gate plan approve --by user --reason "phase 0"
uv run awino dispatch "create scratch/marker.txt containing the single line PHASE0-OK" --confirm-budget --scope .smith/scratch/marker.txt --verify "python -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('scratch/marker.txt').read_text().strip()=='PHASE0-OK' else 1)\""
git diff --stat
uv run awino gate status
```
**Exit:** output shows `MATCHED`, `BUDGET_CONFIRMED`, one floor with a UUID
`invocation_id`, `verified=True`, `COMPLETE`; `git diff` shows the file the *agent*
wrote. If any of that is missing, **the failure is the Phase 0 spec** and nothing
else starts.

### Phase 1 — The worker gets its skill (D1, D11)

**Change:** `dispatch._build_assignment` adds
`context_paths=[str(smith_home/"skills"/skill/"SKILL.md")]` and objective text
"Follow the procedure in the attached SKILL.md exactly." Header prints `skill: <name>`
during an active dispatch.

**Tests:** assignment for `awino-debug` carries the path; generated prompt file
contains the literal first heading of `awino-debug/SKILL.md`.

**Live proof:** rerun Phase 0's dispatch with `--dry-run --show-prompt` (new flag,
prints the prompt file) and paste the section showing SKILL.md text inside it.

### Phase 2 — Verify cannot be tautological (D2)

**Change:** `--verify` optional but **discovered, never defaulted to true**: order
`just test` → `make test` → `pytest` if `pyproject.toml` has `[tool.pytest]` → else
`REFUSED  no verification command found; pass --verify`. Print `VERIFY  <cmd> (from
justfile)` before spawning.

**Tests:** three fixture repos (justfile / Makefile / neither); neither → exit 2.

**Live proof:** run dispatch in this repo without `--verify`; paste the `VERIFY  just
test (from justfile)` line. Run in a bare temp repo; paste the `REFUSED`.

### Phase 3 — Real-subprocess integration test (D3)

`tests/integration/test_real_dispatch.py`: creates a temp repo, dispatches "add one
line to notes.txt" with `--verify` checking the line, asserts `COMPLETE`, the line
exists, and two floors would have had distinct invocation IDs. Marked `integration`,
skipped when `shutil.which("claude")` is None. Added to `just check` behind
`INTEGRATION=1`.

**Live proof:** `INTEGRATION=1 uv run pytest tests/integration -q -rs` pasted, showing
1 passed (not skipped) on this machine.

### Phase 4 — Drop into a repo and it provisions itself, loudly (D4, D5, D6)

**Change:** new `provision.py` with one function `plan(project) -> list[Step]` and
`apply(steps, ask)`. Steps are typed: `SCAFFOLD_SMITH`, `CREATE_VENV`, `INIT_PROJECT`,
`INIT_TRACKER`, `SYNC_DEPS`. Each step has `needs_question: bool`.

Decision table — **every row prints a line, every question is one line:**

| State found | `start` (no flag) | `start --fix` |
| --- | --- | --- |
| no `.smith/` | `MISSING  .smith/ — run start --fix` | `CREATED  .smith/{run,memory}` |
| `pyproject.toml` present, no `.venv` | `MISSING  .venv — run start --fix` | `RUNNING  uv sync` → `CREATED  .venv` |
| no `pyproject.toml`, no `requirements*.txt` | `MISSING  project file` | `QUESTION  No pyproject.toml. Run 'uv init' here? [y/n]` → on y: `CREATED  pyproject.toml` |
| `requirements.txt` only | `FOUND  requirements.txt` | `QUESTION  Create .venv and pip install -r requirements.txt? [y/n]` |
| no `.seeds/` | `MISSING  tracker — run start --fix` | `QUESTION  Init a Seeds tracker here (sd init)? [y/n]` → on y: `CREATED  .seeds/` |
| `justfile`/`Makefile` found | `FOUND  justfile (test, lint)` | same, plus sets default verify |

`awino update` (standalone path) calls `provision.plan` after `restore()` and prints
the same lines. **Mission, `project.yaml`, `memory/`, `run/` are never touched by
provisioning** — test asserts byte-identity.

**Tests:** one per row; one asserting `start` without `--fix` writes nothing; one
asserting `update` keeps `project.yaml` byte-identical while creating `.venv`.

**Live proof (the real-world test you asked for):**
```powershell
$t="$env:TEMP\kilo\field-repo"; mkdir $t; git -C $t init -q
# 1. bare repo, no flag
cd $t; awino start                 # paste: MISSING lines, exit 0, nothing created
# 2. bare repo, --fix, answer questions
awino start --fix                  # paste: each CREATED / QUESTION line and your answers
Test-Path .smith, .venv, pyproject.toml, .seeds   # paste
# 3. simulate old install: delete .venv, keep .smith/project.yaml with a mission
Remove-Item -Recurse .venv; awino update          # paste: RESTORED, CREATED .venv, mission unchanged
Get-Content .smith\project.yaml                    # paste: mission still there
```

### Phase 5 — Stances: dynamic partner, not a flag you remember (D7, D8)

**Catalog (`stance.py`), fixed set, each ≤15 lines in persona:**

| Stance | Auto-trigger (your words / phase) | What changes in my replies |
| --- | --- | --- |
| `advisor` (default) | always on | lead with the uncomfortable truth; `[Certain]/[Likely]/[Guessing]` on claims; disagreement in the 3-line format; no validation phrases |
| `first-principles` | I am decomposing a problem; `plan`, `gate open`, "break this down" | facts vs assumptions table; rebuild from facts; name the assumption to challenge |
| `steel-man` | you state a belief or choice ("I think…", "we should…") | best case for the opposite, then which part to take seriously |
| `assumption-audit` | you state a conclusion ("so it's…", "that means…") | list every assumption, rate each, state what breaks if wrong |
| `teach-back` | "teach", "explain", "I don't understand", after any non-trivial fix | mental map + 3 examples + the 20% that matters; then you explain it back, I stop you at the first gap |
| `research-intake` | "research", "look into", "what do we know about" | 5 sub-questions, settled vs debated, ask which thread first — no information yet |
| `expert` / `friend` | "as a human", "honestly", "how would you" | first-person lived-experience answer, no textbook |

**Mechanics:**
- `stance.detect(user_text, phase) -> Stance | None` — deterministic keyword + phase
  rules, unit-tested per row.
- Switch is **never silent**: reply header shows `stance: <name>`, and the first switch
  in a session prints one line `STANCE  → steel-man (you stated a position)`.
- Override: `--stance X` on `start`/`dispatch`; `awino stance set X` persists in
  `.smith/project.yaml`; `awino stance` shows current + why.
- Claude hook injects `STANCE <name>` into context each prompt (D9); Kilo/Roo depend on
  the persona reading `project.yaml` — stated in docs, tested.
- D8: apology vocabulary removed from persona; `awino hook prompt` prints
  `ADVISOR_LINT  reply opened with validation phrase` when it detects one in the
  previous assistant turn (Claude only).

**Tests:** detection table row-by-row; `build_modes` output contains each stance block;
persona contains zero validation phrases.

**Live proof:** three real messages from you in one session — a decomposition, a stated
belief, a "teach me" — and my three replies pasted with the `stance:` header changing
without you naming any stance.

### Phase 6 — Hook does routing (D9)

`awino hook prompt` calls `dispatch.decide` and `stance.detect`, injects
`MATCHED <skill> conf=<x>` and `STANCE <name>`, spawns nothing. Test: hook output for a
high-confidence prompt; live proof: one Claude Code prompt with the injected block
visible.

### Phase 7 — `awino auto` (D10)

As previously specced, now with Phases 1–2 in place so each Seed's worker has its
skill and a real verify. `sd ready` → refuse planned-class without `[plan approved]`
→ `gate open` → `run_dispatch` → record contract gates → `work-close` → commit. Stops
whole loop on first non-`COMPLETE` with a `pending_decision`. No push.

**Live proof:** `awino auto --max-seeds 2 --confirm-budget` closes `c838` and `1704`;
paste `gate status` and `sd show` for both and `git log -2`.

---

## 3. Spawned reviewers (not me checking my own work)

| After | Reviewer | Instruction |
| --- | --- | --- |
| Phase 1 | read-only | confirm the prompt file contains SKILL.md; try to find a route where it does not |
| Phase 4 | adversarial | try to make `start --fix` write outside `.smith/`, `.venv/`, `.seeds/`, `pyproject.toml`; try to make it act without printing |
| Phase 5 | read-only | feed 20 sentences, report every wrong stance switch and every silent switch |
| Phase 7 | read-only | run `auto --dry-run` and confirm it refuses when any Phase-A Critical Seed is open |

Each reviewer's claims are re-run by me before I accept them.

---

## 4. Order

```
0 → 1 → 2 → 3 → 4 → 5 → 6 → 7
```
0 first because it is the only step that can prove the whole thing doesn't work. 4
before 5 because a partner that can't set up your repo is a talker. 5 before 7 because
`auto` running unattended in the wrong stance is worse than no `auto`.

## 5. Permanently human

Plan approval; `git push`; every `QUESTION` line; raising any ceiling; Cline/Codex
persona paths until proven.

## 6. Approval

- [ ] Approved.  Approver: ________  Date: ________
