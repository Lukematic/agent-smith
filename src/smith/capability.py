"""Self-knowledge: what A.W.I.N.O. can actually do, verified rather than claimed.

This module exists because of a real failure. A.W.I.N.O.'s persona said it "spawns
scoped subagents" and a skill described how, but no spawn code existed. Nothing
caught it: prose describing a capability is indistinguishable from prose
describing an aspiration, and the agent reads both the same way.

So capabilities are **probed**, not declared. Each one names a check that inspects
the running system, and the answer is computed at call time. A capability whose
probe fails is reported as absent no matter what any document says.

The three states matter:

- ``REAL``      the probe passed; A.W.I.N.O. may claim this
- ``DEGRADED``  present but limited; the limit must be stated with the claim
- ``ABSENT``    the probe failed; claiming it is UNGROUNDED_CAPABILITY

The point is not documentation. It is that ``awino limits`` can contradict
``AWINO.md``, and when it does, the probe wins.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from smith.paths import SmithPaths


class State(StrEnum):
    REAL = "REAL"
    DEGRADED = "DEGRADED"
    ABSENT = "ABSENT"

    @property
    def claimable(self) -> bool:
        return self is State.REAL

    @property
    def needs_caveat(self) -> bool:
        return self is State.DEGRADED


@dataclass(frozen=True)
class Capability:
    """One thing A.W.I.N.O. might be able to do, and the truth about it."""

    name: str
    state: State
    detail: str
    limit: str = ""

    @property
    def honest_claim(self) -> str:
        """How A.W.I.N.O. is permitted to describe this capability."""
        if self.state is State.REAL:
            return f"can {self.name}"
        if self.state is State.DEGRADED:
            return f"can {self.name}, but {self.limit}"
        return f"cannot {self.name}: {self.detail}"


def _real(name: str, detail: str) -> Capability:
    return Capability(name, State.REAL, detail)


def _degraded(name: str, detail: str, limit: str) -> Capability:
    return Capability(name, State.DEGRADED, detail, limit)


def _absent(name: str, detail: str) -> Capability:
    return Capability(name, State.ABSENT, detail)


# ── probes ───────────────────────────────────────────────────────────────────
# Each probe inspects the running system. None of them read documentation, which
# is the whole point: a doc cannot be wrong about itself here.


def probe_knowledge(paths: SmithPaths) -> Capability:
    from smith.knowledge import KnowledgeStore

    store = KnowledgeStore(paths)
    indexed = len(store.registry_paths())
    if not indexed:
        return _absent("consult a knowledge base", "REGISTRY.yaml is missing or unparseable")
    age = store.manifest.newest_age_days
    if age is not None and age >= store.stale_days:
        return _degraded(
            "consult a knowledge base",
            f"{indexed} chapters indexed",
            f"the cache is {age} days old, so say so when citing it",
        )
    return _real("consult a knowledge base", f"{indexed} chapters indexed, fetched on demand")


def probe_gate(_paths: SmithPaths) -> Capability:
    from smith.enforce import CONTRACTS

    if not CONTRACTS:
        return _absent("gate completion", "no task contracts defined")
    return _real(
        "gate completion on recorded exit codes",
        f"{len(CONTRACTS)} task classes with fixed gates",
    )


def probe_spawn(_paths: SmithPaths) -> Capability:
    """Probe the implementation separately from optional runtime readiness.

    ``spawn.py`` is part of A.W.I.N.O. and is testable on every machine. The external
    runner is an adapter selected at runtime. Treating a missing Claude/Goose/Codex
    executable as an absent *product capability* made the repository fail its own
    CI on every clean GitHub runner, even though the implementation and its tests
    were present. That confused "not configured here" with "not implemented".

    A configured runner is REAL. An implemented adapter with no local runner is
    DEGRADED and must state the prerequisite. ABSENT is reserved for capability
    code that genuinely does not exist.
    """
    from smith.spawn import detect_runner

    runner, reason = detect_runner()
    if runner.available:
        return _real("spawn scoped subagents", f"via {runner}: {reason}")
    return _degraded(
        "spawn scoped subagents",
        "the scoped delegation implementation is installed",
        f"{reason}; install and authenticate one runner before spawning",
    )


def probe_self_repair(_paths: SmithPaths) -> Capability:
    from smith import fix

    safe = len(fix.SAFE_REPAIRS)
    manual = len(fix.REPORT_ONLY)
    return _degraded(
        "repair itself",
        f"{safe} automatic repairs, {manual} reported for judgement",
        "only mechanically derivable fixes are automatic; prose and verification "
        "commands are reported, never guessed",
    )


def probe_memory(paths: SmithPaths) -> Capability:
    import re

    if not paths.lessons.is_file():
        return _absent("remember lessons across sessions", "memory/lessons.md does not exist")
    text = paths.lessons.read_text(encoding="utf-8")
    count = len(re.findall(r"^- \[\d{4}-\d{2}-\d{2}\]", text, re.MULTILINE))
    if not count:
        return _degraded(
            "remember lessons across sessions",
            "the ledger exists but is empty",
            "nothing has been learned yet, so do not claim experience",
        )
    if paths.is_bundled:
        return _degraded(
            "remember lessons across sessions",
            f"{count} lessons",
            "installed as a wheel, so new lessons go to ~/.smith and not the bundle",
        )
    return _real("remember lessons across sessions", f"{count} dated lessons, append-only")


def probe_self_improvement(_paths: SmithPaths) -> Capability:
    """Refreshing an index is version tracking, not learning. Say so."""
    return _degraded(
        "keep its knowledge current",
        "awino update diffs the registry against one upstream repository",
        "it tracks a known source; it does not discover new sources, read papers, "
        "or learn unprompted. There is no trigger and no crawler",
    )


def probe_autonomy(paths: SmithPaths) -> Capability:
    """A.W.I.N.O. is harness-level. Claiming loop-level would be wishful labelling."""
    hooks = (paths.root / "hooks" / "hooks.json").is_file()
    detail = "SessionStart hook present" if hooks else "no lifecycle hooks"
    return _degraded(
        "run unattended",
        detail,
        "it has no scheduler, cron, or worktree isolation, so it reacts to a "
        "session and cannot start work on its own",
    )


def probe_toolchain(_paths: SmithPaths) -> Capability:
    from smith.paths import Workspace
    from smith.toolchain import Toolchain

    chain = Toolchain(Workspace.discover().project.root)
    gaps = chain.blocking_gaps
    if not gaps:
        return _real("run a project's own gates", "install, lint, and test commands all resolved")
    return _degraded(
        "run a project's own gates",
        f"{len(gaps)} gap(s)",
        f"{gaps[0]}, so gates here rest on attestation rather than execution",
    )


def probe_harness_install(_paths: SmithPaths) -> Capability:
    from smith.harness import status
    from smith.paths import Workspace

    installed = [t for t, present, _detail in status(Workspace.discover().project.root) if present]
    if not installed:
        return _absent("install itself into a harness", "not installed anywhere yet")
    names = ", ".join(sorted({t.harness.label for t in installed}))
    return _real("install itself into a harness", f"present in {names}")


def probe_tracker(_paths: SmithPaths) -> Capability:
    from smith.paths import Workspace
    from smith.seeds import Seeds

    tracker = Seeds(Workspace.discover().project.root)
    state, reason = tracker.state()
    if state.usable:
        return _real("read and close tracked work", reason)
    return _absent("read and close tracked work", reason)


def probe_diagrams(_paths: SmithPaths) -> Capability:
    """An honest absence. Mermaid in a plan is prose; rendering is not implemented."""
    return _absent(
        "render diagrams",
        "no diagram generation exists. Mermaid blocks in a plan are plain text",
    )


def probe_network(_paths: SmithPaths) -> Capability:
    if shutil.which("git") is None:
        return _degraded(
            "fetch upstream knowledge",
            "httpx is available",
            "git is missing, so drift detection against a clone will not work",
        )
    return _real("fetch upstream knowledge", "httpx and git both available")


PROBES: tuple[Callable[[SmithPaths], Capability], ...] = (
    probe_knowledge,
    probe_gate,
    probe_memory,
    probe_spawn,
    probe_self_repair,
    probe_toolchain,
    probe_harness_install,
    probe_tracker,
    probe_self_improvement,
    probe_autonomy,
    probe_diagrams,
    probe_network,
)


def assess(paths: SmithPaths | None = None) -> list[Capability]:
    resolved = paths or SmithPaths.discover()
    out: list[Capability] = []
    for probe in PROBES:
        try:
            out.append(probe(resolved))
        except Exception as exc:
            # A probe that crashes proves the capability is not dependable, which
            # is information. Swallowing it would be the failure this file exists
            # to prevent.
            out.append(
                _absent(
                    probe.__name__.removeprefix("probe_").replace("_", " "), f"probe failed: {exc}"
                )
            )
    return out


def summary(capabilities: list[Capability]) -> dict[str, int]:
    return {
        "real": sum(1 for c in capabilities if c.state is State.REAL),
        "degraded": sum(1 for c in capabilities if c.state is State.DEGRADED),
        "absent": sum(1 for c in capabilities if c.state is State.ABSENT),
    }


# Claims that appear in A.W.I.N.O.'s own documents, mapped to the probe that decides
# whether they are true. Documents drift; probes do not.
CLAIMED_IN_DOCS: dict[str, Callable[[SmithPaths], Capability]] = {
    "spawns scoped subagents": probe_spawn,
    "consults a live knowledge registry": probe_knowledge,
    "refuses to report work as complete": probe_gate,
    "remembers lessons": probe_memory,
}


def audit_claims(paths: SmithPaths | None = None) -> list[tuple[str, Capability]]:
    """Check each documented claim against reality.

    This is the check that was missing. A claim whose probe returns ABSENT is
    ``UNGROUNDED_CAPABILITY``: the document must change, or the code must.
    """
    resolved = paths or SmithPaths.discover()
    return [(claim, probe(resolved)) for claim, probe in CLAIMED_IN_DOCS.items()]
