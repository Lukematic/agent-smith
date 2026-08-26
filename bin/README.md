# `bin`

## Contents

Cross-platform `awino` launchers for native plugin sessions.

## Usage

Claude Code adds this directory to the Bash tool `PATH` while the plugin is enabled.

## Format

POSIX shell and Windows command scripts.

## Stability

Launchers capture the caller directory as `AWINO_PROJECT`, then unset `VIRTUAL_ENV`
and `CONDA_PREFIX` only in the launcher child process before using
`uv run --frozen --no-dev`. This keeps the backend in its version-specific locked
environment without deactivating the user's shell or confusing the target project
with a plugin cache directory. They report `DEGRADED` only when `uv` is unavailable
or locked environment preparation fails.
