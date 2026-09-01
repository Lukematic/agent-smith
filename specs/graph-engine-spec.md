# Independent Worker-Reviewer Graph

## Objective

Implement a cross-platform, bounded graph in which worker and reviewer phases run as distinct subprocess identities and route through `SHIP`, `REVISE`, or `BLOCKED` outcomes. Persist every round through the existing `Ledger`, `RunArtifact`, `Checkpoint`, `Evidence`, and `ProvenanceRecord` mechanisms.

## Scope

- `src/smith/graph.py`
- `src/smith/spawn.py`
- `src/smith/cli.py`
- `tests/test_graph.py`

The unrelated `src/smith/config_review.py` change is explicitly out of scope.

## Required behavior

1. Model named worker and reviewer nodes and route outcomes mechanically.
2. Execute worker and reviewer in fresh subprocess contexts with distinct invocation identities.
3. Keep the reviewer read-only; obtain a structured verdict from bounded subprocess output rather than a reviewer-written project file.
4. Feed `REVISE` feedback into the next worker iteration.
5. Persist iteration number, node identity, subprocess result, feedback, selected edge, budget, and terminal outcome in the existing run ledger.
6. Record an accepted independent review with `Ledger.record_provenance()` and a `verified_by` identity distinct from the run opener.
7. Preserve `adjudicate()` and `Verdict.can_close` as completion authority; graph acceptance alone must not bypass gate closure.
8. Require explicit cost/budget confirmation at the CLI boundary.
9. Work on Windows without POSIX-only `test`, `head`, or `grep` commands.
10. Stop after at most `MAX_ATTEMPTS` rounds.

## Terminal outcomes

- `SHIP`: reviewer accepted and provenance was persisted.
- `REVISE`: reviewer rejected with actionable feedback and budget remains.
- `BLOCKED`: worker or reviewer explicitly blocked, a phase failed, no structured verdict was produced, no runner exists, or execution is nested.
- `MAX_ITERATIONS`: review did not accept before the bounded budget expired.

## Acceptance checks

- First reviewer rejects, its exact feedback reaches the second worker, and a fresh second reviewer accepts.
- Persisted artifacts prove two distinct reviewer invocation identities.
- Worker failure and reviewer failure stop as blocked.
- Missing or malformed review verdict stops as blocked.
- Explicit blocked verdict stops as blocked.
- Repeated revise verdicts stop at maximum iterations.
- CLI refuses execution without explicit budget confirmation.
- Focused tests: `uv run pytest tests/test_graph.py -q`.
- Regression tests: `uv run pytest tests/test_graph.py tests/test_loop.py tests/test_capability.py tests/test_completion_review.py tests/test_gate_review_workflow.py -q`.
- Lint: `uv run ruff check src tests && uv run ruff format --check src tests`.

## Grounding

- `chapters/9-mental-models/8-loop-engineering.md`
- Goose `ralph-loop.sh`: bounded worker/reviewer rounds, cost confirmation, feedback carry-forward, explicit missing-result and phase-failure handling.
- Goose `code-review-mentor.yaml`: systematic independent review and actionable feedback.
- Goose `code-documentation-generator.yaml`: phased execution and explicit quality validation; no documentation-generation behavior is copied into the graph.

## Approval provenance

The user explicitly directed implementation on 2026-08-31 after restating the unfinished requirements and supplying the three Goose references.
