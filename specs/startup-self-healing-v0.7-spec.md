# v0.7 Startup Self-Healing

## Decision

A.W.I.N.O.'s source and dependency authority remains `.awino/pyproject.toml`.
The outer project does not receive a second Python project or virtual environment.

## User entry points

| Surface | Entry point | Automatic behavior |
| --- | --- | --- |
| PowerShell terminal | `awino start` | Installed wrapper locates the source clone, synchronizes only its `.venv`, then invokes `awino start`. |
| PowerShell terminal | `awino start --fix` | Also repairs A.W.I.N.O.-owned Kilo integration files. |
| PowerShell terminal | `awino best` | Uses the source clone environment, then resumes the persisted machine. |
| Kilo new chat | project default `awino` agent | The persona runs `awino start` then `awino best` before substantive work. |
| Claude Code new session | existing plugin SessionStart hook | Calls the A.W.I.N.O. hook; the installed persona also retains the startup contract. |
| Roo/Cline | installed instructions/persona | No unverified lifecycle hook is claimed; the selected A.W.I.N.O. controller performs startup. |

Opening a terminal does not run arbitrary scripts automatically. The explicit terminal command is `awino start`. A harness may only auto-start where it exposes a real session-start mechanism or loads the A.W.I.N.O. agent as the default controller.

## Ordered startup contract

1. Locate the nearest ancestor containing `.awino/pyproject.toml`.
2. Refuse with every searched directory if no installation is found.
3. Synchronize only `<installation>/.awino/.venv` from the lockfile with `uv sync --project`.
4. Run `awino doctor --fast`; a blocking failure prevents resume.
5. Inspect Kilo integration drift: `.kilo/kilo.json`, A.W.I.N.O. persona, and installer manifest.
6. With `--fix`, repair only installer-owned integration files and preserve unrelated Kilo configuration.
7. Report cached Git freshness only; `start` never fetches, pulls, or pushes.
8. Run `awino start`; `best` additionally resumes the persisted machine until its next real human boundary.

## Repair boundaries

`start --fix` may repair:

- a missing/stale `.awino/.venv`;
- a missing or stale A.W.I.N.O. Kilo persona;
- missing/default-agent drift in `.kilo/kilo.json`, preserving unrelated keys;
- stale A.W.I.N.O. installer-manifest entries.

It must not:

- switch an active Kilo/Cline/Roo/Claude session;
- override a human-selected active agent;
- create an outer-project `.venv`, `pyproject.toml`, or lockfile;
- create A.W.I.N.O. state where `.awino/pyproject.toml` is absent;
- fetch, pull, push, or otherwise change Git source during normal `start`.

## Freshness

`start` reports the installed version and local cached Git divergence when an
upstream is configured. `update` is the explicit operation allowed to fetch,
fast-forward, synchronize the environment, and refresh installer-owned harness
integration.

## Verification

1. Root and nested-directory invocations find one `.awino/pyproject.toml`.
2. An absent installation refuses without creating files.
3. The outer project gains no Python environment or packaging files.
4. Kilo repair preserves unrelated configuration and records exact managed hashes.
5. Normal `start` does not invoke Git network or mutation commands.
6. `update` refreshes an out-of-date installer-owned Kilo persona safely.
7. `uv run pytest` and `uv run ruff check src tests` pass from `.awino`.
