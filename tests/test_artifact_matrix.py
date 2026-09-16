"""Artifact behavior and the release gate.

Clean source, wheel-only, and staged-plugin artifacts each verify; a
deliberately broken artifact is rejected WITH A SPECIFIC REASON; and the
release gate refuses to publish/push/tag without explicit authorization.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from awino import manifest as M
from awino import release as R
from awino.cli import registered_command_names

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = registered_command_names()
IGNORE = shutil.ignore_patterns(".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__")


@pytest.fixture(scope="module")
def staged_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A clean copy of the source tree, as a staging area would hold it."""
    staged = tmp_path_factory.mktemp("staged") / "awino"
    shutil.copytree(ROOT, staged, ignore=IGNORE)
    return staged


@pytest.fixture()
def broken_tree(staged_tree: Path, tmp_path: Path) -> Path:
    """A deliberately broken artifact: a bundle entry is missing."""
    broken = tmp_path / "broken-awino"
    shutil.copytree(staged_tree, broken)
    (broken / "memory" / "lessons.md").unlink()
    return broken


def test_clean_source_verifies() -> None:
    report = R.verify_artifact(ROOT, COMMANDS)
    assert report.ok, report.problems
    assert report.version == "0.8.0"
    assert len(report.capabilities) >= 90


def test_wheel_only_artifact_verifies(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH")
    out = tmp_path / "dist"
    out.mkdir()
    built = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert built.returncode == 0, built.stderr[-500:]
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1
    problems = M.verify_wheel(wheels[0])
    assert problems == [], problems
    # The packaged manifest inside the wheel parses and describes this artifact.
    with zipfile.ZipFile(wheels[0]) as archive:
        raw = archive.read("awino/capabilities.json").decode("utf-8")
    packaged = M.CapabilityManifest.from_dict(json.loads(raw))
    assert packaged.version == "0.8.0"
    assert len(packaged.provides) >= 90

    # Inventory alone is not behavioral proof. Execute the console command
    # supplied by the wheel in uv's isolated tool environment so imports cannot
    # silently resolve to this source checkout.
    smoke = subprocess.run(
        [uv, "tool", "run", "--from", str(wheels[0]), "awino", "--version"],
        cwd=tmp_path,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "AWINO_PROJECT", "SMITH_PROJECT"}
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert smoke.stdout.strip() == "awino 0.8.0"


def test_verify_wheel_rejects_a_wheel_without_manifest(tmp_path: Path) -> None:
    fake = tmp_path / "fake-0.1-py3-none-any.whl"
    with zipfile.ZipFile(fake, "w") as archive:
        archive.writestr("awino/__init__.py", "")
    problems = M.verify_wheel(fake)
    assert any("capabilities.json" in p for p in problems), problems


def test_staged_plugin_tree_verifies(staged_tree: Path) -> None:
    report = R.verify_artifact(staged_tree, COMMANDS)
    assert report.ok, report.problems
    plugin_version = json.loads((staged_tree / "plugin.json").read_text(encoding="utf-8"))[
        "version"
    ]
    assert report.version == plugin_version


def test_deliberately_broken_artifact_rejected_with_specific_reason(
    broken_tree: Path,
) -> None:
    report = R.verify_artifact(broken_tree, COMMANDS)
    assert not report.ok
    assert any("memory/lessons.md" in p for p in report.problems), report.problems


def test_broken_manifest_claim_rejected_with_specific_reason(
    staged_tree: Path, tmp_path: Path
) -> None:
    broken = tmp_path / "bogus-manifest"
    shutil.copytree(staged_tree, broken)
    manifest_path = broken / "src" / "awino" / M.MANIFEST_FILENAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["provides"] = sorted(data["provides"] + ["cmd:bogus-capability"])
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    report = R.verify_artifact(broken, COMMANDS)
    assert not report.ok
    assert any("cmd:bogus-capability" in p and "does not provide" in p for p in report.problems), (
        report.problems
    )


def test_release_gate_refuses_publish_without_authorization() -> None:
    with pytest.raises(R.AuthorizationRequired) as exc_info:
        R.release_gate(ROOT, action="publish", authorize=False, commands=COMMANDS)
    assert "authorization" in str(exc_info.value).lower()


def test_release_gate_refuses_push_and_tag_without_authorization() -> None:
    for action in ("push", "tag"):
        with pytest.raises(R.AuthorizationRequired):
            R.release_gate(ROOT, action=action, authorize=False, commands=COMMANDS)


def test_release_gate_refuses_broken_artifact_with_specific_reason(
    broken_tree: Path,
) -> None:
    with pytest.raises(R.ReleaseRefused) as exc_info:
        R.release_gate(broken_tree, action="publish", authorize=True, commands=COMMANDS)
    assert any("memory/lessons.md" in r for r in exc_info.value.reasons), exc_info.value.reasons


def test_release_gate_authorized_good_artifact_lists_capabilities() -> None:
    result = R.release_gate(ROOT, action="publish", authorize=True, commands=COMMANDS)
    assert result.authorized is True
    assert result.performed is False  # the gate verifies; it never publishes itself
    assert len(result.capabilities) >= 90
    assert "verification" in result.note.lower() or "verified" in result.note.lower()


def _cli(*argv: str, root: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "awino.cli", *argv, "--root", str(root)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_release_verify_cli_names_the_broken_file(broken_tree: Path) -> None:
    """Human-observable: the gate says exactly what is broken and refuses."""
    completed = _cli("release", "verify", root=broken_tree)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "REFUSED" in completed.stdout
    assert "memory/lessons.md" in completed.stdout


def test_release_verify_cli_lists_capabilities_on_good_artifact(
    staged_tree: Path,
) -> None:
    completed = _cli("release", "verify", root=staged_tree)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "VERIFIED" in completed.stdout
    assert "cmd:best" in completed.stdout


def test_release_publish_cli_refuses_without_authorize_flag() -> None:
    completed = _cli("release", "publish", root=ROOT)
    assert completed.returncode == 3, completed.stdout + completed.stderr
    assert "authorization" in (completed.stdout + completed.stderr).lower()
