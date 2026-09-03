# Seed-Driven Autonomous Execution — Plan

Status: **awaiting approval**. Nothing implemented.

## 0. What "execute autonomously" can honestly mean here

`awino limits` and the constitution §15 agree: A.W.I.N.O. is **harness-level**. The two
things that promote it to loop-level are a **trigger** and **worktree isolation**. Neither
exists. There is no scheduler, and claiming one is `UNGROUNDED_CAPABILITY`.

So the autonomous unit this plan builds is not "runs by itself at 3 a.m." It is:

> **One human command starts a bounded loop that works through ready Seeds without
> further prompting, and stops on its own at a budget, a blocker, or a decision only a
> human can make.**

That is the elevator operator (`awino dispatch`, shipped) driven by the tracker
(`sd ready`) instead of by a human typing each request. Every piece already exists
after S1–S10: routing, preflight, spawn, independent verify, ledger, Seed closure.
What is missing is the driver that chains **Seeds**, the way dispatch chains **floors**.

## 1. Why the Critical Seeds come first

Autonomy multiplies whatever the system already does. Five open Critical Seeds are all
"A.W.I.N.O. can corrupt or lose its own state":

| Seed | Failure it prevents under autonomy |
| --- | --- |
| `d277` privacy denylist in session_log | an unattended loop logs a secret it read |
| `2750` version-checked writes in session_log | two loops write the same log, one wins silently |
| `95fd` pre-compaction durable dump | a long loop compacts and forgets what it was doing |
| `951d` clone-freshness pre-commit | a loop lands fixes in a stale duplicate clone |
| `42d4` ladder/loop declarations enforced | the loop's own `bounded` verdict is decorative |

`861e` (per-project isolation) is the worktree half of loop-level. Cross-project
contamination under an unattended loop is not recoverable by a human reading a transcript.

**Rule: no Seed in Phase C runs until every Seed in Phase A is closed.** The ledger
enforces this via `sd dep add`, not via prose.

## 2. Phases

### Phase A — Make autonomy safe (Critical Seeds, existing)

`d277`, `2750`, `95fd`, `951d`, `42d4`, `861e`. Each already has a title and a class.
Each is one `dispatch` trip today. The spec for each is its Seed body plus the
`bugfix`/`feature` contract; no new spec file needed.

Exit: all six closed with executed gates; `awino doctor --fast` reports `fail=0`.

### Phase B — Make the trigger real (closes review risk R4)

**B1.** Extend `awino hook prompt` (Claude `UserPromptSubmit`) to call `dispatch.decide`
and print the routing decision into the injected context. **Advisory only** — it does
not spawn. Reason: a hook that spawns subprocesses on every keystroke has no budget
confirmation, which S4 made mandatory. The hook tells the persona what dispatch *would*
do; the persona still calls `awino dispatch --confirm-budget`. Test: hook output
contains `MATCHED <skill>` for a high-confidence prompt and `QUESTION` otherwise; hook
never writes to the project.

**B2.** `docs/agent-guide.md`: replace "does not yet call dispatch" with the true
new statement. `test_dispatch_wiring.py` asserts the new text.

Exit: hook test passes; `agent-guide.md` and reality agree; full suite green.

### Phase C — The Seed driver: `awino auto`

```bash
awino auto --max-seeds N --confirm-budget [--dry-run] [--filter <sd filter>]
```

Loop, per Seed, reusing only shipped pieces:

1. `sd ready` → next unblocked Seed (respects `sd dep`).
2. Refuse Seeds whose class needs a plan (`code-change`, `refactor`, `authoring`) and
   have no `[plan approved]` flag — **stop and ask**, do not author a plan unattended.
3. `gate open <class> "<seed title>" --issue <id> --scope <from seed body>`.
4. `dispatch.run_dispatch(request=<seed title + body>)` — worker, independent verify,
   reroute, ≤3 floors.
5. On `COMPLETE`: record gates (`tested`, `linted`, … from the class contract), then
   `work-close` + `gate close`. Only the ledger says done.
6. On `BLOCKED` / `QUESTION` / `MAX_ITERATIONS`: checkpoint with `pending_decision`,
   **stop the whole loop**, print the decision. Do not skip to the next Seed — a human
   decides whether to continue past a stuck Seed.
7. Commit per Seed. Never push; pushing stays a human action.
8. Repeat until `--max-seeds`, no ready Seeds, or a stop condition.

Tests (real ledger, injected executor, plus one real subprocess trip):
- closes two ready Seeds in order, each with its own run and commit;
- stops at the first `BLOCKED` and leaves a `pending_decision` checkpoint;
- refuses a planned-class Seed without approval and spawns nothing;
- honors `--max-seeds 1` exactly;
- `--dry-run` prints the plan for N Seeds and writes nothing;
- never pushes (assert no `git push` in the recorded commands).

Exit: `awino auto --max-seeds 2 --confirm-budget` closes two real Low-priority Seeds
(`c838`, `1704`) end to end on this repo, with pasted `gate status` for both.

### Phase D — Worktree isolation (loop-level, second half)

Seed `7028` already names it: opt-in git worktree per delegated batch. `awino auto
--isolate` runs each Seed in `.smith/worktrees/<seed-id>`, merges on `COMPLETE`, leaves
the worktree on any stop. This is the last piece the constitution requires before
"loop-level" is an honest label.

Exit: two Seeds run in separate worktrees; main is untouched until merge; a `BLOCKED`
Seed's worktree survives for inspection.

## 3. What stays human, permanently

- Approving plans for planned task classes.
- Any `git push`.
- Every `pending_decision` the loop stops on.
- Raising `--max-seeds` or any attempt ceiling.
- Cline/Codex persona install until a real path is proven.

## 4. Order and dependencies

```
Phase A (6 Critical Seeds, any order, parallel-safe)  ->  Phase B  ->  Phase C  ->  Phase D
```

Wire with `sd dep add` so `awino auto` itself cannot be started early. The driver's
first test is that it refuses to run while any Phase A Seed is open.

## 5. Approval

- [ ] Human approved this plan.  Approver: __________  Date: ________
