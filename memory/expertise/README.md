# memory/expertise

## Contents

One JSONL file per domain. Each line is one durable record:
`{"type","domain","description","classification","source","ts"}`.

## Usage

Appended by `awino-consult` and `awino-memory` when an answer produced a rule that
applies to this project. Read by `recall` alongside `lessons.md`.

## Format

`type` is `convention | pattern | failure | decision`. `classification` is
`foundational` (confirmed across sessions) | `tactical` (default) | `observational`.
One observation is never `foundational`.

## Stability

Append-only. Revise by adding a superseding record, not by editing.
