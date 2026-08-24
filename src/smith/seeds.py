"""Seeds integration: one worklist, and gate closure that closes issues.

Seeds is a git-native issue tracker where the JSONL file is the database. Smith
integrates with it rather than replacing it, because two worklists is worse than
one: a markdown checklist beside a real tracker means nobody knows which is
authoritative. That failure has a name here, ``COMPETING_TRACKER``.

Three things this buys:

1. **A run can name the issue it serves.** Work without a tracked reason is work
   nobody asked for.
2. **Closing a gate can close an issue**, with the verification output as the
   close reason. An issue closed with "done" proves nothing; one closed with a
   pasted exit code does.
3. **Verification issues become discoverable.** An issue labelled ``verify``
   describes a check somebody wanted, which is exactly the input a gate needs.

Every call shells out to ``sd`` with ``--json``. Smith never writes
``.seeds/*.jsonl`` directly: the CLI owns advisory locking and atomic writes, and
hand-editing the JSONL would corrupt concurrent agent runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Labels Smith understands. Seeds itself treats labels as opaque strings.
VERIFY_LABEL = "verify"
BLOCKED_LABEL = "blocked-on-verification"

# Priority scale is seeds', not ours. Documented so callers do not invent one.
PRIORITY_LABELS = {0: "critical", 1: "high", 2: "medium", 3: "low", 4: "backlog"}


class SeedsState(StrEnum):
    """Whether seeds is usable here, and why not when it is not."""

    READY = "ready"
    NOT_INSTALLED = "not-installed"
    NOT_INITIALIZED = "not-initialized"
    BROKEN = "broken"

    @property
    def usable(self) -> bool:
        return self is SeedsState.READY

    @property
    def blocking(self) -> bool:
        """Seeds is never a blocking dependency.

        Smith works in repositories it does not own. A missing tracker is a
        degraded capability, not a failure: Smith falls back to reporting work
        rather than tracking it. Treating an absent optional tool as an error
        would make Smith unusable in exactly the repositories it is meant to help.
        """
        return False


# Installing a tracker into someone else's repository is a committed, git-visible
# mutation nobody asked for. Smith states the effects, offers, and abides.
INSTALL_HINT = "bun install -g @os-eco/seeds-cli"
INIT_HINT = "sd init"
INIT_EFFECTS = (
    ".seeds/config.yaml      project name and plan settings",
    ".seeds/issues.jsonl     the issue database, one JSON object per line",
    ".seeds/templates.jsonl  plan template definitions",
    ".gitattributes          adds merge=union so parallel branches merge cleanly",
)


@dataclass(frozen=True)
class Issue:
    """One tracked piece of work."""

    id: str
    title: str
    status: str
    type: str
    priority: int
    labels: tuple[str, ...] = ()
    assignee: str | None = None
    description: str = ""

    @property
    def open(self) -> bool:
        return self.status in {"open", "in_progress"}

    @property
    def wants_verification(self) -> bool:
        """Whether this issue describes a check rather than a change.

        Detected from the label first, then from the title, because practitioners
        write "Verify X" long before they think to add a label.
        """
        if VERIFY_LABEL in self.labels:
            return True
        opening = self.title.lower().split()
        return bool(opening) and opening[0] in {"verify", "validate", "check", "confirm", "test"}

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS.get(self.priority, str(self.priority))

    @classmethod
    def from_json(cls, raw: dict) -> Issue:
        labels = raw.get("labels") or []
        return cls(
            id=raw.get("id", ""),
            title=raw.get("title", ""),
            status=raw.get("status", "open"),
            type=raw.get("type", "task"),
            priority=int(raw.get("priority", 2)),
            labels=tuple(labels),
            assignee=raw.get("assignee"),
            description=raw.get("description") or "",
        )


@dataclass(frozen=True)
class SeedsResult:
    """Outcome of one ``sd`` invocation."""

    ok: bool
    command: str
    detail: str
    payload: dict | list | None = None


class Seeds:
    """Thin, honest wrapper over the ``sd`` CLI."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root

    # ── availability ─────────────────────────────────────────────────────────
    @property
    def installed(self) -> bool:
        return shutil.which("sd") is not None

    @property
    def initialized(self) -> bool:
        return (self.root / ".seeds" / "issues.jsonl").is_file()

    def state(self) -> tuple[SeedsState, str]:
        """Report usability without pretending. A tracker Smith cannot reach is
        not a tracker Smith should claim to be using."""
        if not self.installed:
            return (
                SeedsState.NOT_INSTALLED,
                f"sd is not on PATH; optional, install with '{INSTALL_HINT}'",
            )
        if not self.initialized:
            inherited = self._inherited_root()
            if inherited:
                return SeedsState.READY, f"using the tracker at {inherited}"
            return (
                SeedsState.NOT_INITIALIZED,
                f"no .seeds/ here; run '{INIT_HINT}' yourself if you want one",
            )
        version = self._version()
        if version is None:
            return SeedsState.BROKEN, "sd is present but did not report a version"
        return SeedsState.READY, f"sd {version} with a tracker at {self.tracker_root}"

    def init(self, *, confirmed: bool = False) -> SeedsResult:
        """Initialize a tracker, only when explicitly confirmed.

        Refuses by default. ``sd init`` writes ``.seeds/`` and edits
        ``.gitattributes`` in a repository Smith may not own, so the human asks
        for it or it does not happen.
        """
        if not confirmed:
            return SeedsResult(
                False,
                "init",
                f"refusing to create a tracker unasked. Run '{INIT_HINT}' yourself, "
                "or pass --confirm to have Smith do it.",
            )
        if not self.installed:
            return SeedsResult(False, "init", f"sd is not installed; '{INSTALL_HINT}'")
        if self.initialized:
            return SeedsResult(True, "init", "already initialized")
        return self._raw(["init"])

    def _version(self) -> str | None:
        result = self._raw(["--version"])
        return result.detail.strip() if result.ok else None

    def _inherited_root(self) -> Path | None:
        """Seeds resolves upward, so a subdirectory inherits its parent's tracker."""
        for candidate in self.root.parents:
            if (candidate / ".seeds" / "issues.jsonl").is_file():
                return candidate
        return None

    @property
    def tracker_root(self) -> Path | None:
        if self.initialized:
            return self.root
        return self._inherited_root()

    # ── invocation ───────────────────────────────────────────────────────────
    def _raw(self, args: list[str]) -> SeedsResult:
        if not self.installed:
            return SeedsResult(False, " ".join(args), "sd is not installed")
        try:
            done = subprocess.run(
                ["sd", *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self.root),
                check=False,
            )
        except (OSError, UnicodeError) as exc:
            # A tracker containing characters the console codec cannot represent
            # must degrade to "unavailable", not crash the whole command. Windows
            # defaults to cp1252 here, which fails on any emoji or smart quote.
            return SeedsResult(False, " ".join(args), str(exc))
        output = (done.stdout or "") + (done.stderr or "")
        return SeedsResult(done.returncode == 0, " ".join(args), output.strip())

    def _json(self, args: list[str]) -> SeedsResult:
        result = self._raw([*args, "--json"])
        if not result.ok:
            return result
        try:
            payload = json.loads(result.detail)
        except json.JSONDecodeError:
            return SeedsResult(
                False, result.command, f"sd returned non-JSON output: {result.detail[:160]}"
            )
        # Seeds wraps mutations as {success, command, ...} but returns bare
        # arrays or {issues: [...]} for reads. Handle both without guessing.
        if isinstance(payload, dict) and payload.get("success") is False:
            return SeedsResult(
                False, result.command, payload.get("error", "unknown error"), payload
            )
        return SeedsResult(True, result.command, result.detail, payload)

    @staticmethod
    def _issues_from(payload: dict | list | None) -> list[Issue]:
        if payload is None:
            return []
        if isinstance(payload, list):
            rows = payload
        else:
            issue = payload.get("issue")
            rows = payload.get("issues") or payload.get("results") or ([issue] if issue else [])
        return [Issue.from_json(row) for row in rows if isinstance(row, dict)]

    # ── reads ────────────────────────────────────────────────────────────────
    def ready(self, limit: int = 20) -> list[Issue]:
        """Open issues with no unresolved blockers: what may be worked now."""
        result = self._json(["ready", "--limit", str(limit)])
        return self._issues_from(result.payload) if result.ok else []

    def list_open(self, limit: int = 50) -> list[Issue]:
        result = self._json(["list", "--status", "open", "--limit", str(limit)])
        return self._issues_from(result.payload) if result.ok else []

    def show(self, issue_id: str) -> Issue | None:
        result = self._json(["show", issue_id])
        issues = self._issues_from(result.payload)
        if issues:
            return issues[0]
        if result.ok and isinstance(result.payload, dict) and result.payload.get("id"):
            return Issue.from_json(result.payload)
        return None

    def verification_issues(self, limit: int = 50) -> list[Issue]:
        """Issues that describe a check somebody wanted performed.

        These are the highest-value input to the gate ledger: each one is a
        human-authored statement of what "verified" should mean here.
        """
        return [issue for issue in self.list_open(limit) if issue.wants_verification]

    def blocked(self) -> list[Issue]:
        result = self._json(["blocked"])
        return self._issues_from(result.payload) if result.ok else []

    def doctor(self) -> SeedsResult:
        """Seeds' own integrity check. Its failures are data problems, not ours."""
        return self._raw(["doctor"])

    def prime(self, compact: bool = True) -> str:
        """Tracker context for an agent, produced by seeds rather than guessed."""
        args = ["prime"]
        if compact:
            args.append("--compact")
        result = self._raw(args)
        return result.detail if result.ok else ""

    # ── writes ───────────────────────────────────────────────────────────────
    def create(
        self,
        title: str,
        *,
        issue_type: str = "task",
        priority: int = 2,
        description: str = "",
        labels: list[str] | None = None,
    ) -> SeedsResult:
        args = ["create", "--title", title, "--type", issue_type, "--priority", str(priority)]
        if description:
            args += ["--description", description]
        result = self._json(args)
        if not result.ok:
            return result
        issue_id = result.payload.get("id") if isinstance(result.payload, dict) else None
        for label in labels or []:
            if issue_id:
                self._raw(["label", "add", issue_id, label])
        return SeedsResult(True, result.command, issue_id or "created", result.payload)

    def start(self, issue_id: str) -> SeedsResult:
        return self._json(["update", issue_id, "--status", "in_progress"])

    def close(self, issue_id: str, reason: str) -> SeedsResult:
        """Close an issue with a reason.

        Callers should pass verification evidence, not a summary. "Implemented"
        proves nothing; "pytest exit 0, 132 passed" is a claim someone can audit.
        """
        return self._json(["close", issue_id, "--reason", reason])

    def sync(self, dry_run: bool = False) -> SeedsResult:
        """Commit tracker changes. Only on explicit request, never implicitly."""
        args = ["sync"]
        if dry_run:
            args.append("--dry-run")
        return self._raw(args)


# ── the bridge between seeds and the gate ledger ─────────────────────────────


@dataclass(frozen=True)
class ClosureCheck:
    """Whether an issue has earned closure, judged from gate evidence."""

    issue_id: str
    may_close: bool
    reason: str
    evidence_summary: str

    @property
    def close_reason(self) -> str:
        """A close reason that carries proof rather than assertion."""
        return f"{self.reason} [{self.evidence_summary}]"


def summarise_evidence(evidence: list) -> str:
    """Condense ledger evidence into an auditable one-liner for a close reason."""
    if not evidence:
        return "no evidence recorded"
    parts: list[str] = []
    for item in evidence:
        marker = "ok" if item.exit_code == 0 else f"exit {item.exit_code}"
        kind = "attested" if item.command.startswith("ATTEST ") else "executed"
        parts.append(f"{item.gate}={marker}/{kind}")
    return "; ".join(parts)


def check_closure(issue_id: str, verdict, evidence: list) -> ClosureCheck:
    """Decide whether a gate verdict justifies closing a tracked issue.

    An issue closed while gates are unmet is a lie that survives in git history,
    which is worse than an open issue. So closure borrows the ledger's verdict
    rather than forming its own opinion.
    """
    summary = summarise_evidence(evidence)
    if verdict is None:
        return ClosureCheck(issue_id, False, "no run is open for this issue", summary)
    if not verdict.can_close:
        return ClosureCheck(issue_id, False, f"gates unmet: {verdict.blocked_reason}", summary)
    executed = [item for item in evidence if not item.command.startswith("ATTEST ")]
    if not executed:
        return ClosureCheck(
            issue_id,
            False,
            "every gate was attested and none executed, so nothing was actually verified",
            summary,
        )
    return ClosureCheck(issue_id, True, "all gates satisfied", summary)
