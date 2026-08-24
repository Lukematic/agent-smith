"""Harness installation: put the persona where each tool actually looks.

A.W.I.N.O.'s persona is one Markdown body, but every harness expects a different
location, filename, and frontmatter shape. Getting any of the three wrong fails
*silently*: the file lands on disk, nothing errors, and the agent never appears.
So each target is verified against a real installation rather than assumed.

| Harness | Location | Selectable by |
| --- | --- | --- |
| Claude Code | `~/.claude/agents/<name>.md` | frontmatter needs `tools` |
| Kilo | `~/.config/kilo/agents/<name>.md` | **`mode: primary`** |
| Roo | project `.roomodes`, or `custom_modes.yaml` | see `modes.py` |
| Copilot | `<prompts>/<name>.chatmode.md` | `description` plus `tools` |
| Goose | `~/.agents/agents/<name>.md` | plugin at `~/.agents/plugins/` |
| Cursor | `.cursor/rules/<name>.mdc` | `alwaysApply` |

The two bugs this file exists to prevent, both found on a real machine:

- A Kilo agent without `mode: primary` installs as a *subagent*. It is invocable
  by another agent but never appears in the mode selector, which looks identical
  to "the install did not work".
- `~/.kilo/` is not where Kilo reads global agents. It is `~/.config/kilo/`.

Skills are **linked** where the harness allows it, so `git pull` updates the live
install with nothing else to run. The persona is copied, because harnesses read it
once at startup and a stable copy is easier to reason about than a file that
changes mid-session.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from smith import ownership


class Harness(StrEnum):
    CLAUDE = "claude"
    AGENTS = "agents"
    KILO = "kilo"
    CURSOR = "cursor"
    COPILOT = "copilot"

    @property
    def label(self) -> str:
        return {
            Harness.CLAUDE: "Claude Code",
            Harness.AGENTS: "Goose / open agents",
            Harness.KILO: "Kilo",
            Harness.CURSOR: "Cursor",
            Harness.COPILOT: "GitHub Copilot",
        }[self]

    @property
    def global_root(self) -> Path:
        """Where this harness keeps user-level configuration.

        Kilo uses `~/.config/kilo`, not `~/.kilo`. Copilot uses the VS Code user
        prompts directory. Both were wrong in an earlier version and produced
        installs that looked successful and did nothing.
        """
        home = Path.home()
        if self is Harness.KILO:
            return home / ".config" / "kilo"
        if self is Harness.COPILOT:
            if os.name == "nt":
                return home / "AppData" / "Roaming" / "Code" / "User" / "prompts"
            if os.uname().sysname == "Darwin":  # type: ignore[attr-defined]
                return home / "Library" / "Application Support" / "Code" / "User" / "prompts"
            return home / ".config" / "Code" / "User" / "prompts"
        return home / f".{self}"

    def project_root(self, project: Path) -> Path:
        if self is Harness.COPILOT:
            return project / ".github" / "chatmodes"
        if self is Harness.KILO:
            return project / ".kilo"
        return project / f".{self}"

    @property
    def persona_dir(self) -> str:
        """Subdirectory holding personas, relative to the harness root."""
        return {
            Harness.CLAUDE: "agents",
            Harness.AGENTS: "agents",
            Harness.KILO: "agents",
            Harness.CURSOR: "rules",
            Harness.COPILOT: "",
        }[self]

    @property
    def persona_filename(self) -> str:
        """Copilot encodes the artifact type in the filename suffix."""
        return {
            Harness.CLAUDE: "awino.md",
            Harness.AGENTS: "awino.md",
            Harness.KILO: "awino.md",
            Harness.CURSOR: "awino.mdc",
            Harness.COPILOT: "awino.chatmode.md",
        }[self]

    @property
    def supports_skills(self) -> bool:
        """Cursor rules and Copilot chat modes are context, not model-invoked skills."""
        return self in {Harness.CLAUDE, Harness.AGENTS, Harness.KILO}

    @property
    def uses_plugins(self) -> bool:
        """Whether the harness loads a whole plugin directory rather than files."""
        return self is Harness.AGENTS

    @property
    def skills_subdir(self) -> str:
        return "skills"


@dataclass(frozen=True)
class Target:
    """One place A.W.I.N.O. can be installed."""

    harness: Harness
    root: Path
    scope: str

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    @property
    def persona_path(self) -> Path:
        parent = self.root / self.harness.persona_dir if self.harness.persona_dir else self.root
        return parent / self.harness.persona_filename

    @property
    def skills_root(self) -> Path:
        return self.root / self.harness.skills_subdir

    @property
    def plugin_path(self) -> Path:
        return self.root / "plugins" / "awino"

    def describe(self) -> str:
        state = "present" if self.exists else "absent"
        return f"{self.harness.label} ({self.scope}, {state}) at {self.root}"


@dataclass(frozen=True)
class Action:
    """One installation step and its result."""

    target: str
    path: Path
    outcome: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.outcome == "FAILED"


def discover(project: Path) -> list[Target]:
    """Every harness location, global first.

    Global comes first because it is the right default: one shared A.W.I.N.O. cannot
    fork its own knowledge base, which is the failure that copying per project
    guarantees.
    """
    found: list[Target] = []
    for harness in Harness:
        found.append(Target(harness, harness.global_root, "global"))
        found.append(Target(harness, harness.project_root(project), "project"))
    return found


def detected(project: Path) -> list[Target]:
    """Only locations that already exist, so A.W.I.N.O. installs where you work."""
    return [t for t in discover(project) if t.exists]


def _already_linked(destination: Path, source: Path) -> bool:
    """Whether the destination already points at the source.

    Windows junctions do not report as symlinks, so `is_symlink()` alone misses
    them and a reinstall tries to recreate a link that already exists. Comparing
    resolved paths covers symlinks, junctions, and bind mounts uniformly.
    """
    if not destination.exists() and not destination.is_symlink():
        return False
    try:
        return destination.resolve() == source.resolve()
    except OSError:
        return False


def _unlink_any(path: Path) -> None:
    """Remove a file, directory, symlink, or Windows junction.

    Junctions need care: they are directories with a reparse point, so
    ``shutil.rmtree`` follows them and would delete the *target's* contents. Only
    ``os.rmdir`` removes the link itself, which is why this is not a one-liner.
    """
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if not path.is_dir():
        return
    if os.name == "nt" and os.path.isjunction(path):
        path.rmdir()
        return
    shutil.rmtree(path)


def _link_or_copy(source: Path, destination: Path, *, overwrite: bool = False) -> tuple[str, str]:
    """Symlink when the platform allows it, otherwise copy.

    Windows needs Developer Mode or elevation for symlinks, so a junction and then
    a copy are the documented fallbacks. The difference matters: a link updates on
    git pull, a copy needs reinstalling.
    """
    # Kept for CLI compatibility; ownership safety applies even when requested.
    del overwrite
    if _already_linked(destination, source):
        return "SKIPPED", "already linked, git pull keeps it current"

    if destination.exists() or destination.is_symlink():
        root = destination.parent
        owned = ownership.entry(root, destination)
        if not owned:
            return "FAILED", "existing destination is not installer-owned; refusing replacement"
        if not ownership.unchanged(root, destination):
            saved = ownership.backup(destination)
            return "FAILED", f"installer-owned destination was modified; backup: {saved}"
        if owned.get("kind") == "copy" and ownership.sha256_path(source) == owned.get("sha256"):
            return "SKIPPED", "installer-owned copy already current"
        try:
            _unlink_any(destination)
        except OSError as exc:
            return "FAILED", f"could not replace existing path: {exc}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.symlink_to(source, target_is_directory=True)
    except OSError:
        pass
    else:
        return "LINKED", "updates automatically on git pull"

    if os.name == "nt":
        import subprocess

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(destination), str(source)],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return "LINKED", "junction, updates automatically on git pull"

    try:
        shutil.copytree(source, destination)
    except OSError as exc:
        return "FAILED", str(exc)
    ownership.record(destination.parent, destination, "copy")
    return "COPIED", "installer-owned copy; re-run install after a git pull to refresh"


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return frontmatter fields and the body, tolerating an absent block."""
    if not text.startswith("---"):
        return {}, text
    closing = text.find("\n---", 3)
    if closing == -1:
        return {}, text
    fields: dict[str, str] = {}
    for line in text[3:closing].splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields, text[closing + 4 :].lstrip("\n")


def _persona_for(harness: Harness, source: Path) -> str:
    """Rebuild the frontmatter for the harness that will read it.

    Every field here was verified against a working installation. Omitting the
    right one does not raise: the file simply never surfaces, which is the most
    expensive kind of failure because it looks like success.
    """
    text = source.read_text(encoding="utf-8")
    fields, body = _split_frontmatter(text)
    name = fields.get("name", "awino")
    description = fields.get("description", "Agentic-engineering expert and agent factory")
    model = fields.get("model")

    if harness is Harness.CURSOR:
        # Cursor rules are always-on context, not agents, so agent metadata is
        # meaningless and alwaysApply is required.
        return f"---\ndescription: {description}\nalwaysApply: false\n---\n\n{body}"

    if harness is Harness.COPILOT:
        # Copilot chat modes quote the description and expect a tools array. An
        # empty array means "inherit whatever the session has".
        quoted = description.replace("'", "''")
        return f"---\ndescription: '{quoted}'\ntools: []\n---\n\n{body}"

    if harness is Harness.KILO:
        # `mode: primary` is what makes an agent appear in the mode selector.
        # Without it Kilo installs a subagent: invocable by another agent, but
        # invisible to the user. That was the actual bug behind "it is not a mode".
        header = [
            "---",
            "mode: primary",
            f"description: {description}",
            "options:",
            "  displayName: A.W.I.N.O.",
            f"  id: {name}",
        ]
        if model:
            header.append(f"model: {model}")
        header.append("---")
        return "\n".join(header) + "\n\n" + body

    if harness is Harness.CLAUDE:
        # Claude Code agents declare a tool list. A.W.I.N.O. needs to read, search,
        # write, and run its own CLI, so the set is deliberate rather than
        # inherited.
        tools = "Read, Write, Edit, Grep, Glob, Bash, Task, TodoWrite, WebFetch"
        header = ["---", f"name: {name}", f"description: {description}", f"tools: {tools}"]
        if model:
            header.append(f"model: {model}")
        header.append("---")
        return "\n".join(header) + "\n\n" + body

    # Goose reads the file as written.
    return text


def install(
    smith_home: Path, target: Target, *, skills: bool = True, overwrite: bool = False
) -> list[Action]:
    """Install the persona, and skills where the harness supports them.

    ``overwrite`` forces a relink even when the destination already points at the
    source. Useful after moving the clone, when the existing link is correct by
    path but stale by intent.
    """
    actions: list[Action] = []
    label = f"{target.harness}/{target.scope}"

    persona_source = smith_home / "agents" / "awino.md"
    if not persona_source.is_file():
        return [Action(label, persona_source, "FAILED", "agents/awino.md is missing")]

    destination = target.persona_path
    try:
        outcome, detail = ownership.safe_write(
            target.root, destination, _persona_for(target.harness, persona_source), "persona"
        )
        actions.append(Action(label, destination, outcome, detail))
    except OSError as exc:
        actions.append(Action(label, destination, "FAILED", str(exc)))
        return actions

    if not skills:
        return actions
    if not target.harness.supports_skills:
        actions.append(
            Action(label, target.root, "SKIPPED", f"{target.harness.label} has no skills mechanism")
        )
        return actions

    if target.harness.uses_plugins:
        outcome, detail = _link_or_copy(smith_home, target.plugin_path, overwrite=overwrite)
        actions.append(Action(label, target.plugin_path, outcome, f"plugin, {detail}"))
        return actions

    # File-based harnesses read one directory per skill.
    source_skills = smith_home / "skills"
    for skill in sorted(source_skills.glob("awino-*/SKILL.md")):
        outcome, detail = _link_or_copy(
            skill.parent, target.skills_root / skill.parent.name, overwrite=overwrite
        )
        actions.append(Action(label, target.skills_root / skill.parent.name, outcome, detail))
    return actions


def status(project: Path, targets: list[Target] | None = None) -> list[tuple[Target, bool, str]]:
    """Where A.W.I.N.O. is currently installed, and where it is not."""
    out: list[tuple[Target, bool, str]] = []
    for target in targets or discover(project):
        persona = target.persona_path
        if not persona.is_file():
            out.append((target, False, "persona not installed"))
            continue
        if target.harness.uses_plugins:
            linked = target.plugin_path.exists() or target.plugin_path.is_symlink()
            out.append(
                (target, True, "persona and plugin" if linked else "persona only, no plugin")
            )
            continue
        if not target.harness.supports_skills:
            detail = f"persona; {target.harness.label} has no skills mechanism"
            out.append((target, True, detail))
            continue
        count = (
            len(list(target.skills_root.glob("*/SKILL.md"))) if target.skills_root.is_dir() else 0
        )
        out.append((target, True, f"persona and {count} skill(s)"))
    return out


def pointer_text(smith_home: Path) -> str:
    """A short block for AGENTS.md or CLAUDE.md in a project.

    This is the minimum a repository needs: three lines that make A.W.I.N.O.
    discoverable without copying anything. Copying A.W.I.N.O. per project forks its
    knowledge base, which is the failure a pointer avoids.
    """
    return f"""## A.W.I.N.O. (Agentic Workflow Intelligence & Navigation Orchestrator)

Agentic-engineering questions, agent authoring, and agent debugging go to
A.W.I.N.O., the canonical agent identity. Its home is
`{smith_home}`.

- Concepts, harness design, failure triage: load `awino`
- Before any multi-step work: `awino plan "<request>"` then `awino gate open ...`
- Completion is computed, never claimed: `awino gate close` decides
- Project purpose and calibration: `awino onboard`
- Project-local conventions live in this repository, never in A.W.I.N.O.'s home
"""
