from __future__ import annotations

import json
from pathlib import Path

from smith.toolchain import Manager, Runner, Toolchain


def test_black_box_fixture_matrix(tmp_path: Path, monkeypatch) -> None:
    from smith import toolchain

    monkeypatch.setattr(toolchain, "_have", lambda name: name in {"uv", "make", "python"})

    existing = tmp_path / "existing"
    (existing / ".venv" / "Scripts").mkdir(parents=True)
    (existing / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert Toolchain(existing).manager[0] is Manager.VENV

    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "uv.lock").write_text("version=1\n", encoding="utf-8")
    assert Toolchain(locked).manager[0] is Manager.UV

    fallback = tmp_path / "fallback"
    fallback.mkdir()
    (fallback / "justfile").write_text("test:\n", encoding="utf-8")
    (fallback / "Makefile").write_text("test:\n", encoding="utf-8")
    assert Toolchain(fallback).runner[0] is Runner.MAKE

    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"scripts": {"test": "node test.js"}}))
    assert Toolchain(package).manager[0] is Manager.NONE
    assert Toolchain(package).runner[0] is Runner.NPM_SCRIPTS
