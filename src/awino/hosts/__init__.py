"""Per-host adapters for real per-turn host activation.

Three separate adapters — Claude Code, Kilo, Roo — each triggering the
same Phase 2 plan controller at session start, user turn, and tool-result
boundaries. See :mod:`awino.hosts.base` for the evidence rules: missing
live-host evidence means assisted/unverified status, never parity.
"""

from awino.hosts.base import (
    Evidence,
    HostAdapter,
    HostStatus,
    UnknownHost,
    get_adapter,
    supported_hosts,
)
from awino.hosts.claude_code import ClaudeCodeAdapter
from awino.hosts.kilo import KiloAdapter
from awino.hosts.recursion import (
    CHAIN_ENV,
    DEPTH_ENV,
    MAX_DEPTH,
    HookRecursionRefused,
    current_chain,
    current_depth,
    describe_refusal,
    guarded,
)
from awino.hosts.roo import RooAdapter

__all__ = [
    "CHAIN_ENV",
    "DEPTH_ENV",
    "MAX_DEPTH",
    "ClaudeCodeAdapter",
    "Evidence",
    "HookRecursionRefused",
    "HostAdapter",
    "HostStatus",
    "KiloAdapter",
    "RooAdapter",
    "UnknownHost",
    "current_chain",
    "current_depth",
    "describe_refusal",
    "get_adapter",
    "guarded",
    "supported_hosts",
]
