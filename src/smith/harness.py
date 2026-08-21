"""Harness installation: put the persona and skills where each tool looks.

Smith's persona is one Markdown file, but every harness expects it somewhere
different, under a different filename, sometimes with different frontmatter. So
installation is detection plus adaptation rather than one hardcoded path.

| Harness | Persona | Skills |
| --- | --- | --- |
| Claude Code | `.claude/agents/<name>.md` | `.claude/skills/<name>/SKILL.md` |
| Goose / open agents | `.agents/agents/<name>.md` | `.agents/plugins/<name>/` |
| Kilo | `.kilo/agent/<name>.md` | `.kilo/skills/<name>/SKILL.md` |
| Cursor | `.cursor/rules/<name>.mdc` | not supported |
| Generic | `AGENTS.md` pointer | referenced by path |

Two placements, each with a different lifetime:

- **Global** (`~/.claude/`, `~/.agents/`) makes Smith available in every project.
  Correct default: one shared Smith avoids forking its knowledge base.
- **Project** (`./.claude/`, `./.kilo/`) pins Smith to one repository, for a team
  that must share an exact version.

Skills are **symlinked** where the harness allows it, so a `git pull` updates the
live install with nothing else to run. The persona is copied, because harnesses
read it once at startup and a stale copy is easier to reason about than a symlink
that silently changes mid-session.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Harness(StrEnum):
    CLAUDE = "claude"
    AGENTS = "agents"
    KILO = "kilo"
    CURSOR = "cursor"

    @property
    def label(self) -> str:
        return {
            Harness.CLAUDE: "Claude Code",
            Harness.AGENTS: "Goose / open agents",
            Harness.KILO: "Kilo",
            Harness.CURSOR: "Cursor",
        }[self]

    @property
    def root_name(self) -> str:
        return f".{self}"

    @property
    def persona_dir(self) -> str:
        """Subdirectory holding agent personas. Kilo uses the singular form."""
        return {
            Harness.CLAUDE: "agents",
            Harness.AGENTS: "agents",
            Harness.KILO: "agent",
            Harness.CURSOR: "rules",
        }[self]

    @property
    def persona_suffix(self) -> str:
        return ".mdc" if self is Harness.CURSOR else ".md"

    @property
    def supports_skills(self) -> bool:
        """Cursor rules are always-on context, not model-invoked skills."""
        return self is not Harness.CURSOR

    @property
    def skills_dir(self) -> str:
        return "skills"

    @property
    def uses_plugins(self) -> bool:
        """Whether the harness loads a whole plugin directory rather than files."""
        return self is Harness.AGENTS


@dataclass(frozen=True)
class Target:
    """One place Smith can be installed."""

    harness: Harness
    root: Path
    scope: str

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    @property
    def persona_path(self) -> Path:
        return self.root / self.harness.persona_dir / f"agent-smith{self.harness.persona_suffix}"

    @property
    def skills_root(self) -> Path:
        return self.root / self.harness.skills_dir

    @property
    def plugin_path(self) -> Path:
        return self.root / "plugins" / "agent-smith"

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

    Global comes first because it is the right default: one shared Smith cannot
    fork its own knowledge base, which is the failure that copying per project
    guarantees.
    """
    home = Path.home()
    found: list[Target] = []
    for harness in Harness:
        found.append(Target(harness, home / harness.root_name, "global"))
        found.append(Target(harness, project / harness.root_name, "project"))
    return found


def detected(project: Path) -> list[Target]:
    """Only locations that already exist, so Smith installs where you work."""
    return [t for t in discover(project) if t.exists]


def _link_or_copy(source: Path, destination: Path) -> tuple[str, str]:
    """Symlink when the platform allows it, otherwise copy.

    Windows needs Developer Mode or elevation for symlinks, so a copy is the
    documented fallback. The difference matters: a symlink updates on git pull, a
    copy needs reinstalling.
    """
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink():
            return "SKIPPED", "already linked"
        shutil.rmtree(destination, ignore_errors=True)

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.symlink_to(source, target_is_directory=True)
    except OSError:
        pass
    else:
        return "LINKED", "updates automatically on git pull"

    try:
        if os.name == "nt":
            import subprocess

            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(destination), str(source)],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                return "LINKED", "junction, updates automatically on git pull"
        shutil.copytree(source, destination)
    except OSError as exc:
        return "FAILED", str(exc)
    return "COPIED", "re-run install after a git pull to refresh"


def _persona_for(harness: Harness, source: Path) -> str:
    """Adapt persona frontmatter to the harness that will read it.

    Cursor rules need `.mdc` frontmatter with an `alwaysApply` flag rather than
    agent metadata, so the body is preserved and the header replaced.
    """
    text = source.read_text(encoding="utf-8")
    if harness is not Harness.CURSOR:
        return text

    body = text
    if text.startswith("---"):
        closing = text.find("\n---", 3)
        if closing != -1:
            body = text[closing + 4 :].lstrip("\n")
    header = (
        "---\n"
        "description: Agentic-engineering expert and agent factory\n"
        "alwaysApply: false\n"
        "---\n\n"
    )
    return header + body


def install(smith_home: Path, target: Target, *, skills: bool = True) -> list[Action]:
    """Install the persona, and skills where the harness supports them."""
    actions: list[Action] = []
    label = f"{target.harness}/{target.scope}"

    persona_source = smith_home / "agents" / "agent-smith.md"
    if not persona_source.is_file():
        return [Action(label, persona_source, "FAILED", "agents/agent-smith.md is missing")]

    destination = target.persona_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.write_text(_persona_for(target.harness, persona_source), encoding="utf-8")
        actions.append(Action(label, destination, "INSTALLED", "persona"))
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
        outcome, detail = _link_or_copy(smith_home, target.plugin_path)
        actions.append(Action(label, target.plugin_path, outcome, f"plugin, {detail}"))
        return actions

    # File-based harnesses read one directory per skill.
    source_skills = smith_home / "skills"
    for skill in sorted(source_skills.glob("*/SKILL.md")):
        outcome, detail = _link_or_copy(skill.parent, target.skills_root / skill.parent.name)
        actions.append(Action(label, target.skills_root / skill.parent.name, outcome, detail))
    return actions


def status(project: Path) -> list[tuple[Target, bool, str]]:
    """Where Smith is currently installed, and where it is not."""
    out: list[tuple[Target, bool, str]] = []
    for target in discover(project):
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
        count = (
            len(list(target.skills_root.glob("*/SKILL.md"))) if target.skills_root.is_dir() else 0
        )
        out.append((target, True, f"persona and {count} skill(s)"))
    return out


def pointer_text(smith_home: Path) -> str:
    """A short block for AGENTS.md or CLAUDE.md in a project.

    This is the minimum a repository needs: three lines that make Smith
    discoverable without copying anything. Copying Smith per project forks its
    knowledge base, which is the failure a pointer avoids.
    """
    return f"""## Agent Smith

Agentic-engineering questions, agent authoring, and agent debugging go to Agent
Smith. Its home is `{smith_home}`.

- Concepts, harness design, failure triage: load `agent-smith`
- Before any multi-step work: `smith plan "<request>"` then `smith gate open ...`
- Completion is computed, never claimed: `smith gate close` decides
- Project purpose and calibration: `smith mission`
- Project-local conventions live in this repository, never in Smith's home
"""

