"""Self-healing: diagnose a failure, apply a known remedy, retry.

Grounded in two book chapters, not improvised:

- chapters/6-harnesses/5-harness-engineering.md — Hashimoto's principle: "anytime
  an agent makes a mistake, engineer a solution such that it never makes that
  mistake again." A diagnose-then-remedy table is that principle turned into code:
  each named failure gets a structural fix once, and every future occurrence of
  that signature is handled without rediscovering it.
- chapters/9-mental-models/8-loop-engineering.md — "verification becomes the
  binding constraint on how far the loop can run unattended." This is why healing
  stops after three attempts and why a remedy that reports success but does not
  change the outcome is treated as a failure, not a retry.

The failure this replaces: A.W.I.N.O. spawned a subagent, it failed on
"Not logged in", and A.W.I.N.O. reported only `FAILED  unverified`. The exit code was
captured correctly and the diagnosis was absent, which is the difference between a
ledger and a colleague.

Three rules keep this from becoming a retry loop that hides real breakage:

- A remedy must be idempotent. Running it twice must be safe, because a retry may
  run it again.
- A remedy must not require judgement. Re-syncing an environment is mechanical.
  Choosing what a test should assert is not, and is reported instead.
- The same diagnosis recurring after its remedy already ran means the remedy did
  not address the cause. Applying it a third time would only spend money proving
  that again, so the loop stops and reports instead of repeating the remedy.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

MAX_HEAL_ATTEMPTS = 3


class Failure(StrEnum):
    """Named failure classes. A class A.W.I.N.O. cannot name, it cannot heal."""

    AUTH_MISSING = "AUTH_MISSING"
    CLI_MISSING = "CLI_MISSING"
    ENV_UNSYNCED = "ENV_UNSYNCED"
    ENV_MISSING = "ENV_MISSING"
    HARDLINK_REFUSED = "HARDLINK_REFUSED"
    NO_TEST_TARGET = "NO_TEST_TARGET"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    STALE_LOCK = "STALE_LOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Diagnosis:
    """What went wrong, and what to do about it."""

    failure: Failure
    evidence: str
    remedy_description: str
    remedy: Callable[[Path], tuple[bool, str]] | None = None
    human_action: str = ""

    @property
    def self_healable(self) -> bool:
        return self.remedy is not None

    @property
    def report(self) -> str:
        if self.self_healable:
            return f"{self.failure}: {self.evidence} -> healing by {self.remedy_description}"
        return f"{self.failure}: {self.evidence} -> needs you: {self.human_action}"


# ── remedies ─────────────────────────────────────────────────────────────────
# Each returns (succeeded, detail). All are idempotent, because a retry may run
# them again.


def _run(command: list[str], cwd: Path, timeout: int = 300) -> tuple[bool, str]:
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = ((done.stdout or "") + (done.stderr or "")).strip()
    return done.returncode == 0, output[-400:]


def heal_env_unsynced(project: Path) -> tuple[bool, str]:
    """Re-sync the environment, forcing copy mode.

    Hardlinking fails on OneDrive, network shares, and Docker volumes, so the
    remedy sets the one variable that makes sync work everywhere.
    """
    env = dict(os.environ)
    env["UV_LINK_MODE"] = "copy"
    try:
        done = subprocess.run(
            ["uv", "sync", "--all-groups"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(project),
            env=env,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return done.returncode == 0, "uv sync with UV_LINK_MODE=copy"


def heal_stale_lock(project: Path) -> tuple[bool, str]:
    """Remove lock files left by a crashed process.

    Safe because a lock whose owner is gone is not protecting anything.
    """
    removed: list[str] = []
    for pattern in ("*.lock", ".seeds/*.lock", ".smith/**/*.lock"):
        for path in project.glob(pattern):
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                continue
    if not removed:
        return False, "no stale locks found"
    return True, f"removed {', '.join(removed)}"


def heal_missing_env(project: Path) -> tuple[bool, str]:
    """Create the virtual environment, then install into it."""
    created, detail = _run(["uv", "venv"], project)
    if not created:
        return False, detail
    return heal_env_unsynced(project)


# ── detection ────────────────────────────────────────────────────────────────
# Ordered most specific first: a broad pattern placed early would swallow a
# precise one, because many messages contain generic words like "not found".

SIGNATURES: tuple[tuple[Failure, tuple[str, ...], str, Callable | None, str], ...] = (
    (
        Failure.AUTH_MISSING,
        ("not logged in", "please run /login", "authentication_error", "invalid api key", "401"),
        "cannot be healed automatically: credentials are yours to provide",
        None,
        "run 'claude /login', set ANTHROPIC_API_KEY, or point the runner at your "
        "own gateway (see docs/api-keys.md). A.W.I.N.O. will not touch your credentials.",
    ),
    (
        Failure.RATE_LIMITED,
        ("rate limit", "429", "too many requests", "overloaded"),
        "waiting is the only remedy, and A.W.I.N.O. does not sleep on your behalf",
        None,
        "wait and retry. If this repeats, reduce concurrency with fewer subagents.",
    ),
    (
        Failure.HARDLINK_REFUSED,
        ("failed to hardlink", "incompatible hardlinks", "os error 396"),
        "re-syncing with UV_LINK_MODE=copy",
        heal_env_unsynced,
        "",
    ),
    (
        Failure.ENV_MISSING,
        ("no virtual environment", "no `.venv`", "no interpreter found"),
        "creating the environment and installing dependencies",
        heal_missing_env,
        "",
    ),
    (
        Failure.ENV_UNSYNCED,
        ("no module named", "modulenotfounderror", "is not installed", "failed to install"),
        "re-syncing the environment",
        heal_env_unsynced,
        "",
    ),
    (
        Failure.STALE_LOCK,
        ("lock file", "already locked", "resource temporarily unavailable", "advisory lock"),
        "removing stale lock files",
        heal_stale_lock,
        "",
    ),
    (
        Failure.NO_TEST_TARGET,
        ("file or directory not found", "no tests ran", "no tests collected"),
        "cannot be healed: the test target is a decision, not a mechanism",
        None,
        "point the gate at a test path that exists, or write the test first.",
    ),
    (
        Failure.CLI_MISSING,
        ("is not recognized", "command not found", "no such file or directory: "),
        "cannot be healed: installing arbitrary tooling is not A.W.I.N.O.'s call",
        None,
        "install the missing command, then retry.",
    ),
    (
        Failure.PERMISSION_DENIED,
        ("permission denied", "access is denied", "operation not permitted", "eacces"),
        "cannot be healed: elevation is yours to grant",
        None,
        "check file ownership, or enable Developer Mode on Windows for symlinks.",
    ),
    (
        Failure.NETWORK,
        ("connection refused", "temporary failure in name resolution", "timed out", "unreachable"),
        "cannot be healed: the network is outside A.W.I.N.O.",
        None,
        "check connectivity or proxy settings, then retry.",
    ),
    (
        Failure.TIMEOUT,
        ("exceeded", "timeoutexpired", "deadline"),
        "cannot be healed automatically: a longer timeout may hide a real hang",
        None,
        "raise --timeout only if the work is genuinely long, otherwise investigate the hang.",
    ),
)


def diagnose(output: str, exit_code: int = 1) -> Diagnosis:
    """Name the failure from its output.

    An unrecognised failure is reported as ``UNKNOWN`` with the tail attached,
    rather than guessed at. A wrong diagnosis sends effort at the wrong surface,
    which is worse than no diagnosis.
    """
    lowered = output.lower()
    for failure, patterns, description, remedy, human in SIGNATURES:
        if any(pattern in lowered for pattern in patterns):
            matched = next(p for p in patterns if p in lowered)
            return Diagnosis(failure, f"matched {matched!r}", description, remedy, human)

    tail = " ".join(output.strip().splitlines()[-2:])[:200] or f"exit code {exit_code}"
    return Diagnosis(
        Failure.UNKNOWN,
        tail,
        "no known remedy for this signature",
        None,
        "read the output above. If this recurs, add a signature to smith/healing.py "
        "so the next occurrence is diagnosed rather than rediscovered.",
    )


@dataclass
class HealingAttempt:
    """One diagnose-heal-retry cycle, recorded."""

    attempt: int
    diagnosis: Diagnosis
    healed: bool
    detail: str


@dataclass
class HealingRun:
    """The full history of trying to make one command succeed."""

    command: str
    attempts: list[HealingAttempt] = field(default_factory=list)
    succeeded: bool = False
    final_output: str = ""

    @property
    def exhausted(self) -> bool:
        return len(self.attempts) >= MAX_HEAL_ATTEMPTS and not self.succeeded

    @property
    def blocked_on_human(self) -> bool:
        return bool(self.attempts) and not self.attempts[-1].diagnosis.self_healable

    def summary(self) -> str:
        if self.succeeded:
            healed = [a for a in self.attempts if a.healed]
            if not healed:
                return "succeeded on the first attempt"
            names = ", ".join(str(a.diagnosis.failure) for a in healed)
            return f"succeeded after healing {names}"
        if self.blocked_on_human:
            return f"blocked: {self.attempts[-1].diagnosis.human_action}"
        if self.attempts and "did not address the cause" in self.attempts[-1].detail:
            return (
                f"gave up: {self.attempts[-1].diagnosis.failure} recurred after its own "
                "remedy ran, so retrying it would not have changed the outcome"
            )
        if self.attempts and not self.attempts[-1].healed:
            # The remedy itself could not run, distinct from "ran but did not help".
            last = self.attempts[-1]
            return (
                f"gave up: the remedy for {last.diagnosis.failure} failed to apply ({last.detail})"
            )
        if self.exhausted:
            tried = ", ".join(str(a.diagnosis.failure) for a in self.attempts)
            return f"THREE_STRIKES after {tried}. Stop and escalate rather than retrying."
        return "did not succeed"


def run_with_healing(
    command: str,
    project: Path,
    *,
    max_attempts: int = MAX_HEAL_ATTEMPTS,
    timeout: int = 900,
) -> HealingRun:
    """Run a command, healing known failures between attempts.

    Stops early when the diagnosis needs a human, or when the same diagnosis
    recurs after a remedy that reported success. A remedy "succeeding" but the
    identical failure returning means it did not address the real cause, and
    applying it a third time would only spend money proving that again.
    """
    run = HealingRun(command=command)
    last_failure: Failure | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            done = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(project),
                timeout=timeout,
                check=False,
            )
            output = ((done.stdout or "") + (done.stderr or "")).strip()
            code = done.returncode
        except subprocess.TimeoutExpired:
            output, code = f"timeout exceeded {timeout}s", 124
        except OSError as exc:
            output, code = str(exc), 1

        run.final_output = "\n".join(output.splitlines()[-20:])

        if code == 0:
            run.succeeded = True
            return run

        diagnosis = diagnose(output, code)

        if not diagnosis.self_healable:
            run.attempts.append(HealingAttempt(attempt, diagnosis, False, "needs a human"))
            return run

        if diagnosis.failure is last_failure and attempt > 1:
            run.attempts.append(
                HealingAttempt(
                    attempt,
                    diagnosis,
                    False,
                    "same failure recurred after the remedy; it did not address the cause",
                )
            )
            return run

        healed, detail = diagnosis.remedy(project)  # type: ignore[misc]
        run.attempts.append(HealingAttempt(attempt, diagnosis, healed, detail))
        last_failure = diagnosis.failure

        if not healed:
            # The remedy itself failed, so retrying the command changes nothing.
            return run

    return run
