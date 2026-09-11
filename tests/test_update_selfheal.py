"""Self-healing ``awino update``: after the version update, ensure project
state exists, refresh only harnesses that were already detected, and prove
the whole operation is idempotent and non-destructive.

This is the "pull in what's missing, keep project things aside and add them
back" behavior the human described as wanting from ``awino update``. It
reuses ``updater.snapshot``/``restore`` (already correct) and
``harness.refresh_skills`` (S6) rather than inventing new preservation logic.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from awino import cli
from awino.enforce import Ledger, TaskClass

REPO_ROOT = Path(__file__).resolve().parents[1]

SMITH_HOME_MARKERS = ("plugin.json", "knowledge")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )


def _make_remote_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        capture_output=True,
        text=True,
        check=True,
    )

    origin = tmp_path / "origin"
    origin.mkdir()
    for marker in SMITH_HOME_MARKERS:
        target = origin / marker
        if marker == "knowledge":
            target.mkdir()
            (target / ".gitkeep").write_text("", encoding="utf-8")
        else:
            target.write_text("{}", encoding="utf-8")
    (origin / "agents").mkdir()
    (origin / "agents" / "awino.md").write_text("---\nname: awino\n---\n\nbody\n", encoding="utf-8")
    (origin / "skills").mkdir()
    _git(origin, "init", "-b", "main")
    _git(origin, "config", "user.email", "test@example.com")
    _git(origin, "config", "user.name", "test")
    (origin / "README.md").write_text("v1\n", encoding="utf-8")
    _git(origin, "add", "README.md", "plugin.json", "knowledge", "agents", "skills")
    _git(origin, "commit", "-m", "v1")
    _git(origin, "remote", "add", "origin", str(remote))
    _git(origin, "push", "-u", "origin", "main")
    return remote, origin


def _make_target_project(tmp_path: Path) -> Path:
    project = tmp_path / "target-project"
    project.mkdir()
    (project / ".git").mkdir()
    return project


class TestUpdateEnsuresProjectState:
    def test_a_project_missing_run_dir_gains_it_after_update(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _remote, origin = _make_remote_and_clone(tmp_path)
        project = _make_target_project(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-claude-here")
        monkeypatch.setenv("AWINO_HOME", str(origin))
        monkeypatch.chdir(project)

        result = CliRunner().invoke(cli.app, ["update"])

        assert result.exit_code == 0, result.output
        assert (project / ".awino" / "run").is_dir()


class TestUpdateRefreshesOnlyDetectedHarnesses:
    def test_an_absent_harness_stays_absent_after_update(self, tmp_path: Path, monkeypatch) -> None:
        _remote, origin = _make_remote_and_clone(tmp_path)
        project = _make_target_project(tmp_path)
        fake_home = tmp_path / "no-claude-here"
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setenv("AWINO_HOME", str(origin))
        monkeypatch.chdir(project)

        result = CliRunner().invoke(cli.app, ["update"])

        assert result.exit_code == 0, result.output
        assert not (fake_home / ".claude").exists()
        assert not (fake_home / ".config" / "kilo").exists()


class TestUpdatePreservesProjectSpecificState:
    def test_project_yaml_memory_and_run_survive_byte_identical(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _remote, origin = _make_remote_and_clone(tmp_path)
        project = _make_target_project(tmp_path)
        awino_dir = project / ".awino"
        memory_dir = awino_dir / "memory"
        memory_dir.mkdir(parents=True)
        lessons = memory_dir / "lessons.md"
        lessons.write_text("- [2026-01-01] a durable project lesson\n", encoding="utf-8")
        project_yaml = awino_dir / "project.yaml"
        project_yaml.write_text("mission: test project\n", encoding="utf-8")

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-claude-here")
        monkeypatch.setenv("AWINO_HOME", str(origin))
        monkeypatch.chdir(project)

        before_lessons = lessons.read_bytes()
        before_yaml = project_yaml.read_bytes()

        result = CliRunner().invoke(cli.app, ["update"])

        assert result.exit_code == 0, result.output
        assert lessons.read_bytes() == before_lessons
        assert project_yaml.read_bytes() == before_yaml


class TestUpdateIsIdempotent:
    def test_a_second_consecutive_update_reports_no_further_changes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _remote, origin = _make_remote_and_clone(tmp_path)
        project = _make_target_project(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-claude-here")
        monkeypatch.setenv("AWINO_HOME", str(origin))
        monkeypatch.chdir(project)

        first = CliRunner().invoke(cli.app, ["update"])
        assert first.exit_code == 0, first.output
        run_dir_before = list((project / ".awino" / "run").iterdir())

        second = CliRunner().invoke(cli.app, ["update"])
        assert second.exit_code == 0, second.output
        run_dir_after = list((project / ".awino" / "run").iterdir())

        assert run_dir_before == run_dir_after


class TestUpdatePrintsOneSummaryEndingWithVersion:
    def test_the_final_line_reports_the_active_version(self, tmp_path: Path, monkeypatch) -> None:
        _remote, origin = _make_remote_and_clone(tmp_path)
        project = _make_target_project(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-claude-here")
        monkeypatch.setenv("AWINO_HOME", str(origin))
        monkeypatch.chdir(project)

        result = CliRunner().invoke(cli.app, ["update"])

        assert result.exit_code == 0, result.output
        lines = [line for line in result.output.splitlines() if line.strip()]
        assert lines[-1].startswith("VERSION")


class TestStaleInstallUpgradesEndToEnd:
    """A stale install upgrades cleanly with project state intact.

    The owner points agents at the repo URL, so a clone that fell behind must
    fast-forward through the real ``awino update-preflight`` + ``awino update``
    path - file:// upstream, no network - while .awino state (project.yaml,
    memory, ledger runs) survives byte-identical.
    """

    def _origin_from_checkout(self, tmp_path: Path) -> tuple[Path, str, str]:
        """Bare origin cloned from this checkout, with its full history."""
        origin = tmp_path / "origin.git"
        subprocess.run(
            ["git", "clone", "--bare", str(REPO_ROOT), str(origin)],
            capture_output=True,
            text=True,
            check=True,
        )
        new_sha = _git(REPO_ROOT, "rev-parse", "HEAD").stdout.strip()
        old_sha = _git(REPO_ROOT, "rev-parse", "HEAD~5").stdout.strip()
        assert new_sha and old_sha and new_sha != old_sha
        return origin, new_sha, old_sha

    def test_stale_clone_fast_forwards_and_project_state_survives(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        origin, new_sha, old_sha = self._origin_from_checkout(tmp_path)

        install = tmp_path / "install"
        _git(tmp_path, "clone", f"file://{origin}", str(install))
        _git(install, "config", "user.email", "test@example.com")
        _git(install, "config", "user.name", "test")
        # Simulate the stale install: same tracking branch, wound back 5
        # commits. A detached checkout would have no @{u}, which preflight
        # rightly refuses - a real stale install sits behind on its branch.
        _git(install, "reset", "--hard", old_sha)
        assert _git(install, "rev-parse", "HEAD").stdout.strip() == old_sha
        assert _git(install, "status", "--porcelain").stdout.strip() == ""

        project = tmp_path / "target-project"
        project.mkdir()
        (project / ".git").mkdir()
        awino_dir = project / ".awino"
        (awino_dir / "memory").mkdir(parents=True)
        project_yaml = awino_dir / "project.yaml"
        project_yaml.write_text(
            "mission: upgrade probe\nupgrade_marker: STALE-STATE-KEPT-4242\n",
            encoding="utf-8",
        )
        lesson = awino_dir / "memory" / "lessons.md"
        lesson.write_text(
            "- [2026-09-11] stale installs must upgrade cleanly\n", encoding="utf-8"
        )
        run = Ledger(awino_dir).open(
            TaskClass.RESEARCH, objective="probe run that must survive upgrade"
        )

        before_yaml = project_yaml.read_bytes()
        before_lesson = lesson.read_bytes()

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-claude-here")
        monkeypatch.setenv("AWINO_HOME", str(install))
        monkeypatch.setenv("AWINO_PROJECT", str(project))
        monkeypatch.chdir(project)

        preflight = CliRunner().invoke(cli.app, ["update-preflight"])
        assert preflight.exit_code == 0, preflight.output
        assert "BACKUP" in preflight.output
        assert "UPDATED" in preflight.output

        updated = CliRunner().invoke(cli.app, ["update"])
        assert updated.exit_code == 0, updated.output
        assert "UPDATED" in updated.output

        # The engine fast-forwarded to the newer commit.
        assert _git(install, "rev-parse", "HEAD").stdout.strip() == new_sha
        # Project state is byte-identical, marker value included.
        assert project_yaml.read_bytes() == before_yaml
        assert b"STALE-STATE-KEPT-4242" in project_yaml.read_bytes()
        assert lesson.read_bytes() == before_lesson
        # The ledger run still loads.
        loaded = Ledger(awino_dir).load(run.run_id)
        assert loaded.objective == "probe run that must survive upgrade"
