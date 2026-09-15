"""Hook recursion prevention.

A host hook that triggers the very action the hook guards would recurse
until something breaks: Claude Code's ``PreToolUse`` hook firing because
the hook itself ran a tool, a ``SessionStart`` hook spawning a session
that fires ``SessionStart`` again. The guard is a depth token plus an
event chain, both carried in the environment so they survive into child
processes (``awino hook ...`` is exec'd by the host, not called
in-process).

Rules:

- depth 0, event not in chain -> run, at depth 1 with the event chained.
- the same event already in the chain -> refuse (a cycle, even across
  processes).
- depth already >= MAX_DEPTH -> refuse (nested hook execution).

Refusal raises :class:`HookRecursionRefused`; the CLI turns it into a
clear message and a distinct exit code. The previous environment is
always restored, so a refused or finished hook never wedges later hooks.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator

DEPTH_ENV = "AWINO_HOOK_DEPTH"
CHAIN_ENV = "AWINO_HOOK_CHAIN"
MAX_DEPTH = 1


class HookRecursionRefused(RuntimeError):
    """A hook invocation that would recurse. Carries the evidence."""

    def __init__(self, event: str, depth: int, chain: list[str], reason: str) -> None:
        super().__init__(
            f"refusing recursive hook {event!r}: {reason} (depth={depth}, chain={chain})"
        )
        self.event = event
        self.depth = depth
        self.chain = list(chain)
        self.reason = reason


def describe_refusal(exc: HookRecursionRefused) -> str:
    chain = " -> ".join([*exc.chain, exc.event]) if exc.chain else exc.event
    return (
        f"event={exc.event} reason={exc.reason} "
        f"depth={exc.depth} chain={chain}. "
        "A hook must never trigger its own event; the invocation was "
        "stopped before it could recurse."
    )


def _read_depth() -> int:
    try:
        return int(os.environ.get(DEPTH_ENV, "0"))
    except ValueError:
        return 0


def _read_chain() -> list[str]:
    raw = os.environ.get(CHAIN_ENV, "")
    return [part for part in raw.split(",") if part]


@contextlib.contextmanager
def guarded(event: str) -> Iterator[None]:
    """Run a hook body exactly once per trigger.

    Raises :class:`HookRecursionRefused` when the event is already in the
    chain (a cycle) or the depth token shows a hook is already running
    (nesting). Restores the environment on the way out.
    """
    depth = _read_depth()
    chain = _read_chain()
    if event in chain:
        raise HookRecursionRefused(
            event, depth, chain, reason=f"{event!r} is already in the hook chain"
        )
    if depth >= MAX_DEPTH:
        raise HookRecursionRefused(
            event,
            depth,
            chain,
            reason=f"a hook is already running at depth {depth} (max {MAX_DEPTH})",
        )
    old_depth = os.environ.get(DEPTH_ENV)
    old_chain = os.environ.get(CHAIN_ENV)
    os.environ[DEPTH_ENV] = str(depth + 1)
    os.environ[CHAIN_ENV] = ",".join([*chain, event])
    try:
        yield
    finally:
        if old_depth is None:
            os.environ.pop(DEPTH_ENV, None)
        else:
            os.environ[DEPTH_ENV] = old_depth
        if old_chain is None:
            os.environ.pop(CHAIN_ENV, None)
        else:
            os.environ[CHAIN_ENV] = old_chain


def current_depth() -> int:
    """Visible for tests and diagnostics: the hook depth right now."""
    return _read_depth()


def current_chain() -> list[str]:
    """Visible for tests and diagnostics: the hook chain right now."""
    return _read_chain()
