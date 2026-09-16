"""The capability manifest is generated from the tree and must describe it
accurately: a stale or hand-edited manifest is detected, not trusted."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from awino import manifest as M
from awino.cli import registered_command_names

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = registered_command_names()


def test_build_manifest_version_matches_pyproject() -> None:
    built = M.build_manifest(ROOT, COMMANDS)
    assert built.version == "0.8.1"
    assert built.name


def test_manifest_provides_the_capabilities_that_matter() -> None:
    provides = set(M.build_manifest(ROOT, COMMANDS).provides)
    assert "cmd:best" in provides
    assert "cmd:exam" in provides
    assert "cmd:release verify" in provides
    assert "cmd:release publish" in provides
    assert any(p.startswith("cmd:gate ") for p in provides)
    assert any(p.startswith("skill:") for p in provides)
    assert "hook:SessionStart" in provides
    assert "template:task-contract.yaml" in provides
    assert len(provides) >= 90


def test_manifest_entry_points_expose_the_cli() -> None:
    built = M.build_manifest(ROOT, COMMANDS)
    assert "awino = awino.cli:app" in built.entry_points


def test_recorded_manifest_is_in_sync_with_the_tree() -> None:
    assert M.load_manifest(ROOT) is not None
    assert M.verify_manifest(ROOT, COMMANDS) == []


def test_verify_detects_manifest_claiming_a_missing_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = M.load_manifest(ROOT)
    assert recorded is not None
    tampered = replace(recorded, provides=(*recorded.provides, "cmd:does-not-exist"))
    monkeypatch.setattr(M, "load_manifest", lambda root: tampered)
    problems = M.verify_manifest(ROOT, COMMANDS)
    assert any("cmd:does-not-exist" in p and "does not provide" in p for p in problems), problems


def test_verify_detects_tree_capability_missing_from_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = M.load_manifest(ROOT)
    assert recorded is not None
    dropped = tuple(p for p in recorded.provides if p != "cmd:best")
    assert len(dropped) == len(recorded.provides) - 1
    tampered = replace(recorded, provides=dropped)
    monkeypatch.setattr(M, "load_manifest", lambda root: tampered)
    problems = M.verify_manifest(ROOT, COMMANDS)
    assert any("cmd:best" in p and "omits" in p for p in problems), problems


def test_verify_detects_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = M.load_manifest(ROOT)
    assert recorded is not None
    tampered = replace(recorded, version="9.9.9")
    monkeypatch.setattr(M, "load_manifest", lambda root: tampered)
    problems = M.verify_manifest(ROOT, COMMANDS)
    assert any("9.9.9" in p and "version" in p for p in problems), problems


def test_verify_reports_missing_manifest() -> None:
    problems = M.verify_manifest(Path("/nonexistent-root-xyz"), COMMANDS)
    assert any("no recorded manifest" in p for p in problems)


def test_packaged_manifest_loads_from_the_installed_package() -> None:
    packaged = M.load_packaged_manifest()
    assert packaged is not None
    assert packaged.version == "0.8.1"
    assert len(packaged.provides) >= 90
