"""Live regression for a real request: one command that updates A.W.I.N.O.
itself the right way for how it is installed, instead of the human needing
to remember two different procedures (Claude plugin commands vs git pull).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from smith import cli


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


class TestClaudePluginDetection:
    def test_no_claude_settings_file_means_not_a_plugin_install(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cli._detect_claude_plugin() is False

    def test_claude_settings_without_awino_enabled_means_not_a_plugin_install(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"some-other-plugin@marketplace": True}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cli._detect_claude_plugin() is False

    def test_awino_enabled_in_claude_settings_is_detected(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"awino@awino": True}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cli._detect_claude_plugin() is True

    def test_malformed_settings_json_is_treated_as_no_plugin_rather_than_crashing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("not valid json{{{", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cli._detect_claude_plugin() is False


class TestUpdateCheckStandaloneClone:
    """Real disposable git clones, no plugin settings present, exercising the
    actual updater.update_preflight() path this command reuses rather than
    duplicating."""

    def _make_remote_and_clone(self, tmp_path: Path) -> tuple[Path, Path]:
        remote = tmp_path / "remote.git"
        remote.mkdir()
        _git(remote, "init", "--bare", "-b", "main")

        origin = tmp_path / "origin"
        origin.mkdir()
        # Real HOME_MARKERS so SmithPaths.discover() recognizes this
        # disposable clone as an A.W.I.N.O. home rather than falling back to
        # the actual source tree running the test - the exact mistake the
        # first draft of this test made, caught by its own assertion.
        (origin / "plugin.json").write_text("{}", encoding="utf-8")
        (origin / "knowledge").mkdir()
        (origin / "knowledge" / ".gitkeep").write_text("", encoding="utf-8")
        _git(origin, "init", "-b", "main")
        _git(origin, "config", "user.email", "test@example.com")
        _git(origin, "config", "user.name", "test")
        (origin / "README.md").write_text("v1\n", encoding="utf-8")
        _git(origin, "add", "README.md", "plugin.json", "knowledge")
        _git(origin, "commit", "-m", "v1")
        _git(origin, "remote", "add", "origin", str(remote))
        _git(origin, "push", "-u", "origin", "main")
        return remote, origin

    def test_already_current_clean_clone_reports_updated_and_version(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _remote, origin = self._make_remote_and_clone(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-claude-here")
        monkeypatch.chdir(origin)
        result = CliRunner().invoke(cli.app, ["update"])
        assert result.exit_code == 0, result.output
        assert "DETECTED  standalone clone" in result.output
        assert "UPDATED  source is clean and fast-forwarded" in result.output
        assert "VERSION" in result.output

    def test_dirty_clone_refuses_and_still_reports_the_unchanged_version(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _remote, origin = self._make_remote_and_clone(tmp_path)
        (origin / "README.md").write_text("locally edited, uncommitted\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-claude-here")
        monkeypatch.chdir(origin)
        result = CliRunner().invoke(cli.app, ["update"])
        assert result.exit_code == 1
        assert "REFUSED" in result.output
        assert "dirty" in result.output
        assert "VERSION" in result.output
        assert "unchanged" in result.output

    def test_behind_clean_clone_pulls_the_remote_change(self, tmp_path: Path, monkeypatch) -> None:
        remote, origin = self._make_remote_and_clone(tmp_path)
        other_clone = tmp_path / "other-clone"
        _git(tmp_path, "clone", str(remote), str(other_clone))
        _git(other_clone, "config", "user.email", "test@example.com")
        _git(other_clone, "config", "user.name", "test")
        (other_clone / "README.md").write_text("v2 from elsewhere\n", encoding="utf-8")
        _git(other_clone, "add", "README.md")
        _git(other_clone, "commit", "-m", "v2")
        _git(other_clone, "push", "origin", "main")

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-claude-here")
        monkeypatch.chdir(origin)
        result = CliRunner().invoke(cli.app, ["update"])
        assert result.exit_code == 0, result.output
        assert "UPDATED" in result.output
        assert (origin / "README.md").read_text(encoding="utf-8") == "v2 from elsewhere\n"
