"""Claude Code hook decisions derived from confirmed project workflow policy."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from smith.onboarding import ProjectIntent

_BRANCH_CREATE = re.compile(r"\bgit\s+(?:switch\s+-c|checkout\s+-b)\s+([^\s;&|]+)", re.I)
_COMMIT_OR_PUSH = re.compile(r"\bgit\s+(?:commit|push)\b", re.I)


def project_context(intent: ProjectIntent) -> str:
    lines = ["A.W.I.N.O. project memory (human-confirmed):", f"Mission: {intent.mission}"]
    if intent.goals:
        lines.append("Goals: " + "; ".join(intent.goals))
    if intent.tenets:
        lines.append("Binding tenets: " + "; ".join(intent.tenets))
    if intent.expectations:
        lines.append("Expectations: " + "; ".join(intent.expectations))
    workflow = intent.workflow
    if workflow.one_task_per_session:
        lines.append(
            "Session boundary: one task only; start a new session for a materially new task."
        )
    lines.append(f"Planning interview: {workflow.planning_interview}")
    if workflow.issue_required:
        lines.append("Guardrail: a valid issue ID is required before opening implementation work.")
    if workflow.branch_pattern:
        lines.append(f"Guardrail: branch names must match {workflow.branch_pattern}")
    if workflow.changelog_file:
        lines.append(f"Guardrail: commits must include {workflow.changelog_file}")
    return "\n".join(lines)


def pre_tool_decision(intent: ProjectIntent, payload: dict, project: Path) -> dict | None:
    if payload.get("tool_name") != "Bash":
        return None
    command = str((payload.get("tool_input") or {}).get("command") or "")
    workflow = intent.workflow
    created = _BRANCH_CREATE.search(command)
    if created and workflow.branch_pattern:
        branch = created.group(1).strip("'\"")
        if re.fullmatch(workflow.branch_pattern, branch) is None:
            return _deny(
                f"Branch {branch!r} does not match project pattern {workflow.branch_pattern!r}."
            )
        if workflow.base_branch and not _starts_from_base(command, project, workflow.base_branch):
            return _deny(
                f"Create the branch from {workflow.base_branch!r}; current or explicit start point differs."
            )
    if _COMMIT_OR_PUSH.search(command):
        if workflow.branch_pattern:
            branch = _git(project, "branch", "--show-current")
            if not branch or re.fullmatch(workflow.branch_pattern, branch) is None:
                return _deny(
                    f"Current branch {branch or '(detached)'} does not match {workflow.branch_pattern!r}."
                )
        if workflow.changelog_file and re.search(r"\bgit\s+commit\b", command, re.I):
            staged = set(_git(project, "diff", "--cached", "--name-only").splitlines())
            if workflow.changelog_file not in staged:
                return _deny(
                    f"Stage {workflow.changelog_file!r} before committing this project task."
                )
    return None


def _starts_from_base(command: str, project: Path, base: str) -> bool:
    tokens = command.replace("'", "").replace('"', "").split()
    if base in tokens:
        return True
    return _git(project, "branch", "--show-current") == base


def _git(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def prompt_context(text: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }


def emit(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"))
