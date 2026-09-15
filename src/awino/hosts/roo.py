"""Roo Code host adapter.

Roo Code is a VS Code extension; no Roo host exists in this Linux sandbox
and no Roo extension API is documented in this tree. Like the Kilo
adapter, this adapter detects with a documented heuristic, defines the
integration seam (the three boundary methods on :class:`HostAdapter`),
and reports ``unverified`` with the missing surface spelled out.

Host-side wiring (a Roo extension contributing session/turn/tool
lifecycle hooks that call this adapter) does not exist yet and is
recorded as missing, not invented.
"""

from __future__ import annotations

from pathlib import Path

from awino.hosts.base import HostAdapter

#: Directory names that would indicate a Roo Code VS Code extension
#: install. Heuristic only; absence proves nothing beyond "not detected".
_ROO_MARKERS = ("roo", "roo-code", "roocode")


def _vscode_extension_dirs() -> list[Path]:
    home = Path("~").expanduser()
    return [
        home / ".vscode" / "extensions",
        home / ".vscode-insiders" / "extensions",
        home / ".vscode-server" / "extensions",
    ]


class RooAdapter(HostAdapter):
    HOST = "roo"

    def detect_live(self) -> bool:
        for ext_dir in _vscode_extension_dirs():
            if not ext_dir.is_dir():
                continue
            try:
                names = [p.name.lower() for p in ext_dir.iterdir()]
            except OSError:
                continue
            if any(marker in name for name in names for marker in _ROO_MARKERS):
                return True
        return False

    def missing_api_surface(self) -> list[str]:
        return [
            "No Roo Code extension or hook API is documented in this tree; "
            "the host-side mechanism that would call the adapter's boundary "
            "methods does not exist yet.",
            "The adapter defines the required seam — session_start, "
            "user_turn and tool_result on the shared plan controller — but "
            "Roo-side wiring (extension lifecycle hooks) is missing.",
            "No Roo host is present in this environment, so no live "
            "journey evidence can be produced here.",
        ]
