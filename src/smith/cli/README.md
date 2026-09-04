# `src/smith/cli`

Every `awino` command, grouped by what it serves. This package is the only part
of `src/smith` that prints; library modules return data and this layer renders it.

## Contents

| Module | Owns |
| --- | --- |
| `__init__.py` | The Typer apps (`app`, `gate_app`, `floor_app`, `debug_app`), the `--version` callback, the `smith` compatibility entry, and the helpers every command shares: `_workspace`, `_paths`, `_ledger`, `_echo`, `_resolve_run`, `_skill_catalog`, `_toolchain`, `_require_valid_plan`, `_ledger_error`, `_debug_session`, `_version`. Command modules are imported at the bottom so importing `smith.cli` registers everything. |
| `__main__.py` | `python -m` entry for the `smith.cli` package. |
| `gate.py` | The run ledger: `gate *`, `gate plan *`, and `debug *`. |
| `dispatch.py` | Bounded delegation: `dispatch`, `floor open/close`, `auto`, `gate graph`, `gate loop`. |
| `install.py` | Getting A.W.I.N.O. into harnesses and modes: `install*`, `link`, `mode-status`, `pointer`, `skills`, `skills-status`, `scaffold`. |
| `project.py` | The project under work: `start`, `best`, `mission`, `onboard`, `context`, `stance`, `project-*`, `work*`, `resume`, `note`, `ask`, `session-log`, `remember`, `workflow`, `env`, `setup`, `limits`, `ladder`, `plan`. |
| `maintain.py` | A.W.I.N.O.'s own installation: `doctor`, `update*`, `rollback`, `knowledge-update`, `tidy`, `clean`, `fix`, `validate`, `hook`, `heal`, `watch*`, `config-review`, `review-doc`, `delegate`, `registry-json`, `status`, `pit`. |
| `knowledge.py` | The knowledge harness: `route`, `fetch`, `drift`. |

Each command module's docstring begins `owns: <command names>`; `tests/test_cli_layout.py`
checks that line against the commands actually registered.

## Usage

Installed as the `awino` console script (`smith.cli:app`) and reachable with
`python -m` on the `smith.cli` package. `from smith import cli; cli.app` yields the fully
registered Typer app.

## Format

Python 3.12, typed, ruff-formatted. Command modules import the shared helpers
from `smith.cli` and register against the apps defined there; the import at the
bottom of `__init__.py` is load-bearing and must stay last.

## Stability

Add a command to the module whose `owns:` line it belongs under, and add its
name to that line. No module outside this package may import `smith.cli`
(enforced by `tests/test_cli_layout.py`). The registered command set is pinned
by the same test: removing or renaming a command is a deliberate, tested change.

---

This file is hand-written and carries no generated marker, so `awino fix` will
not overwrite it.
