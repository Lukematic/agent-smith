"""One capability manifest: what this A.W.I.N.O. artifact provides and requires.

The manifest is generated from the tree (never hand-written) and shipped inside
the wheel at ``awino/capabilities.json``. ``verify_manifest`` rebuilds the
expected manifest from the tree under test and reports every difference, so a
stale or inaccurate manifest is detected instead of trusted.
"""

from __future__ import annotations

import importlib.resources
import json
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

MANIFEST_FILENAME = "capabilities.json"

#: Repo-relative data the wheel must carry for A.W.I.N.O. to work at all.
#: Mirrors the force-include mapping in pyproject.toml.
BUNDLE_ENTRIES = (
    "AWINO.md",
    "AGENT_SMITH.md",
    "plugin.json",
    ".claude-plugin",
    "settings.json",
    "bin",
    "knowledge/REGISTRY.yaml",
    "knowledge/SOURCES.yaml",
    "skills",
    "agents",
    "hooks",
    "templates",
    "memory/lessons.md",
)


@dataclass(frozen=True)
class CapabilityManifest:
    name: str
    version: str
    python_requires: str
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    entry_points: tuple[str, ...] = ()
    bundle_entries: tuple[str, ...] = field(default_factory=lambda: BUNDLE_ENTRIES)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CapabilityManifest:
        return cls(
            name=data["name"],
            version=data["version"],
            python_requires=data["python_requires"],
            provides=tuple(data.get("provides", ())),
            requires=tuple(data.get("requires", ())),
            entry_points=tuple(data.get("entry_points", ())),
            bundle_entries=tuple(data.get("bundle_entries", BUNDLE_ENTRIES)),
        )


def _pyproject(root: Path) -> dict:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def _skills(root: Path) -> list[str]:
    skills = root / "skills"
    if not skills.is_dir():
        return []
    return sorted(
        f"skill:{p.name}" for p in skills.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    )


def _hooks(root: Path) -> list[str]:
    path = root / "hooks" / "hooks.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    hooks = data.get("hooks", {})
    return sorted(f"hook:{name}" for name in hooks) if isinstance(hooks, dict) else []


def _templates(root: Path) -> list[str]:
    templates = root / "templates"
    if not templates.is_dir():
        return []
    return sorted(
        f"template:{p.name}" for p in templates.iterdir() if p.suffix in {".yaml", ".yml", ".md"}
    )


def build_manifest(root: Path, commands: Sequence[str]) -> CapabilityManifest:
    """Derive the manifest from the tree at ``root``. The tree is the source of truth.

    ``commands`` is the registered ``awino`` command surface (with sub-app
    prefixes, e.g. ``"release verify"``). It is passed in rather than
    introspected here: library modules must never import ``awino.cli``
    (see tests/test_cli_layout.py), so the CLI layer supplies it.
    """
    project = _pyproject(root).get("project", {})
    provides = [f"cmd:{name}" for name in commands]
    provides += _skills(root)
    provides += _hooks(root)
    provides += _templates(root)
    scripts = project.get("scripts", {})
    return CapabilityManifest(
        name=project.get("name", "awino"),
        version=project.get("version", "0+unknown"),
        python_requires=project.get("requires-python", ""),
        provides=tuple(sorted(provides)),
        requires=tuple(sorted(project.get("dependencies", []))),
        entry_points=tuple(sorted(f"{name} = {target}" for name, target in scripts.items())),
    )


def write_manifest(root: Path, path: Path | None = None, *, commands: Sequence[str]) -> Path:
    """Write the generated manifest. Defaults to the in-package location that ships in the wheel."""
    target = path or (root / "src" / "awino" / MANIFEST_FILENAME)
    target.write_text(
        json.dumps(build_manifest(root, commands).to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def load_manifest(root: Path) -> CapabilityManifest | None:
    """Load the manifest recorded for the tree at ``root``. None when absent."""
    path = root / "src" / "awino" / MANIFEST_FILENAME
    if not path.is_file():
        return None
    try:
        return CapabilityManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def load_packaged_manifest() -> CapabilityManifest | None:
    """Load the manifest shipped inside the installed package (wheel or source)."""
    try:
        text = (
            importlib.resources.files("awino")
            .joinpath(MANIFEST_FILENAME)
            .read_text(encoding="utf-8")
        )
    except (OSError, FileNotFoundError):
        return None
    try:
        return CapabilityManifest.from_dict(json.loads(text))
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def verify_manifest(root: Path, commands: Sequence[str]) -> list[str]:
    """Compare the recorded manifest against the tree. Empty means accurate.

    Every difference is a specific, actionable problem: a capability the
    manifest claims but the tree lacks, a capability the tree has but the
    manifest omits, a version mismatch, or a missing bundle entry.
    """
    problems: list[str] = []
    recorded = load_manifest(root)
    if recorded is None:
        return [f"no recorded manifest at src/awino/{MANIFEST_FILENAME}"]
    expected = build_manifest(root, commands)
    if recorded.version != expected.version:
        problems.append(
            f"manifest version {recorded.version!r} != tree version {expected.version!r}"
        )
    recorded_set, expected_set = set(recorded.provides), set(expected.provides)
    for missing in sorted(recorded_set - expected_set):
        problems.append(f"manifest claims {missing} but the tree does not provide it")
    for unlisted in sorted(expected_set - recorded_set):
        problems.append(f"tree provides {unlisted} but the manifest omits it")
    for entry in expected.bundle_entries:
        if not (root / entry).exists():
            problems.append(f"bundle entry missing from tree: {entry}")
    return problems


def verify_wheel(wheel_path: Path) -> list[str]:
    """Verify a built wheel carries an accurate manifest and the bundle.

    wheel_path: path to a ``.whl`` file. Problems are specific strings.
    """
    import zipfile

    problems: list[str] = []
    try:
        archive = zipfile.ZipFile(wheel_path)
    except (OSError, zipfile.BadZipFile):
        return [f"not a readable wheel: {wheel_path}"]
    names = set(archive.namelist())
    with archive:
        manifest_name = next((n for n in names if n == f"awino/{MANIFEST_FILENAME}"), None)
        if manifest_name is None:
            problems.append(f"wheel is missing awino/{MANIFEST_FILENAME}")
        else:
            try:
                manifest = CapabilityManifest.from_dict(
                    json.loads(archive.read(manifest_name).decode("utf-8"))
                )
            except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
                problems.append("wheel manifest is present but unparseable")
                manifest = None
            if manifest is not None:
                for entry in manifest.bundle_entries:
                    prefix = f"awino/_bundle/{entry}"
                    if not any(n == prefix or n.startswith(prefix + "/") for n in names):
                        problems.append(f"wheel bundle is missing: {entry}")
        entry_points = next((n for n in names if n.endswith("entry_points.txt")), None)
        if entry_points is None:
            problems.append("wheel has no entry_points.txt")
        else:
            text = archive.read(entry_points).decode("utf-8", errors="replace")
            if "awino = awino.cli:app" not in text:
                problems.append("wheel entry points do not expose 'awino = awino.cli:app'")
    return problems
