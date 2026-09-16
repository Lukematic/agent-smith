# Task contract reference examples

## Contents

This directory contains production-ready task contract references and decision
fixtures for planning, agent authoring, and delegation.

- `task-contract-examples.md`: The seven canonical task families with their
  required policy blocks, decision rules, constraints, and negative fixtures.

## Usage

Consulted on demand by `awino-rpi`, `awino-author-agent`, and `awino-delegate`.
Do not load into global context for unrelated small tasks.

## Format

Markdown specification following the 8-block contract format (Goal, Context,
Instruction priority, Autonomy, Tools/Delegation, Output, Verification, Stop condition).

## Stability

Maintained alongside `templates/task-contract.yaml` and `src/awino/task_contract.py`.
