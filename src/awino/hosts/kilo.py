"""Kilo host adapter.

Kilo is a VS Code extension; no Kilo host exists in this Linux sandbox and
no Kilo extension API is documented in this tree. This adapter therefore
does three honest things:

1. Detects a plausible Kilo installation with a documented heuristic
   (VS Code extension directories) — a heuristic, not proof.
2. Defines the integration seam: the three boundary methods on
   :class:`HostAdapter` (session_start / user_turn / tool_result) are
   what Kilo-side code must call to activate the shared controller.
3. Reports ``unverified`` with the missing API surface spelled out,
   never parity.

Host-side wiring (a Kilo extension contributing session/turn/tool
lifecycle hooks that call this adapter) does not exist yet and is
recorded as missing, not invented.
"""

from __future__ import annotations

from pathlib import Path

from awino.hosts.base import HostAdapter

#: Directory names that would indicate a Kilo VS Code extension install.
#: Heuristic only; absence proves nothing beyond "not detected here".
_KILO_MARKERS = ("kilo", "kilocode", "kilo-code")


def _vscode_extension_dirs() -> list[Path]:
    home = Path("~").expanduser()
    return [
        home / ".vscode" / "extensions",
        home / ".vscode-insiders" / "extensions",
        home / ".vscode-server" / "extensions",
    ]


class KiloAdapter(HostAdapter):
    HOST = "kilo"

    def detect_live(self) -> bool:
        for ext_dir in _vscode_extension_dirs():
            if not ext_dir.is_dir():
                continue
            try:
                names = [p.name.lower() for p in ext_dir.iterdir()]
            except OSError:
                continue
            if any(marker in name for name in names for marker in _KILO_MARKERS):
                return True
        return False

    def missing_api_surface(self) -> list[str]:
        return [
            "No Kilo extension or hook API is documented in this tree; the "
            "host-side mechanism that would call the adapter's boundary "
            "methods does not exist yet.",
            "The adapter defines the required seam — session_start, "
            "user_turn and tool_result on the shared plan controller — but "
            "Kilo-side wiring (extension lifecycle hooks) is missing.",
            "No Kilo host is present in this environment, so no live "
            "journey evidence can be produced here.",
        ]
