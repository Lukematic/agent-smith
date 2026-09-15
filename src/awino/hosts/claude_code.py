"""Claude Code host adapter.

Integration seam (real, in this tree): the plugin's ``hooks/hooks.json``
fires ``awino hook session-start | prompt | pre-compact | pre-tool`` with
a JSON payload on stdin. The adapter's boundary methods are what that
host-side wiring calls to activate the shared plan controller; the
existing ``awino hook`` CLI keeps its behavior and gains the recursion
guard (see ``awino.hosts.recursion``).

Detection is honest about what it means: the ``claude`` CLI on PATH or
``CLAUDE_PLUGIN_ROOT`` in the environment says the host tooling is
present. It does NOT prove a live interactive journey — journey evidence
is recorded separately and labeled.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from awino.hosts.base import HostAdapter

#: The hook events this tree's Claude Code plugin registers, and the CLI
#: entry point each one fires. This is the documented host-side seam.
HOOK_EVENTS: dict[str, str] = {
    "SessionStart": "awino hook session-start",
    "UserPromptSubmit": "awino hook prompt",
    "PreCompact": "awino hook pre-compact",
    "PreToolUse": "awino hook pre-tool",
}


class ClaudeCodeAdapter(HostAdapter):
    HOST = "claude_code"

    def detect_live(self) -> bool:
        if shutil.which("claude"):
            return True
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
        return bool(plugin_root) and Path(plugin_root).is_dir()

    def missing_api_surface(self) -> list[str]:
        return [
            "No programmatic Claude Code session API exists in this tree: "
            "the only host-side triggers are the hooks/hooks.json events, "
            "each firing 'awino hook <event>' with a JSON payload on stdin.",
            "UserPromptSubmit sees only the human's typed text, never what "
            "the agent asked; the hook cannot observe agent speech.",
            "There is no host callback for the adapter to query live session "
            "state; the adapter's session_start/user_turn/tool_result "
            "methods ARE the seam a host integration must call.",
        ]

    def hook_command(self, event: str) -> str | None:
        """The CLI entry the plugin fires for a hook event, or None when
        the plugin does not register that event."""
        return HOOK_EVENTS.get(event)
