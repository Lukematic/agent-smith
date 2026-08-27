"""Real subprocess CLI test for `awino config-review`, plus the read-only guarantee.

The read-only guarantee is not claimed, it is measured: every file under the
fixture project is hashed and mtime-stamped before and after invoking the CLI,
and the test fails if a single byte or timestamp changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def _snapshot(root: Path) -> dict[str, tuple[float, str]]:
    snapshot: dict[str, tuple[float, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[str(path.relative_to(root))] = (path.stat().st_mtime, digest)
    return snapshot


def _run_awino(args: list[str], project_root: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["AWINO_PROJECT"] = str(project_root)
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _mixed_conflict_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "mixed"\nrequires-python = ">=3.12"\n[tool.uv]\n',
        encoding="utf-8",
    )
    (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (project / "Makefile").write_text("test:\n\tpytest -q\n", encoding="utf-8")
    (project / "justfile").write_text("test:\n    pytest --cov\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=do-not-print\n", encoding="utf-8")
    (project / "kilo.json").write_text('{"permissions": {"*": "allow"}}\n', encoding="utf-8")
    (project / "README.md").write_text("Run `make test`.\n", encoding="utf-8")
    return project


class TestConfigReviewCliJson:
    def test_json_output_shape_and_citations(self, tmp_path: Path) -> None:
        project = _mixed_conflict_project(tmp_path)

        result = _run_awino(["config-review", "--json"], project)

        assert result.returncode == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["root"] == str(project)
        assert payload["count"] == len(payload["findings"])
        assert payload["count"] > 0
        assert payload["summary"]["error"] >= 1

        for finding in payload["findings"]:
            assert set(finding) == {
                "severity",
                "category",
                "citation",
                "message",
                "suggested_command",
            }
            assert finding["severity"] in {"info", "warn", "error"}
            # Every finding must cite a real path, never a vague description.
            assert finding["citation"], finding

        secret_leaked = any("do-not-print" in finding["message"] for finding in payload["findings"])
        assert not secret_leaked

    def test_human_readable_output_includes_citations(self, tmp_path: Path) -> None:
        project = _mixed_conflict_project(tmp_path)

        result = _run_awino(["config-review"], project)

        assert result.returncode == 1, result.stdout + result.stderr
        assert "SUMMARY" in result.stdout
        assert "kilo.json" in result.stdout
        assert "justfile" in result.stdout or "Makefile" in result.stdout

    def test_clean_project_exits_zero(self, tmp_path: Path) -> None:
        project = tmp_path / "clean"
        project.mkdir()
        (project / "package.json").write_text(
            '{"name": "clean", "scripts": {"test": "jest"}}\n', encoding="utf-8"
        )

        result = _run_awino(["config-review", "--json"], project)

        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["count"] == 0


class TestConfigReviewIsReadOnly:
    """The read-only guarantee, measured rather than asserted in prose."""

    def test_zero_file_writes_against_mixed_conflict_fixture(self, tmp_path: Path) -> None:
        project = _mixed_conflict_project(tmp_path)
        before = _snapshot(project)

        result = _run_awino(["config-review", "--json"], project)

        after = _snapshot(project)
        assert result.returncode in (0, 1), result.stdout + result.stderr
        assert set(before) == set(after), "config-review changed the set of files present"
        assert before == after, "config-review modified file content or mtime"

    def test_zero_file_writes_against_uv_make_fixture(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "a"\nrequires-python = ">=3.12"\n[tool.uv]\n[tool.ruff]\n',
            encoding="utf-8",
        )
        (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        (project / "Makefile").write_text(
            "test:\n\tuv run pytest -q\n\nlint:\n\tuv run ruff check .\n", encoding="utf-8"
        )
        (project / "README.md").write_text("Run `make test` and `make lint`.\n", encoding="utf-8")
        before = _snapshot(project)

        result = _run_awino(["config-review"], project)

        after = _snapshot(project)
        assert result.returncode == 0, result.stdout + result.stderr
        assert before == after
