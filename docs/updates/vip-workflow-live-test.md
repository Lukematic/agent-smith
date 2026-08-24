# VIP Workflow Live Test

`tests/test_vip_workflow_live.py` is the seed 17fa black-box acceptance test for
the gated VIP workflow. It starts a new CLI subprocess for every operation and
uses a disposable project under pytest's temporary directory rather than
Smith's own repository.

## Command Matrix

The test executed this matrix successfully on Windows on 2026-08-24. These are
the command forms exercised by the test, not pasted or fabricated transcripts.

| Phase | Command form | Acceptance signal |
| --- | --- | --- |
| Health | `python -m smith.cli doctor --fast` | `HEALTH` reports `fail=0` |
| Context | `python -m smith.cli context` | disposable project is the resolved project |
| Route | `python -m smith.cli skills --route "seed 17fa VIP workflow" --json` | local `vip-workflow` skill is selected |
| Plan advice | `python -m smith.cli plan "normalize the VIP greeting"` | request is analyzed before opening work |
| Attempt ceiling | `gate record researched --cmd <failing-script>` three times, then a fourth command | fourth command is refused with `THREE_STRIKES`; marker bytes do not change |
| Tracker | `sd init --json`; `sd create ... --json` | isolated real Seeds issue exists |
| Open | `gate open code-change ... --plan <plan> --issue <id>` | plan, scope, and issue are linked |
| Skill audit | `gate skill vip-workflow --state used ...` | usage is persisted in the run |
| Checkpoint | `gate checkpoint ... --pending ... --option approve --option hold` | pending approval is durable |
| New process | `resume` | pending decision and linked issue are reported |
| Decisions | `gate decide approve`; `gate plan approve` | checkpoint resolves and exact plan SHA-256 is printed |
| Plan mutation | modify approved plan; `gate record tested ...` | execution is refused because the approved hash differs |
| Reapproval | restore plan; `gate plan approve` | restored exact bytes receive a fresh approval |
| Implementation | edit `src/vip.py` in the disposable repository | real Python behavior normalizes whitespace |
| Test evidence | `gate record tested --cmd "python -m pytest -q"` | project test executes and passes |
| Lint evidence | `gate record linted --cmd "python -m ruff check src tests"` | project lint executes and passes |
| Git checks | `gate check --diff-base HEAD` | tests are not weakened and changes remain in declared scope |
| Run closure | `gate close` | all five code-change gates are satisfied |
| Issue closure | `work-close --run <id>` | linked Seeds issue is closed with evidence in the reason |
| Stale resume | `resume` | closed current run reports `status=stale` |

The run also found and fixed a real Seeds 0.5.15 compatibility issue: `sd show
--json` wraps the returned row under `issue`, while the integration previously
recognized only `issues`, `results`, and bare rows.

## Running

```powershell
uv run pytest tests/test_vip_workflow_live.py -q
```

The repository verification commands are:

```powershell
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run smith doctor
```

## Limitations

- The test requires `git` and the optional `sd` CLI. It skips only when `sd` is
  unavailable; CI environments intended to cover this acceptance path must
  install Seeds.
- The project is real and disposable, but it uses the current test interpreter
  and its installed pytest and Ruff rather than creating another virtualenv.
- Seeds generates the issue identifier, so the test labels the scenario seed
  17fa but does not assume a fixed tracker ID.
- Each Smith operation is a fresh subprocess. The implementation edit and Git
  bootstrap are performed by the test harness because they are the external
  actor actions the CLI is expected to govern.
- No command transcript is committed. Assertions inspect real exit codes,
  durable files, and selected output tokens to avoid coupling the test to timing
  values or cosmetic output.
