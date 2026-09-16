"""The release gate: verify the artifact, then refuse to release without
explicit, separate authorization.

Publishing, pushing, tagging, and releasing stay behind an explicit
authorization flag. The gate's job is to say exactly what is broken and
refuse — or, on a good artifact, list the verified capabilities. It never
performs a network publish itself; actual publishing is a separately
authorized human step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from awino import manifest as _manifest

#: Actions the gate guards. Each needs its own explicit authorization.
GUARDED_ACTIONS = ("publish", "push", "tag", "release")


class ReleaseRefused(Exception):
    """The gate refused. ``reasons`` names exactly what is wrong."""

    def __init__(self, action: str, reasons: list[str]) -> None:
        self.action = action
        self.reasons = list(reasons)
        super().__init__(f"RELEASE REFUSED ({action}): " + "; ".join(self.reasons))


class AuthorizationRequired(ReleaseRefused):
    """Verification passed, but no explicit authorization was given."""


@dataclass(frozen=True)
class ArtifactReport:
    ok: bool
    problems: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    version: str = ""


@dataclass(frozen=True)
class ReleaseReport:
    action: str
    authorized: bool
    performed: bool
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""


def verify_artifact(root: Path, commands: Sequence[str]) -> ArtifactReport:
    """Verify a source tree artifact: accurate manifest, complete bundle.

    ``commands`` is the registered command surface, supplied by the caller
    (the CLI layer introspects it; library code must not import awino.cli).
    Returns a report; never raises for a broken artifact — the brokenness is
    the report, with specific reasons.
    """
    problems = _manifest.verify_manifest(root, commands)
    recorded = _manifest.load_manifest(root)
    capabilities = recorded.provides if recorded is not None else ()
    version = recorded.version if recorded is not None else ""
    return ArtifactReport(
        ok=not problems,
        problems=tuple(problems),
        capabilities=capabilities,
        version=version,
    )


def release_gate(
    root: Path, *, action: str, authorize: bool = False, commands: Sequence[str]
) -> ReleaseReport:
    """Run the release gate for ``action``.

    - Broken artifact -> ReleaseRefused with the specific reasons.
    - Good artifact, no authorization -> AuthorizationRequired.
    - Good artifact with authorization -> ReleaseReport listing the verified
      capabilities. The gate verifies; it does not publish.
    """
    if action not in GUARDED_ACTIONS:
        raise ReleaseRefused(action, [f"unknown guarded action {action!r}"])
    report = verify_artifact(root, commands)
    if not report.ok:
        raise ReleaseRefused(action, list(report.problems))
    if not authorize:
        raise AuthorizationRequired(
            action,
            [
                "explicit authorization not given "
                "(pass authorize=True / --authorize as a separate, deliberate step)"
            ],
        )
    return ReleaseReport(
        action=action,
        authorized=True,
        performed=False,
        capabilities=report.capabilities,
        note=(
            "artifact verified; the gate does not publish, push, or tag by itself — "
            "the actual release step is a separately authorized human action"
        ),
    )
