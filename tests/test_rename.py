"""The ``smith`` -> ``awino`` rename: package, entry points, and state migration.

Covers what the blanket rename cannot prove by itself: that every module
imports under the new name, that both console entry points resolve to the
renamed package, and that a pre-rename ``.smith/`` project keeps working via
the one-shot migration in ``awino.paths``.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import pkgutil
import tomllib
from pathlib import Path

import pytest

import awino
from awino import cli
from awino.paths import (
    AwinoPaths,
    ProjectPaths,
    Workspace,
    migrate_legacy_dir,
    project_state_dir,
    user_config_dir,
)

REPO = Path(__file__).parents[1]


def test_package_imports_under_the_new_name() -> None:
    assert awino.__name__ == "awino"
    assert "AwinoPaths" in awino.__all__


def test_cli_package_imports_under_the_new_name() -> None:
    assert cli.app is not None
    assert callable(cli.deprecated_smith_entry)


def test_console_entry_points_resolve_to_the_renamed_package() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert scripts["awino"] == "awino.cli:app"
    # The deprecated shim is kept for back-compat, pointing at the new package.
    assert scripts["smith"] == "awino.cli:deprecated_smith_entry"

    for name in ("awino", "smith"):
        entry = next(
            ep
            for ep in importlib.metadata.entry_points(group="console_scripts")
            if ep.name == name
        )
        assert entry.value == scripts[name]
        assert callable(entry.load())


def test_every_module_under_the_new_package_imports() -> None:
    failures: list[str] = []
    for module in pkgutil.walk_packages(awino.__path__, prefix="awino."):
        try:
            importlib.import_module(module.name)
        except Exception as exc:
            failures.append(f"{module.name}: {exc!r}")
    assert not failures, "modules that do not import under awino.*:\n" + "\n".join(failures)


def test_paths_classes_resolve_under_the_new_name() -> None:
    home = AwinoPaths.discover(start=REPO)
    assert home.root == REPO
    workspace = Workspace.discover(start=REPO)
    assert isinstance(workspace.project, ProjectPaths)
    assert workspace.project.awino_dir == REPO / ".awino"


# ── .smith/ -> .awino/ migration ─────────────────────────────────────────────


def _snapshot(root: Path) -> dict[str, str]:
    """sha256 of every file under root, keyed by relative posix path."""
    digest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return digest


@pytest.fixture()
def legacy_project(tmp_path: Path) -> Path:
    """A pre-rename project: populated `.smith/` (ledger, memory, mission),
    plus `.seeds/` which must not be touched by the migration."""
    project = tmp_path / "project"
    legacy = project / ".smith"
    (legacy / "run").mkdir(parents=True)
    (legacy / "memory").mkdir(parents=True)
    (legacy / "run" / "ledger.jsonl").write_text(
        '{"run_id": "r1", "event": "opened"}\n{"run_id": "r1", "event": "closed"}\n',
        encoding="utf-8",
    )
    (legacy / "memory" / "lessons.md").write_text(
        "# lessons\n\n- never migrate twice\n", encoding="utf-8"
    )
    (legacy / "MISSION.md").write_text("# mission\n\n## ship it\n", encoding="utf-8")
    (legacy / "project.yaml").write_text("stance: advisor\n", encoding="utf-8")
    (project / ".seeds").mkdir(parents=True)
    (project / ".seeds" / "seed.md").write_text("untouched\n", encoding="utf-8")
    return project


def test_migration_moves_legacy_state_with_zero_data_loss(
    legacy_project: Path,
) -> None:
    before = _snapshot(legacy_project / ".smith")

    resolved = project_state_dir(legacy_project)

    assert resolved == legacy_project / ".awino"
    assert not (legacy_project / ".smith").exists()
    after = _snapshot(legacy_project / ".awino")
    assert after == before, "migration changed bytes"
    # Everything outside .smith/ is untouched.
    assert (legacy_project / ".seeds" / "seed.md").read_text(encoding="utf-8") == "untouched\n"


def test_migration_is_idempotent_and_leaves_an_existing_awino_dir_alone(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / ".smith" / "memory").mkdir(parents=True)
    (project / ".smith" / "memory" / "old.md").write_text("old\n", encoding="utf-8")
    (project / ".awino" / "memory").mkdir(parents=True)
    (project / ".awino" / "memory" / "new.md").write_text("new\n", encoding="utf-8")

    assert project_state_dir(project) == project / ".awino"
    # Both survive; .awino wins; nothing is deleted or merged silently.
    assert (project / ".smith" / "memory" / "old.md").is_file()
    assert (project / ".awino" / "memory" / "new.md").is_file()
    # Second call is a no-op.
    assert project_state_dir(project) == project / ".awino"


def test_old_paths_still_resolve_through_project_paths(
    legacy_project: Path,
) -> None:
    """`ProjectPaths.awino_dir` triggers the migration, so code holding a
    `ProjectPaths` for a legacy project transparently lands on the moved data."""
    paths = ProjectPaths(root=legacy_project)
    assert paths.awino_dir == legacy_project / ".awino"
    assert (paths.awino_dir / "MISSION.md").read_text(encoding="utf-8").startswith("# mission")
    assert (paths.memory / "lessons.md").is_file()
    assert (paths.runs / "ledger.jsonl").is_file()


def test_user_config_dir_migrates_home_dot_smith(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    legacy = fake_home / ".smith"
    legacy.mkdir()
    (legacy / "profile.yaml").write_text("challenge_me: true\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))

    resolved = user_config_dir()
    assert resolved == fake_home / ".awino"
    assert (resolved / "profile.yaml").read_text(encoding="utf-8") == "challenge_me: true\n"
    assert not legacy.exists()


def test_migrate_legacy_dir_is_a_no_op_without_legacy(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert migrate_legacy_dir(project / ".smith", project / ".awino") == project / ".awino"
    assert not (project / ".awino").exists()
