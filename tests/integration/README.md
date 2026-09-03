# tests/integration

## Contents

Full-boundary tests that run the real `smith.cli` in a subprocess against a
throwaway project: `gate open` → `floor open` → work → `floor close`.

## Usage

```bash
uv run pytest -m integration
```

Slower than unit tests but dependency-free: the "worker" is plain python,
precisely because dispatch floors are harness-agnostic - no external agent
CLI or login required.

## Format

One pytest module per full-boundary contract, marked `integration` (registered
in `pyproject.toml`). Each test builds its own temp project with a `.git`
directory and a checker script, then drives the CLI exactly as a human or
agent would.

## Stability

Add one test per contract. Keep workers as plain subprocesses; an external
agent CLI dependency here would reintroduce the login problem floors exist to
remove.
