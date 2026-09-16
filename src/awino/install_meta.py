"""Installation-owned metadata: markers the host writes, not the user.

The GUI/host installer records that an installation is live by writing a
``.in_use`` marker at the installation root. That marker is host-owned: it is
not clutter, it is not documentation debt, and it must never be flagged,
archived, or deleted to make a health gate green.

The classification is deliberately narrow. Only explicitly known host-owned
names are installation metadata; every other hidden file or directory is
still inspected normally, so a real mess cannot hide behind a leading dot.

Shared by ``awino.tidy`` (never flag/archive/clean these), ``awino.health``
(never fail a gate on these), and ``awino.fair`` (never demand docs for
these). One module owns the list so the three cannot drift apart.
"""

from __future__ import annotations

from enum import StrEnum

#: Names owned by the host/installer. Preserved unconditionally: never flagged
#: as stray, never archived, never deleted.
HOST_OWNED_NAMES = frozenset(
    {
        # Host installation lock, written by the GUI/host installer to mark the
        # installation as live. Deleting it to satisfy a gate would desync the
        # host; the gate must pass with it present instead.
        ".in_use",
    }
)


class RootEntryKind(StrEnum):
    INSTALLATION_METADATA = "installation-metadata"
    UNKNOWN = "unknown"


def classify_root_entry(name: str) -> RootEntryKind:
    """Classify a root-level entry by name.

    Returns ``INSTALLATION_METADATA`` for host-owned markers, ``UNKNOWN`` for
    everything else. Callers combine this with their own allow-lists: unknown
    does not mean stray, it means "not installation metadata, decide yourself".
    """
    if name in HOST_OWNED_NAMES:
        return RootEntryKind.INSTALLATION_METADATA
    return RootEntryKind.UNKNOWN


def is_installation_metadata(name: str) -> bool:
    """Whether this root entry is host-owned and must be preserved as-is."""
    return classify_root_entry(name) is RootEntryKind.INSTALLATION_METADATA


def describe(name: str) -> str:
    """Human-readable reason a marker is preserved, for gate output."""
    if is_installation_metadata(name):
        return "host-owned installation marker; preserved, never flagged or removed"
    return "not installation metadata"
