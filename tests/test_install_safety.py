from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from awino.harness import Harness, Target, _link_or_copy, install
from awino.ownership import manifest_path

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def awino_home(tmp_path: Path) -> Path:
    home = tmp_path / "source"
    (home / "agents").mkdir(parents=True)
    (home / "skills" / "awino-test").mkdir(parents=True)
    (home / "agents" / "awino.md").write_text("---\nname: awino\n---\nbody\n", encoding="utf-8")
    (home / "skills" / "awino-test" / "SKILL.md").write_text("v1\n", encoding="utf-8")
    return home


def force_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Path, "symlink_to", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, b"", b""),
    )


def test_foreign_real_directory_is_never_deleted(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("new", encoding="utf-8")
    destination = tmp_path / "skill"
    destination.mkdir()
    (destination / "mine.txt").write_text("keep", encoding="utf-8")

    outcome, detail = _link_or_copy(source, destination)

    assert outcome == "FAILED"
    assert "not installer-owned" in detail
    assert (destination / "mine.txt").read_text(encoding="utf-8") == "keep"


def test_unchanged_installer_copy_refreshes_and_repeat_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_copy(monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("v1", encoding="utf-8")
    destination = tmp_path / "skill"
    assert _link_or_copy(source, destination)[0] == "COPIED"
    (source / "SKILL.md").write_text("v2", encoding="utf-8")
    assert _link_or_copy(source, destination)[0] == "COPIED"
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "v2"
    assert _link_or_copy(source, destination)[0] == "SKIPPED"


def test_modified_owned_copy_is_backed_up_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_copy(monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("v1", encoding="utf-8")
    destination = tmp_path / "skill"
    assert _link_or_copy(source, destination)[0] == "COPIED"
    (destination / "SKILL.md").write_text("local", encoding="utf-8")

    outcome, detail = _link_or_copy(source, destination)

    assert outcome == "FAILED"
    backup = Path(detail.split("backup: ", 1)[1])
    assert (backup / "SKILL.md").read_text(encoding="utf-8") == "local"
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "local"


def test_modified_persona_is_backed_up_and_refused(awino_home: Path, tmp_path: Path) -> None:
    target = Target(Harness.CLAUDE, tmp_path / ".claude", "project")
    target.root.mkdir()
    assert not any(action.failed for action in install(awino_home, target, skills=False))
    target.persona_path.write_text("local persona\n", encoding="utf-8")

    actions = install(awino_home, target, skills=False)

    assert actions[0].failed
    backup = Path(actions[0].detail.split("backup: ", 1)[1])
    assert backup.read_text(encoding="utf-8") == "local persona\n"
    assert target.persona_path.read_text(encoding="utf-8") == "local persona\n"


def test_manifest_is_deterministic_and_records_hashes(awino_home: Path, tmp_path: Path) -> None:
    target = Target(Harness.CLAUDE, tmp_path / ".claude", "project")
    target.root.mkdir()
    install(awino_home, target, skills=False)
    first = manifest_path(target.root).read_bytes()
    install(awino_home, target, skills=False)
    second = manifest_path(target.root).read_bytes()
    payload = json.loads(second)

    assert first == second
    assert payload["version"] == 1
    assert payload["entries"]["agents/awino.md"]["sha256"]


def test_windows_persona_install_writes_the_exact_hashed_lf_bytes(
    awino_home: Path, tmp_path: Path
) -> None:
    target = Target(Harness.CLAUDE, tmp_path / ".claude", "project")
    target.root.mkdir()

    first = install(awino_home, target, skills=False)
    second = install(awino_home, target, skills=False)

    assert first[0].outcome == "INSTALLED"
    assert second[0].outcome == "SKIPPED"
    assert b"\r\n" not in target.persona_path.read_bytes()


def test_windows_persona_real_edit_is_still_backed_up_and_refused(
    awino_home: Path, tmp_path: Path
) -> None:
    target = Target(Harness.CLAUDE, tmp_path / ".claude", "project")
    target.root.mkdir()
    install(awino_home, target, skills=False)
    target.persona_path.write_bytes(target.persona_path.read_bytes() + b"local edit\n")

    action = install(awino_home, target, skills=False)[0]

    assert action.failed
    assert "backup:" in action.detail


def test_exact_link_is_skipped(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"
    try:
        destination.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert _link_or_copy(source, destination)[0] == "SKIPPED"


# ── install reliability (workstream 1) ────────────────────────────────────────


def test_installer_scripts_are_executable_in_git_index() -> None:
    """Fresh POSIX clones must be able to run ./install.sh directly."""
    result = subprocess.run(
        ["git", "ls-files", "-s", "install.sh", "bootstrap.sh", "bin/awino"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    modes = {line.split()[-1]: line.split()[0] for line in result.stdout.splitlines()}
    assert modes == {
        "install.sh": "100755",
        "bootstrap.sh": "100755",
        "bin/awino": "100755",
    }


def test_launcher_finds_uv_in_local_bin_not_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bin/awino must prepend ~/.local/bin to PATH before checking for uv."""
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    fake_uv = local_bin / "uv"
    fake_uv.write_text("#!/bin/sh\nprintf 'uv-ok\\n'\n", encoding="utf-8")
    fake_uv.chmod(0o755)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # deliberately no ~/.local/bin
    monkeypatch.delenv("AWINO_PROJECT", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)

    result = subprocess.run(
        [str(REPO_ROOT / "bin" / "awino"), "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "uv-ok" in result.stdout  # only the fake uv prints this


def test_launcher_still_degrades_gracefully_without_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("AWINO_PROJECT", raising=False)

    result = subprocess.run(
        [str(REPO_ROOT / "bin" / "awino"), "doctor"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "uv" in result.stderr


def _install_summary_snippet() -> str:
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    begin = text.index("# BEGIN_INSTALL_SUMMARY")
    end = text.index("# END_INSTALL_SUMMARY")
    return text[begin:end]


def _run_install_summary(doctor_failed: int, tests_failed: int) -> str:
    """Evaluate the real install_summary function from install.sh in isolation."""
    script = _install_summary_snippet() + f"\ninstall_summary {doctor_failed} {tests_failed}\n"
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_failure_summary_names_tests_when_doctor_is_clean() -> None:
    out = _run_install_summary(0, 1)
    assert "test suite" in out.lower()
    assert "doctor" not in out.lower()


def test_failure_summary_names_doctor_when_tests_pass() -> None:
    out = _run_install_summary(1, 0)
    assert "doctor" in out.lower()
    assert "test suite" not in out.lower()


def test_failure_summary_names_both_when_both_fail() -> None:
    out = _run_install_summary(1, 1)
    assert "doctor" in out.lower()
    assert "test suite" in out.lower()


def test_install_tail_reports_the_failed_step() -> None:
    tail = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'install_summary "$DOCTOR_FAILED" "$TESTS_FAILED"' in tail
    assert "Fix what the doctor reported" not in tail
