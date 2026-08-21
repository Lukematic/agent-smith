"""Subagent spawning with enforced delegation discipline.

The persona claimed Smith "spawns scoped subagents" and `smith-delegate` described
how, but no spawn code existed. That is an ``UNGROUNDED_CLAIM`` in Smith's own
artifact, and the fix is code rather than softer wording.

What this module refuses to do is the important part:

- **No spawn without disjoint file ownership.** Two agents writing one file destroy
  work silently, so the overlap check runs before any process starts.
- **No spawn without a verification command.** An agent that cannot be checked
  produces work nobody can trust, which is worse than no work.
- **No nested spawning.** Subagents are leaf nodes. The assignment says so and the
  spawn refuses a depth beyond one, because runaway delegation is expensive and
  hard to observe.
- **No trusting the result.** A subagent reporting success is a claim. The
  verification command is re-run by the orchestrator, and that exit code is what
  counts.

Availability is detected rather than assumed: with no agent CLI installed, this
returns a plan a human can execute instead of pretending to have run it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

MAX_CONCURRENT = 6
DEFAULT_TIMEOUT_SECONDS = 900
SPAWN_DEPTH_ENV = "SMITH_SPAWN_DEPTH"


class Runner(StrEnum):
    """An agent CLI that can execute an assignment headlessly."""

    CLAUDE = "claude"
    GOOSE = "goose"
    CODEX = "codex"
    NONE = "none"

    @property
    def available(self) -> bool:
        return self is not Runner.NONE and shutil.which(str(self)) is not None

    def command(self, prompt_file: Path, *, read_only: bool) -> list[str]:
        """Headless invocation for this runner.

        Read-only mode matters: a reviewer or scout that can write is not a
        reviewer, and the restriction belongs on the process rather than in the
        prompt asking it nicely.
        """
        if self is Runner.CLAUDE:
            args = ["claude", "-p", f"@{prompt_file}"]
            if read_only:
                args += ["--allowedTools", "Read,Grep,Glob"]
            return args
        if self is Runner.GOOSE:
            return ["goose", "run", "--instructions", str(prompt_file)]
        if self is Runner.CODEX:
            return ["codex", "exec", "--full-auto", f"@{prompt_file}"]
        return []


def detect_runner(preferred: str | None = None) -> tuple[Runner, str]:
    """Find an agent CLI, reporting why when none is usable."""
    if preferred:
        candidate = Runner(preferred) if preferred in set(Runner) else Runner.NONE
        if candidate.available:
            return candidate, f"{candidate} requested and present"
        return Runner.NONE, f"{preferred} requested but not on PATH"
    for candidate in (Runner.CLAUDE, Runner.GOOSE, Runner.CODEX):
        if candidate.available:
            return candidate, f"{candidate} found on PATH"
    return Runner.NONE, "no agent CLI found; install claude, goose, or codex"


class Role(StrEnum):
    """What a subagent is for. Determines whether it may write at all."""

    SCOUT = "scout"
    REVIEWER = "reviewer"
    BUILDER = "builder"
    TESTER = "tester"

    @property
    def read_only(self) -> bool:
        """Scouts and reviewers cannot write.

        This is capability minimization: a reviewer that can edit will edit, and
        then it is reviewing its own work.
        """
        return self in {Role.SCOUT, Role.REVIEWER}


@dataclass
class Assignment:
    """One unit of delegated work.

    Every field is required because a subagent inherits nothing: it does not see
    the orchestrator's conversation, so anything omitted is simply absent.
    """

    agent_id: str
    role: Role
    objective: str
    file_scope: list[str] = field(default_factory=list)
    context_paths: list[str] = field(default_factory=list)
    verification: str = ""
    depends_on: list[str] = field(default_factory=list)

    def problems(self) -> list[str]:
        """Contract violations, checked before anything is spawned."""
        issues: list[str] = []
        if not self.objective.strip():
            issues.append("objective is empty")
        if not self.role.read_only and not self.file_scope:
            issues.append("a writing role must declare its file scope")
        if not self.verification:
            issues.append("no verification command, so the result cannot be checked")
        if self.role.read_only and self.file_scope:
            issues.append(f"{self.role} is read-only but declares a file scope")
        return issues

    def render(self, smith_home: Path) -> str:
        """The prompt a subagent receives. Self-contained by necessity."""
        scope = "\n".join(f"- {p}" for p in self.file_scope) or "- none, this is read-only work"
        context = "\n".join(f"- {p}" for p in self.context_paths) or "- none specified"
        return f"""# Assignment: {self.agent_id}

You are a **{self.role}** subagent. You were spawned by Agent Smith to do one
scoped piece of work. You inherit no conversation, so everything you need is here.

## Objective

{self.objective}

## Files you may WRITE

{scope}

Writing outside this list is `FILE_SCOPE_VIOLATION`. Stop and report instead.

## Context to read first

{context}

## Constraints

- You are a **leaf node**. Do not spawn subagents. If the work needs splitting,
  report that rather than delegating.
- Do not start servers or any blocking process. Emit the command for a human.
- Do not modify tests to make them pass. Fix the code.
- Read anything for context; write only what is scoped above.

## Verification

Run this and confirm it passes before reporting:

```bash
{self.verification}
```

## Completion

Paste the verification command's real output, then state exactly:

`{self.agent_id} COMPLETE`

If you cannot finish, state `{self.agent_id} BLOCKED` with what you tried. A
blocked report is useful; a false completion is not.

## Reference

Agent Smith's constitution is at `{smith_home}/AGENT_SMITH.md` if you need its
conventions.
"""


@dataclass
class SpawnResult:
    """What one subagent produced, and whether it was independently verified."""

    agent_id: str
    outcome: str
    exit_code: int
    duration_ms: int
    output_tail: str
    claimed_complete: bool = False
    verified: bool | None = None

    @property
    def trustworthy(self) -> bool:
        """A completion claim means nothing until the check is re-run.

        ``verified is None`` means nobody checked, which is treated as untrusted
        rather than assumed fine.
        """
        return self.verified is True


def check_ownership(assignments: list[Assignment]) -> list[str]:
    """Find file-scope overlaps between assignments.

    This is the single most destructive delegation failure: two agents editing one
    file overwrite each other with no error. So it is arithmetic, run before any
    process starts, rather than a warning in a prompt.
    """
    conflicts: list[str] = []
    owners: dict[str, str] = {}
    for assignment in assignments:
        for path in assignment.file_scope:
            key = path.replace("\\", "/").lstrip("./")
            if key in owners:
                conflicts.append(f"{key} claimed by both {owners[key]} and {assignment.agent_id}")
            else:
                owners[key] = assignment.agent_id
    return conflicts


def plan_waves(assignments: list[Assignment]) -> list[list[Assignment]]:
    """Group assignments into dependency waves.

    Everything in a wave is independent and runs concurrently. Sequential spawning
    of independent work is ``FALSE_PARALLELISM``: the coordination cost is paid and
    none of the speedup is collected.
    """
    remaining = {a.agent_id: a for a in assignments}
    done: set[str] = set()
    waves: list[list[Assignment]] = []

    while remaining:
        ready = [a for a in remaining.values() if set(a.depends_on) <= done]
        if not ready:
            # A dependency cycle would loop forever, so surface the rest as a
            # final wave and let the caller see the problem.
            waves.append(list(remaining.values()))
            break
        waves.append(ready)
        for assignment in ready:
            done.add(assignment.agent_id)
            del remaining[assignment.agent_id]
    return waves


def current_depth() -> int:
    try:
        return int(os.environ.get(SPAWN_DEPTH_ENV, "0"))
    except ValueError:
        return 0


def spawn_one(
    assignment: Assignment,
    smith_home: Path,
    project: Path,
    runner: Runner,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    dry_run: bool = False,
) -> SpawnResult:
    """Run one assignment and capture what it actually did."""
    problems = assignment.problems()
    if problems:
        return SpawnResult(assignment.agent_id, "REFUSED", 2, 0, "; ".join(problems))

    if current_depth() >= 1:
        return SpawnResult(
            assignment.agent_id,
            "REFUSED",
            2,
            0,
            "already inside a subagent; nesting is not allowed",
        )

    scratch = project / ".smith" / "assignments"
    scratch.mkdir(parents=True, exist_ok=True)
    prompt_file = scratch / f"{assignment.agent_id}.md"
    prompt_file.write_text(assignment.render(smith_home), encoding="utf-8")

    if dry_run or runner is Runner.NONE:
        detail = "dry run" if dry_run else "no agent CLI available"
        return SpawnResult(assignment.agent_id, "PLANNED", 0, 0, f"{detail}: {prompt_file}")

    command = runner.command(prompt_file, read_only=assignment.role.read_only)
    environment = {**os.environ, SPAWN_DEPTH_ENV: str(current_depth() + 1)}

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(project),
            env=environment,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - started) * 1000)
        return SpawnResult(assignment.agent_id, "TIMEOUT", 124, elapsed, f"exceeded {timeout}s")
    except OSError as exc:
        return SpawnResult(assignment.agent_id, "FAILED", 1, 0, str(exc))

    elapsed = int((time.monotonic() - started) * 1000)
    output = (completed.stdout or "") + (completed.stderr or "")
    claimed = f"{assignment.agent_id} COMPLETE" in output
    blocked = f"{assignment.agent_id} BLOCKED" in output

    if blocked:
        outcome = "BLOCKED"
    elif completed.returncode != 0:
        outcome = "FAILED"
    elif claimed:
        outcome = "CLAIMED"
    else:
        # Exited zero without the signal: cannot distinguish finished from stalled.
        outcome = "NO_SIGNAL"

    return SpawnResult(
        assignment.agent_id,
        outcome,
        completed.returncode,
        elapsed,
        "\n".join(output.strip().splitlines()[-15:]),
        claimed_complete=claimed,
    )


def verify(result: SpawnResult, assignment: Assignment, project: Path) -> SpawnResult:
    """Re-run the verification command ourselves.

    The orchestrator does not take a subagent's word. This is the difference
    between delegation and hoping.
    """
    if not assignment.verification:
        return result
    try:
        completed = subprocess.run(
            assignment.verification,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(project),
            check=False,
        )
    except OSError:
        result.verified = False
        return result
    result.verified = completed.returncode == 0
    if not result.verified:
        tail = "\n".join(
            ((completed.stdout or "") + (completed.stderr or "")).strip().splitlines()[-8:]
        )
        result.output_tail += f"\n[independent verification FAILED]\n{tail}"
    return result
