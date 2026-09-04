"""The ``smith.cli`` package keeps its shape.

Three properties that a split can silently lose: each command module declares
what it owns, library modules never reach back into the CLI, and the set of
registered commands is exactly the set that existed before the split. The
command names below were dumped from the pre-split monolithic ``cli.py``, so a
command that vanishes or gets renamed in a refactor fails here rather than in a
user's shell.
"""

from __future__ import annotations

import ast
from pathlib import Path

import typer

from smith import cli

SRC = Path(__file__).parents[1] / "src" / "smith"
CLI_PACKAGE = SRC / "cli"

# Every registered command, with sub-app prefixes, as it stood in the 4141-line
# cli.py before the package split. 88 names.
PRE_SPLIT_COMMANDS = frozenset(
    {
        "ask",
        "auto",
        "best",
        "clean",
        "config-review",
        "context",
        "debug attempt",
        "debug authorize-fix",
        "debug begin",
        "debug evidence",
        "debug hypothesize",
        "debug verify",
        "delegate",
        "dispatch",
        "doctor",
        "drift",
        "exam",
        "env",
        "fetch",
        "fix",
        "floor close",
        "floor open",
        "gate block",
        "gate check",
        "gate checkpoint",
        "gate close",
        "gate contracts",
        "gate decide",
        "gate graph",
        "gate loop",
        "gate open",
        "gate pause",
        "gate plan approve",
        "gate plan hold",
        "gate plan reject",
        "gate plan status",
        "gate record",
        "gate record-completeness",
        "gate review",
        "gate score",
        "gate skill",
        "gate status",
        "heal",
        "hook",
        "install",
        "install-mode",
        "install-refresh",
        "install-status",
        "knowledge-update",
        "ladder",
        "limits",
        "link",
        "mission",
        "mode-status",
        "note",
        "onboard",
        "pit",
        "plan",
        "pointer",
        "project-bootstrap",
        "project-scaffold",
        "push",
        "registry-json",
        "remember",
        "resume",
        "review-doc",
        "rollback",
        "route",
        "scaffold",
        "session-log",
        "setup",
        "skills",
        "skills-status",
        "stance",
        "start",
        "status",
        "tidy",
        "update",
        "update-preflight",
        "validate",
        "watch",
        "watch-add",
        "watch-list",
        "watch-remove",
        "work",
        "work-close",
        "work-init",
        "workflow",
    }
)


def _registered(app: typer.Typer, prefix: str = "") -> set[str]:
    names: set[str] = set()
    for command in app.registered_commands:
        assert command.callback is not None
        name = command.name or command.callback.__name__.replace("_", "-")
        names.add(prefix + name)
    for group in app.registered_groups:
        assert group.name is not None
        names |= _registered(group.typer_instance, f"{prefix}{group.name} ")
    return names


def _command_modules() -> list[Path]:
    return sorted(
        path for path in CLI_PACKAGE.glob("*.py") if path.name not in {"__init__.py", "__main__.py"}
    )


def _imports_cli(module: Path) -> bool:
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "smith.cli" or alias.name.startswith("smith.cli.")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and (
                node.module == "smith.cli" or node.module.startswith("smith.cli.")
            ):
                return True
            if node.module == "smith" and any(alias.name == "cli" for alias in node.names):
                return True
    return False


class TestCommandModulesDeclareOwnership:
    def test_there_are_command_modules(self) -> None:
        assert _command_modules(), "cli/ has no command modules"

    def test_every_command_module_starts_with_an_owns_line(self) -> None:
        missing: list[str] = []
        for module in _command_modules():
            doc = ast.get_docstring(ast.parse(module.read_text(encoding="utf-8")))
            first = (doc or "").splitlines()[0] if doc else ""
            if not first.startswith("owns: "):
                missing.append(f"{module.name}: {first!r}")
        assert not missing, "modules without an 'owns:' first docstring line:\n" + "\n".join(
            missing
        )

    def test_every_owned_name_is_a_real_command(self) -> None:
        registered = _registered(cli.app)
        bogus: list[str] = []
        for module in _command_modules():
            doc = ast.get_docstring(ast.parse(module.read_text(encoding="utf-8"))) or ""
            owned = [n.strip() for n in doc.splitlines()[0].removeprefix("owns: ").split(",")]
            bogus.extend(
                f"{module.name} claims {name!r}" for name in owned if name not in registered
            )
        assert not bogus, "\n".join(bogus)


class TestLibraryNeverImportsTheCli:
    def test_no_module_outside_cli_imports_smith_cli(self) -> None:
        offenders = [
            module.relative_to(SRC).as_posix()
            for module in SRC.rglob("*.py")
            if CLI_PACKAGE not in module.parents
            and "__pycache__" not in module.parts
            and _imports_cli(module)
        ]
        assert not offenders, f"library modules importing smith.cli: {offenders}"


class TestCommandSurfaceIsUnchanged:
    def test_registered_commands_match_the_pre_split_dump(self) -> None:
        registered = _registered(cli.app)
        assert registered == PRE_SPLIT_COMMANDS, (
            f"missing={sorted(PRE_SPLIT_COMMANDS - registered)} "
            f"unexpected={sorted(registered - PRE_SPLIT_COMMANDS)}"
        )

    def test_eighty_six_commands(self) -> None:
        assert len(PRE_SPLIT_COMMANDS) == 88
        assert len(_registered(cli.app)) == 88

    def test_public_entry_points_survive(self) -> None:
        assert isinstance(cli.app, typer.Typer)
        assert callable(cli.deprecated_smith_entry)
        assert (CLI_PACKAGE / "__main__.py").is_file()
