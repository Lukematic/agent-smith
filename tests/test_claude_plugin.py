from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
CANONICAL_SKILLS = {
    "awino-author-agent",
    "awino-author-tool",
    "awino-bootstrap",
    "awino-consult",
    "awino-delegate",
    "awino-discover",
    "awino-evidence",
    "awino-memory",
    "awino-ralph",
    "awino-reproducibility",
    "awino-rpi",
    "awino-self-update",
    "awino-triage",
    "awino-visualize",
}

VISUAL_TRIGGERS = (
    "Create a Mermaid architecture diagram for this service",
    "Chart these monthly results",
    "Make an interactive dashboard for this data",
    "Draw a technical schematic of the workflow",
)


def load_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_native_plugin_manifest_is_explicit_and_uses_supported_defaults() -> None:
    manifest = load_json(".claude-plugin/plugin.json")
    marketplace = load_json(".claude-plugin/marketplace.json")
    settings = load_json("settings.json")

    assert manifest["name"] == "awino"
    assert "agents" not in manifest
    assert "hooks" not in manifest
    assert settings == {"agent": "awino:awino"}

    plugin = marketplace["plugins"]
    assert len(plugin) == 1
    assert plugin[0]["name"] == "awino"
    assert plugin[0]["source"] == "./"
    assert set(plugin[0]["skills"]) == {f"./skills/{name}" for name in CANONICAL_SKILLS}
    assert "agents" not in plugin[0]
    assert "hooks" not in plugin[0]


def test_repository_exposes_only_canonical_skills() -> None:
    found = {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")}
    assert found == CANONICAL_SKILLS


def test_visual_requests_route_to_awino_visualize() -> None:
    from smith.skill_catalog import SkillCatalog

    empty = ROOT / "tests" / "missing-skill-root"
    catalog = SkillCatalog(empty, empty, ROOT / "skills")

    for request in VISUAL_TRIGGERS:
        recommendation = catalog.recommend(request)
        assert recommendation is not None
        assert recommendation.skill.name == "awino-visualize"


def test_repository_exposes_only_the_awino_agent() -> None:
    agent_files = [path.name for path in (ROOT / "agents").glob("*.md") if path.name != "README.md"]
    assert agent_files == ["awino.md"]


def test_awino_agent_inherits_the_users_selected_model() -> None:
    agent = (ROOT / "agents" / "awino.md").read_text(encoding="utf-8")
    assert "model: inherit" in agent
    assert "model: sonnet" not in agent
    assert "model: claude-sonnet" not in agent


def test_plugin_default_agent_keeps_ask_user_question() -> None:
    """The scoped plugin agent must not resolve to a shadowing user agent."""
    # Claude prints the init event before authentication, so this remains a
    # deterministic tool-resolution test in CI without credentials.
    settings = json.dumps(load_json("settings.json"), separators=(",", ":"))
    result = subprocess.run(
        [
            "claude",
            "-p",
            "--settings",
            settings,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "1",
            "Respond OK.",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    events = [json.loads(line) for line in result.stdout.splitlines()]
    init_line = next(
        event
        for event in events
        if event.get("type") == "system" and event.get("subtype") == "init"
    )

    assert "AskUserQuestion" in init_line["tools"]


def test_plugin_uses_official_root_and_never_initializes_project_state() -> None:
    plugin_files = [
        ROOT / ".claude-plugin" / "plugin.json",
        ROOT / ".claude-plugin" / "marketplace.json",
        ROOT / "hooks" / "hooks.json",
        ROOT / "agents" / "awino.md",
        ROOT / "bin" / "awino",
        ROOT / "bin" / "awino.cmd",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in plugin_files)

    assert "${PLUGIN_ROOT}" not in combined
    assert "${CLAUDE_PLUGIN_ROOT}" in combined
    assert "work-init" in combined
    assert "sd init" not in combined
    assert "pip install" not in combined
    assert "uv tool install" not in combined


def test_missing_uv_launcher_is_truthfully_degraded(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    result = subprocess.run(
        ["C:\\Windows\\System32\\cmd.exe", "/d", "/c", str(ROOT / "bin" / "awino.cmd")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "DEGRADED" in result.stderr
    assert "needs uv and Python" in result.stderr
    assert "run this command again" in result.stderr


def test_clean_plugin_cache_prepares_locked_environment_and_runs_doctor(tmp_path: Path) -> None:
    plugin = tmp_path / "plugins" / "cache" / "awino" / "awino" / "0.4.1"
    shutil.copytree(
        ROOT,
        plugin,
        ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", ".ruff_cache"),
    )
    env = os.environ.copy()
    env["UV_LINK_MODE"] = "copy"
    if os.name == "nt":
        command = [
            "C:\\Windows\\System32\\cmd.exe",
            "/d",
            "/c",
            str(plugin / "bin" / "awino.cmd"),
            "doctor",
            "--fast",
        ]
    else:
        command = ["sh", str(plugin / "bin" / "awino"), "doctor", "--fast"]

    first = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    second = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "HEALTH" in first.stdout
    assert (plugin / ".venv").is_dir()


def test_claude_cli_strictly_validates_plugin_and_marketplace() -> None:
    result = subprocess.run(
        ["claude", "plugin", "validate", ".", "--strict"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
