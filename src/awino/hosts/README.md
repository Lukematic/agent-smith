# `src/awino/hosts`

One adapter per supported host (Claude Code, Kilo, Roo). Each adapter detects
its host, triggers the same plan controller at session start, user turn, and
tool-result boundaries, and records per-host integration evidence. A missing
host or missing host API is reported as `unverified` — never as a silent pass,
never as parity.

## Contents

| Module | Owns |
| --- | --- |
| `__init__.py` | Re-exports of the package's public surface. |
| `base.py` | The shared `HostAdapter` ABC, `HostStatus`/`Evidence` labels (`live`, `double_driven`, `unverified`, `unsupported`), `UnknownHost`, and `get_adapter()` host dispatch. |
| `claude_code.py` | Claude Code adapter: detection, hook seam, missing-CLI reporting. |
| `kilo.py` | Kilo adapter: detection and documented missing extension-API surface. |
| `roo.py` | Roo adapter: detection and documented missing extension-API surface. |
| `recursion.py` | Hook recursion guard: a hook firing its own trigger is refused. |

## Usage

```python
from awino.hosts import get_adapter

adapter = get_adapter("claude_code")  # or "kilo", "roo"
status = adapter.probe()              # HostStatus: evidence label + missing surface
adapter.session_started(controller, plan_id)
adapter.user_turn(controller, plan_id, text)
adapter.tool_result(controller, plan_id, result)
```

Unknown names raise `UnknownHost` naming the supported hosts. The recursion
guard wraps hook execution (`awino hook` runs inside it; recursive invocation
is refused with exit code 3).

## Format

Python 3.12, typed, ruff-formatted. Adapters never mutate plan substance —
they record `host_session_started` / `host_user_turn` / `host_tool_result`
controller events only. Journey evidence lands in the controller journal and in
per-host `evidence.jsonl` files, every record labeled by evidence class.

## Stability

Add a host by subclassing `HostAdapter` in a new module and registering it in
`get_adapter()`; do not invent host APIs — document the missing surface and
report `unverified`. Doubles used in tests must stay labeled `double_driven`:
a double-driven journey never upgrades evidence to `live`.

---

This file is hand-written and carries no generated marker, so `awino fix` will
not overwrite it.
