"""Custom mode installation for Kilo, Roo, and their forks.

A *mode* is not the same thing as a persona file. Kilo and Roo modes appear in the
mode selector, carry a `roleDefinition` that replaces the system prompt, and
declare `groups` that restrict which tools the mode may use. That last part is the
reason modes matter here: a mode is tool restriction enforced by the harness, which
is the forcing function Smith's own doctrine argues for.

Two placements, mirroring the persona installer:

- **Global**: `<globalStorage>/<extension-id>/settings/custom_modes.yaml`, available
  in every workspace.
- **Project**: `.kilocodemodes` or `.roomodes` at the repository root, committed so
  a team shares it.

The schema is validated by the extension, so this module writes exactly what
`modeConfigSchema` accepts and nothing more. An invalid entry makes the extension
drop the whole file, which fails silently: no error, the mode simply never appears.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")
VALID_GROUPS = frozenset({"read", "edit", "command", "mcp", "modes"})

# Extension identities. Kilo is a Roo fork, so the schema is shared and only the
# storage directory and project filename differ.
EDITORS: dict[str, tuple[str, str, str]] = {
    # key: (label, vscode extension id, project-level filename)
    "kilo": ("Kilo Code", "kilocode.kilo-code", ".kilocodemodes"),
    "roo": ("Roo Code", "rooveterinaryinc.roo-cline", ".roomodes"),
    "zoo": ("Zoo Code", "zoocodeorganization.zoo-code", ".zoocodemodes"),
}

# VS Code variants keep globalStorage in different roots.
VSCODE_ROOTS = (
    "AppData/Roaming/Code/User/globalStorage",
    "AppData/Roaming/Code - Insiders/User/globalStorage",
    "AppData/Roaming/VSCodium/User/globalStorage",
    "Library/Application Support/Code/User/globalStorage",
    ".config/Code/User/globalStorage",
)


@dataclass(frozen=True)
class Mode:
    """One custom mode, shaped to the extension's schema."""

    slug: str
    name: str
    role_definition: str
    when_to_use: str = ""
    description: str = ""
    custom_instructions: str = ""
    groups: list = field(default_factory=lambda: ["read", "edit", "command", "mcp"])

    def validate(self) -> list[str]:
        """Return schema problems. An invalid mode is dropped silently by the
        extension, so catching it here is the difference between a clear error and
        a mode that mysteriously never appears."""
        problems: list[str] = []
        if not SLUG_PATTERN.match(self.slug):
            problems.append(f"slug {self.slug!r} must be letters, numbers, and dashes only")
        if not self.name:
            problems.append("name is required")
        if not self.role_definition:
            problems.append("roleDefinition is required")
        seen: set[str] = set()
        for group in self.groups:
            key = group[0] if isinstance(group, list | tuple) else group
            if key not in VALID_GROUPS:
                problems.append(f"unknown tool group {key!r}")
            if key in seen:
                problems.append(f"duplicate tool group {key!r}")
            seen.add(key)
        return problems

    def to_dict(self) -> dict:
        """Serialize using the extension's camelCase keys, omitting empties.

        Writing a key with an empty value is not the same as omitting it: the
        schema treats present-but-empty as a value and will fail `min(1)` checks.
        """
        payload: dict = {
            "slug": self.slug,
            "name": self.name,
            "roleDefinition": self.role_definition,
            "groups": list(self.groups),
        }
        if self.when_to_use:
            payload["whenToUse"] = self.when_to_use
        if self.description:
            payload["description"] = self.description
        if self.custom_instructions:
            payload["customInstructions"] = self.custom_instructions
        return payload


@dataclass(frozen=True)
class ModeTarget:
    """One place a mode can be written."""

    editor: str
    label: str
    path: Path
    scope: str

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def parent_exists(self) -> bool:
        """Whether the editor is installed, even if it has no modes file yet.

        For a global target the meaningful question is whether the extension exists,
        which is its directory two levels up from `settings/custom_modes.yaml`.
        """
        if self.scope == "global":
            return self.path.parent.parent.is_dir()
        return self.path.parent.is_dir()

    def describe(self) -> str:
        if self.exists:
            state = "file present"
        elif self.parent_exists:
            state = "editor present, no modes file yet"
        else:
            state = "not installed"
        return f"{self.label} ({self.scope}, {state}) at {self.path}"


def discover(project: Path) -> list[ModeTarget]:
    """Every mode file location, for every known editor.

    Detection keys off the *extension* directory rather than the settings file. A
    freshly installed editor has no `settings/custom_modes.yaml` until the user
    creates their first mode, so requiring the file would miss exactly the case
    where installing a mode is most useful.
    """
    home = Path.home()
    found: list[ModeTarget] = []

    for key, (label, extension_id, project_file) in EDITORS.items():
        for root in VSCODE_ROOTS:
            extension_dir = home / root / extension_id
            if not extension_dir.is_dir():
                continue
            found.append(
                ModeTarget(key, label, extension_dir / "settings" / "custom_modes.yaml", "global")
            )
            break
        found.append(ModeTarget(key, label, project / project_file, "project"))
    return found


def detected(project: Path) -> list[ModeTarget]:
    """Only locations whose editor is actually installed."""
    return [t for t in discover(project) if t.exists or t.parent_exists]


def _load(path: Path) -> list[dict]:
    """Read existing modes, tolerating an absent or empty file."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        # A corrupt file must not be silently overwritten: that would destroy a
        # user's own modes. Signal by raising rather than returning empty.
        raise ValueError(f"{path} is not valid YAML; fix or move it before installing") from None
    if isinstance(data, dict):
        return list(data.get("customModes") or [])
    if isinstance(data, list):
        # Project .roomodes files are sometimes a bare list.
        return list(data)
    return []


def _dump(path: Path, modes: list[dict], *, bare_list: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = modes if bare_list else {"customModes": modes}
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def install(mode: Mode, target: ModeTarget, *, force: bool = False) -> tuple[str, str]:
    """Add or update one mode in one target, preserving every other mode.

    Existing modes are read, the matching slug is replaced, and the rest are
    written back untouched. Rewriting the file wholesale would delete a user's own
    modes, which is unacceptable for a tool that installs itself.
    """
    problems = mode.validate()
    if problems:
        return "FAILED", "; ".join(problems)

    try:
        existing = _load(target.path)
    except ValueError as exc:
        return "FAILED", str(exc)

    index = next((i for i, m in enumerate(existing) if m.get("slug") == mode.slug), None)
    if index is not None and not force:
        return "SKIPPED", f"{mode.slug} already present, use --force to overwrite"

    payload = mode.to_dict()
    if index is None:
        existing.append(payload)
        outcome = "INSTALLED"
    else:
        existing[index] = payload
        outcome = "UPDATED"

    try:
        _dump(target.path, existing)
    except OSError as exc:
        return "FAILED", str(exc)
    return outcome, f"{len(existing)} mode(s) in file"


def status(project: Path, slug: str) -> list[tuple[ModeTarget, bool]]:
    """Whether a given mode slug is present in each target."""
    out: list[tuple[ModeTarget, bool]] = []
    for target in discover(project):
        try:
            present = any(m.get("slug") == slug for m in _load(target.path))
        except ValueError:
            present = False
        out.append((target, present))
    return out


# ── the Smith modes ──────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def build_modes(smith_home: Path) -> list[Mode]:
    """Modes split first by capability boundary, then by specialist purpose.

    The split is the point. `agent-smith-ask`, `agent-smith-discover`, and
    `agent-smith-research` cannot edit. `agent-smith-plan` can write only Markdown
    and has no command group. That is capability minimization enforced by the
    editor rather than requested in prose.

    ``roleDefinition`` stays short on purpose. It is resident in context on every
    turn, so embedding the whole persona would duplicate the constitution and pay
    for it repeatedly, which is the CONTEXT_BLOAT this tool exists to prevent. The
    mode points at the files instead: progressive disclosure applied to Smith's own
    installation.
    """
    home = smith_home.as_posix()

    role = (
        "You are A.W.I.N.O., an Agentic Workflow Intelligence & Navigation "
        "Orchestrator (working name history: Agent Smith).\n\n"
        "Your authority comes from a living knowledge source you consult on demand, "
        "never from memory of it. Your discipline comes from a gate ledger, never from "
        "good intentions.\n\n"
        f"Your home is `{home}`. Read these before acting, in order:\n\n"
        f"1. `{home}/AGENT_SMITH.md` is the constitution. It overrides your defaults.\n"
        f"2. `{home}/memory/lessons.md` holds binding rules earned from real failures.\n"
        f"3. `{home}/knowledge/REGISTRY.yaml` is the knowledge index. Index only, never "
        "chapter bodies.\n\n"
        "Four rules you never break:\n\n"
        "- **Harness over prompt.** A repeated mistake gets a structural fix, not a "
        "warning. Label any wording-only fix `PROMPT-PATCH (debt)` with the structural "
        "fix it defers.\n"
        '- **No unnamed failures.** "The agent is bad" is not a diagnosis. Name the '
        "failure mode and the surface: prompt, model, context, or tools.\n"
        "- **Cite or mark inferred.** Every knowledge claim carries a chapter path. "
        "Everything else is `[inferred]`. A fabricated path is UNGROUNDED_CLAIM.\n"
        "- **Completion is computed.** `smith gate close` decides whether work is done. "
        "You never assert it.\n\n"
        "Open every reply with `[Smith | mode: <mode> | run: <id|none> | budget: <n>/3]`."
    )

    shared = (
        "Run `smith context` first in an unfamiliar repository, then `smith mission` to "
        "learn what the project is for. Both are cheap and prevent generic advice.\n\n"
        "Maximum three knowledge files per task. A fourth means the task is "
        "under-decomposed: say so and split it.\n\n"
        "Prefer the CLI over reasoning through anything deterministic. That is the "
        "MODEL_DOES_DETERMINISM guard.\n"
    )

    return [
        Mode(
            slug="agent-smith",
            name="🧭 A.W.I.N.O.",
            role_definition=role,
            when_to_use=(
                "Use for agentic-engineering work: harness design, prompt and context "
                "questions, tool restriction, multi-agent patterns, debugging an agent that "
                "misbehaves, or authoring a new agent, skill, or tool. Also use when work "
                "must be gated so completion is proven rather than claimed."
            ),
            description="Agentic-engineering expert with a gate ledger",
            custom_instructions=(
                shared + "\n"
                'Before multi-step work run `smith plan "<request>"`. It reports which '
                "leverage rung the request actually sits on, where the binding constraint is, "
                "how much autonomy the current verification supports, and whether fanning out "
                "to parallel agents is justified. Respect the autonomy verdict: `supervised` "
                "means one step then report, `unattended` means a trigger may drive it.\n\n"
                "Open a gated run before changing anything:\n\n"
                "```bash\n"
                'smith gate open <class> "<objective>" --scope <paths>\n'
                'smith gate record tested --cmd "<real test command>"\n'
                "smith gate check --diff-base HEAD\n"
                "smith gate close\n"
                "```\n\n"
                "The task class fixes the gates; you do not choose them. You may not report "
                "work as complete until `smith gate close` exits zero. If one gate fails three "
                "times, stop and escalate rather than retrying.\n\n"
                f"Skills live in `{home}/skills/`. Load one at a time and record it with "
                "`smith gate skill <name>` so usage is auditable."
            ),
            groups=["read", "edit", "command", "mcp"],
        ),
        Mode(
            slug="agent-smith-ask",
            name="🧭 A.W.I.N.O. Consult",
            role_definition=(
                role + "\n\nIn this mode you answer and diagnose. You cannot edit files, which "
                "is deliberate: a consult that quietly changes code is no longer a consult."
            ),
            when_to_use=(
                "Use for conceptual questions such as what a harness is, how context should be "
                "managed, which pattern fits, or why an agent keeps making the same mistake. "
                "Read-only, so safe on any repository."
            ),
            description="Read-only agentic-engineering consult",
            custom_instructions=(
                shared + "\n"
                'Route with `smith route "<question>"` first: it maps the question to chapters '
                "without spending any budget. Then fetch at most three.\n\n"
                "For a misbehaving agent, name the failure mode and the surface, then give two "
                "columns: the prompt patch to reject, and the structural fix to make. A fix is "
                "incomplete until you name what makes the mistake unrepeatable, such as a hook, "
                "a removed capability, a validator, or a test."
            ),
            groups=["read", "mcp"],
        ),
        Mode(
            slug="agent-smith-plan",
            name="🧭 A.W.I.N.O. Plan",
            role_definition=(
                role + "\n\nIn this mode you research and plan. You may write Markdown only, so "
                "a planning session cannot silently become an implementation session."
            ),
            when_to_use=(
                "Use before a complex multi-file change, refactor, or migration. Produces a "
                "research document and a phased plan with a real verification command per "
                "phase. Restricted to Markdown, so it cannot start implementing."
            ),
            description="Research and plan, Markdown only",
            custom_instructions=(
                shared + "\n"
                "Research first, and document only what exists: no opinions, no proposed fixes. "
                "An opinion recorded as fact poisons the plan built on it.\n\n"
                "Write research to `thoughts/research/YYYY-MM-DD-HHmm-<topic>.md` and plans to "
                "`thoughts/plans/`. Every phase carries a command that must pass, and the plan "
                "must be explicit enough for a fresh session with no memory of this one to "
                "execute it.\n\n"
                "Stop after the plan and hand off. Do not implement."
            ),
            # No command group: Markdown-only editor permissions are not a real
            # planning boundary if an unrestricted shell can mutate anything.
            groups=[
                "read",
                ["edit", {"fileRegex": r"\.(md|markdown)$", "description": "Markdown only"}],
                "mcp",
            ],
        ),
        Mode(
            slug="agent-smith-discover",
            name="🧭 A.W.I.N.O. Discover",
            role_definition=(
                role + "\n\nIn this mode you are a mission and requirements partner. You listen, "
                "reflect decisions back, ask one unresolved frontier question at a time, "
                "and never implement."
            ),
            when_to_use=(
                "Use when a user has a raw idea, a sparse repository, or no confirmed mission. "
                "Captures the primary user, goals, tenets, expectations, non-goals, and success "
                "metric before architecture or implementation."
            ),
            description="Mission and requirements discovery without implementation",
            custom_instructions=(
                shared + "\nLoad `smith-discover`. Run `smith onboard`, reflect the mission draft "
                "and its evidence, then ask exactly one unresolved question. Persist answers "
                "with `smith onboard --set key=value`. Do not produce a spec until "
                "`.smith/project.yaml` is confirmed. If a question needs something to react "
                "to, propose the smallest throwaway prototype instead of continuing to ask."
            ),
            groups=["read", "mcp"],
        ),
        Mode(
            slug="agent-smith-research",
            name="🧭 A.W.I.N.O. Research",
            role_definition=(
                role + "\n\nIn this mode you design and audit source-grounded research workflows. "
                "You do not release synthesis whose evidence gate has not passed."
            ),
            when_to_use=(
                "Use for scientific research assistants, RAG, literature review, evidence "
                "synthesis, citation audits, or reproducible data and agent pipelines."
            ),
            description="Evidence-grounded research and reproducibility specialist",
            custom_instructions=(
                shared + "\nLoad `smith-evidence` before factual synthesis and "
                "`smith-reproducibility` before pipeline design. Every released factual claim "
                "maps to inspectable source IDs. Label abstract-only evidence. Capture run IDs, "
                "input and config snapshots, prompt/model versions, retries, and errors. The "
                "domain expert approves scientific interpretation; you provide the structure, "
                "provenance, and gates."
            ),
            groups=["read", "mcp"],
        ),
    ]


def as_json(smith_home: Path) -> str:
    """Emit the modes as JSON, for a tool that wants to import them directly."""
    return json.dumps([m.to_dict() for m in build_modes(smith_home)], indent=2)
