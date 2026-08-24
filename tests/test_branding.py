from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TEXT_SUFFIXES = {".md", ".py", ".ps1", ".sh", ".json", ".toml", ".yaml", ".yml"}
ACTIVE_ROOT_FILES = {
    "AWINO.md",
    "README.md",
    "bootstrap.ps1",
    "bootstrap.sh",
    "install.ps1",
    "install.sh",
    "justfile",
    "plugin.json",
    "pyproject.toml",
}
ACTIVE_TREES = ("docs", "hooks", "knowledge", "src", "templates")
HISTORICAL_PREFIXES = ("docs/updates/", "memory/")
HISTORICAL_FILES = {"docs/name-options.txt"}
LEGACY_FILES = {"AGENT_SMITH.md", "agents/agent-smith.md"}
COMPATIBILITY_IMPLEMENTATIONS = {
    "src/smith/skill_catalog.py",
    "src/smith/health.py",
    "src/smith/knowledge.py",
    "src/smith/watch.py",
}

OLD_BRAND = re.compile(r"\bAgent Smith\b|\bSmith(?:'s)?\b|\bsmith\s+[a-z]", re.IGNORECASE)
OLD_ENV = re.compile(r"(?<![A-Z])SMITH_[A-Z0-9_]+")
OLD_MARKER = re.compile(r"<!-- smith:(?:generated|stub) -->")


def _active_files() -> list[Path]:
    files = [ROOT / name for name in ACTIVE_ROOT_FILES]
    files.extend(ROOT.glob("agents/awino.md"))
    files.extend(ROOT.glob("skills/awino-*/SKILL.md"))
    for tree in ACTIVE_TREES:
        files.extend(path for path in (ROOT / tree).rglob("*") if path.is_file())
    return sorted(
        {path for path in files if path.suffix in TEXT_SUFFIXES or path.name == "justfile"}
    )


def _allowed_residue(path: Path, line: str) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    if relative in LEGACY_FILES or relative in HISTORICAL_FILES:
        return True
    if relative in COMPATIBILITY_IMPLEMENTATIONS:
        return True
    if relative.startswith(HISTORICAL_PREFIXES):
        return True

    # Python's installed module/package ABI and the on-disk state directory stay `.smith`.
    if "src/smith" in line or re.search(r"(?:from|import) smith(?:\.|\b)", line):
        return True
    if re.search(r'[`"]smith(?:/|\.|_)|\bsmith\.cli:|--cov=smith|smith/healing\.py', line):
        return True
    if re.search(r"\bSmith(?:Paths|Path|Home|Workspace)\b", line):
        return True
    if ".smith" in line:
        line = line.replace(".smith", "")

    # The current repository URL and explicit migration/compatibility behavior remain old-named.
    if "agent-smith" in line and ("github.com" in line or "raw.githubusercontent.com" in line):
        return True
    if "agent-smith" in line and any(
        token in line
        for token in (
            "agents/agent-smith",
            "plugins/agent-smith",
            "agent-smith.chatmode",
            "agent-smith.mdc",
            "agent-smith.md",
        )
    ):
        return True
    if "SMITH_" in line and "AWINO_" in line:
        return True
    compatibility_words = (
        "deprecated",
        "compatibility",
        "former",
        "legacy",
        "migration",
        "fallback",
        "falls back",
    )
    if any(word in line.lower() for word in compatibility_words):
        return True
    if OLD_MARKER.search(line) and ("recogn" in line.lower() or "legacy" in line.lower()):
        return True
    return not (OLD_BRAND.search(line) or OLD_ENV.search(line) or OLD_MARKER.search(line))


def test_active_surfaces_have_no_unclassified_smith_branding() -> None:
    violations: list[str] = []
    for path in _active_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _allowed_residue(path, line):
                relative = path.relative_to(ROOT).as_posix()
                violations.append(f"{relative}:{number}: {line.strip()}")

    assert not violations, "Unclassified legacy branding:\n" + "\n".join(violations)


def test_canonical_skill_set_contains_only_thirteen_awino_skills() -> None:
    canonical = sorted(path.parent.name for path in ROOT.glob("skills/awino-*/SKILL.md"))
    assert len(canonical) == 13
    assert all(name.startswith("awino-") for name in canonical)
