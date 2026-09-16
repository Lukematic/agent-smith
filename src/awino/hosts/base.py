"""Per-host adapters for real per-turn host activation.

Each supported host (Claude Code, Kilo, Roo) gets its own adapter class
that triggers the SAME Phase 2 plan controller at three boundaries:

- session start  -> ``host_session_started``
- user turn      -> ``host_user_turn``
- tool result    -> ``host_tool_result``

The adapters are the integration seam: host-side code (a plugin hook, an
extension, a CLI wrapper) calls the boundary methods; the controller
records normalized, idempotent events. Adapters never invent host APIs:
where the live host or its API is absent, the adapter reports an honest
``unverified`` status instead of claiming parity. A deliberately
unsupported host name raises :class:`UnknownHost`.

Evidence classes (labels, never inflated):

- ``live``          — a real interactive journey ran through this adapter
                      on a real host.
- ``double_driven`` — driven by a faithful test double. Never parity.
- ``unverified``    — host or API absent here; integration unproven.
- ``unsupported``   — not a known host.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from awino.controller import PlanAdapter


class Evidence:
    """Evidence labels for per-host integration claims."""

    LIVE = "live"
    DOUBLE_DRIVEN = "double_driven"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"

    ALL = (LIVE, DOUBLE_DRIVEN, UNVERIFIED, UNSUPPORTED)


class UnknownHost(RuntimeError):
    """A host name with no adapter. First-class, never a silent pass."""

    def __init__(self, host: str, supported: list[str]) -> None:
        super().__init__(
            f"unknown host {host!r}; supported hosts: {', '.join(supported)}. "
            "An unsupported host yields this report, never a silent pass."
        )
        self.host = host
        self.supported = list(supported)


@dataclass(frozen=True)
class HostStatus:
    """The honest integration report for one host."""

    host: str
    detected_live: bool
    evidence: str  # one of Evidence.ALL
    detail: str
    missing_apis: tuple[str, ...] = ()
    evidence_path: str = ""
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "detected_live": self.detected_live,
            "evidence": self.evidence,
            "detail": self.detail,
            "missing_apis": list(self.missing_apis),
            "evidence_path": self.evidence_path,
            "checked_at": self.checked_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class HostAdapter(ABC):
    """One host's view of the shared plan controller.

    Subclasses implement exactly two host-specific pieces: how to detect
    the live host, and which host API surface is missing. Everything else
    — the three boundary methods, evidence recording, status reporting —
    is shared so no host can quietly diverge.
    """

    HOST: str = "unknown"

    # ── host-specific ─────────────────────────────────────────────

    @abstractmethod
    def detect_live(self) -> bool:
        """True when the live host looks present in this environment."""

    @abstractmethod
    def missing_api_surface(self) -> list[str]:
        """Host API surface the integration would need but this tree does
        not have. Documents the gap; never invents the API."""

    # ── shared ────────────────────────────────────────────────────

    def status(self) -> HostStatus:
        """The integration report. Never raises, never claims parity."""
        try:
            live = bool(self.detect_live())
        except Exception:
            live = False
        missing = self.missing_api_surface()
        if live and not missing:
            evidence = Evidence.LIVE
            detail = f"{self.HOST} detected and its API surface is present"
        elif live:
            evidence = Evidence.UNVERIFIED
            detail = (
                f"{self.HOST} looks present but required API surface is "
                "missing or unproven here; live parity NOT claimed"
            )
        else:
            evidence = Evidence.UNVERIFIED
            detail = (
                f"{self.HOST} not detected in this environment; "
                "integration is unproven, not assumed"
            )
        return HostStatus(
            host=self.HOST,
            detected_live=live,
            evidence=evidence,
            detail=detail,
            missing_apis=tuple(missing),
            checked_at=_now(),
        )

    # ── the three boundaries: same controller, every host ─────────

    def _fire(
        self,
        adapter: PlanAdapter,
        *,
        event_id: str,
        kind: str,
        session_id: str,
        evidence: str,
        detail: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if evidence not in Evidence.ALL:
            raise ValueError(f"evidence must be one of {Evidence.ALL}, got {evidence!r}")
        payload: dict[str, Any] = {
            "host": self.HOST,
            "session_id": session_id,
            "evidence": evidence,
            "detail": detail,
        }
        payload.update(extra or {})
        return adapter.controller.submit_event(event_id=event_id, kind=kind, payload=payload)

    def session_start(
        self,
        adapter: PlanAdapter,
        session_id: str,
        *,
        evidence: str,
        detail: str = "",
    ) -> dict[str, Any]:
        """A host session began. Returns the controller's pending-work
        status lines so the host can show them: starting a session shows
        pending work and asks for consequential decisions; nothing runs
        unattended."""
        outcome = self._fire(
            adapter,
            event_id=f"host-{self.HOST}-session-{session_id}",
            kind="host_session_started",
            session_id=session_id,
            evidence=evidence,
            detail=detail,
        )
        outcome["status_lines"] = adapter.status_lines()
        return outcome

    def user_turn(
        self,
        adapter: PlanAdapter,
        session_id: str,
        prompt: str,
        *,
        delivery_id: str,
        evidence: str,
        detail: str = "",
    ) -> dict[str, Any]:
        """The human said something. ``delivery_id`` makes redelivery
        idempotent: the same turn submitted twice applies once."""
        return self._fire(
            adapter,
            event_id=f"host-{self.HOST}-turn-{delivery_id}",
            kind="host_user_turn",
            session_id=session_id,
            evidence=evidence,
            detail=detail,
            extra={"prompt_chars": len(prompt)},
        )

    def tool_result(
        self,
        adapter: PlanAdapter,
        session_id: str,
        tool_name: str,
        result_summary: str,
        *,
        delivery_id: str,
        evidence: str,
        detail: str = "",
    ) -> dict[str, Any]:
        """A tool call completed. Same idempotency contract as user_turn."""
        return self._fire(
            adapter,
            event_id=f"host-{self.HOST}-tool-{delivery_id}",
            kind="host_tool_result",
            session_id=session_id,
            evidence=evidence,
            detail=detail,
            extra={"tool_name": tool_name, "result_chars": len(result_summary)},
        )

    # ── per-host integration evidence ─────────────────────────────

    def evidence_file(self, state_root: Path) -> Path:
        return Path(state_root) / "hosts" / self.HOST / "evidence.jsonl"

    def record_evidence(
        self,
        state_root: Path,
        *,
        session_id: str,
        evidence: str,
        boundaries: list[str],
        detail: str = "",
    ) -> Path:
        """Append one per-host evidence record. This is the host's own
        integration evidence: what ran, under which evidence label."""
        if evidence not in Evidence.ALL:
            raise ValueError(f"evidence must be one of {Evidence.ALL}, got {evidence!r}")
        path = self.evidence_file(state_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": _now(),
            "host": self.HOST,
            "session_id": session_id,
            "evidence": evidence,
            "boundaries": list(boundaries),
            "detail": detail,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return path

    def read_evidence(self, state_root: Path) -> list[dict[str, Any]]:
        """The host's own evidence records. Empty when none were recorded."""
        path = self.evidence_file(state_root)
        if not path.is_file():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records


def _adapter_classes() -> list[type[HostAdapter]]:
    # Imported here so importing awino.hosts.base never pulls host code
    # with heavier dependencies.
    from awino.hosts.claude_code import ClaudeCodeAdapter
    from awino.hosts.kilo import KiloAdapter
    from awino.hosts.roo import RooAdapter

    return [ClaudeCodeAdapter, KiloAdapter, RooAdapter]


def supported_hosts() -> list[str]:
    return [cls.HOST for cls in _adapter_classes()]


def get_adapter(host: str) -> HostAdapter:
    """Return the adapter for a known host. Unknown names raise
    :class:`UnknownHost` — the spec's "never a silent pass" rule as code."""
    for cls in _adapter_classes():
        if host == cls.HOST:
            return cls()
    raise UnknownHost(host, supported_hosts())
