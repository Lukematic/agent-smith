---
name: awino-reproducibility

description: Reproducibility discipline for scientific research pipelines. Requires traceable run artifacts with inputs configuration model identity event history and rerun instructions.
---

# Smith Reproducibility

Use before building or modifying a scientific, data, RAG, evaluation, or agent
pipeline whose results must be explainable later.

The invariant is not “the output text is byte-identical”—stochastic models and
external APIs can make that impossible. The invariant is:

> Given the captured inputs, configuration, software revision, model identity,
> source snapshots, and random parameters, the run is explainable and can be
> rerun within declared tolerance.

## Minimum run artifact

Each run gets a unique ID and one directory:

```text
runs/<run_id>/
  manifest.json          input hashes, code revision, dependency lock hash
  input_snapshot/        immutable inputs or source pointers plus hashes
  config.json            parameters, model/provider, temperature, seed
  prompts.json           prompt names, versions, and hashes
  sources.jsonl          source IDs, queries, URLs, retrieval timestamps
  events.jsonl           append-only state and tool-call trail
  outputs/               result artifacts
  errors.json            complete failure summary, even when empty
```

## Rules

1. Create `run_id` before processing.
2. Snapshot inputs or record immutable identifiers and hashes before mutation.
3. Capture configuration, prompt hashes, model/provider identity, and software
   revision at start.
4. Keep events append-only during the run.
5. Redact secrets before writing any artifact.
6. Record retries, fallback providers, and partial failures.
7. Preserve failed runs until their failure has been analyzed.
8. Define reproducibility tolerance for stochastic outputs; do not claim exact
   determinism without evidence.

## Scientific/research additions

- distinguish retrieval time from publication time;
- retain search queries and filters;
- preserve excluded-source rationale;
- map every synthesis claim to source IDs;
- record whether evidence came from metadata, abstract, or full text;
- snapshot calibration and preprocessing configuration;
- record human approvals and interpretation boundaries.

## Verification

Before completion, check:

- run directory exists;
- input count/hashes match the starting state;
- config and prompt/model versions are captured;
- `run_started` and terminal event both exist;
- output counts reconcile with success/failure counts;
- secrets are absent;
- a documented rerun command exists;
- rerun behavior meets the stated tolerance.

## Failure Modes

| Mode | Definition |
| --- | --- |
| `RUN_WITHOUT_ID` | processing starts before a run artifact exists |
| `MOVING_INPUT` | source data changes without a snapshot or hash |
| `CONFIG_AMNESIA` | parameters or prompt/model versions are missing |
| `SUCCESS_ONLY_AUDIT` | failures and retries are omitted from the trail |
| `SECRET_CAPTURE` | credentials enter configs, logs, or snapshots |
| `FALSE_DETERMINISM` | stochastic output is promised to be byte-identical |
| `SOURCE_TIME_CONFUSION` | retrieval date is treated as publication/evidence date |
| `UNREPLAYABLE_RESULT` | no command can reconstruct the run environment |

## Completion

Done when a second operator can identify what happened, which evidence and code
produced it, which failures occurred, and how to rerun it without private context
from the original session.

Grounding: chapters/8-practices/6-knowledge-evolution.md,
chapters/9-mental-models/4-context-as-code.md,
chapters/12-long-horizon-agent-state/1-five-layer-model.md
