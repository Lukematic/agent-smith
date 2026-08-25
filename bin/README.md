# `bin`

## Contents

Cross-platform `awino` launchers for native plugin sessions.

## Usage

Claude Code adds this directory to the Bash tool `PATH` while the plugin is enabled.

## Format

POSIX shell and Windows command scripts.

## Stability

Launchers use `uv run --frozen --no-dev` to create or refresh the version-specific
locked `.venv` automatically. They report `DEGRADED` only when `uv` is unavailable
or locked environment preparation fails.
