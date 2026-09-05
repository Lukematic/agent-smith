from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
CANONICAL_SKILLS = {
    "awino-author-agent",
    "awino-author-tool",
    "awino-bootstrap",
    "awino-config-review",
    "awino-consult",
    "awino-delegate",
    "awino-debug",
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


def test_failures_route_to_debug_but_vague_agent_behavior_routes_to_triage() -> None:
    from smith.skill_catalog import SkillCatalog

    empty = ROOT / "tests" / "missing-skill-root"
    catalog = SkillCatalog(empty, empty, ROOT / "skills")

    for request in ("fix this bug", "pytest has failing tests", "ValueError in parser"):
        recommendation = catalog.recommend(request)
        assert recommendation is not None
        assert recommendation.skill.name == "awino-debug"
    recommendation = catalog.recommend("my agent keeps behaving badly")
    assert recommendation is not None
    assert recommendation.skill.name == "awino-triage"


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
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not installed")
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
    assert "project-bootstrap" in combined
    assert "sd init" not in combined
    assert "pip install" not in combined
    assert "uv tool install" not in combined


def test_plugin_registers_project_memory_and_guard_hooks() -> None:
    hooks = load_json("hooks/hooks.json")["hooks"]
    assert "SessionStart" in hooks
    assert "UserPromptSubmit" in hooks
    assert "PreToolUse" in hooks
    assert hooks["PreToolUse"][0]["matcher"] == "Bash|Edit|Write|MultiEdit"


def test_missing_uv_launcher_is_truthfully_degraded(tmp_path: Path) -> None:
    env = os.environ.copy()
    uv_bin = shutil.which("uv")
    if uv_bin:
        uv_dir = str(Path(uv_bin).parent)
        env["PATH"] = os.pathsep.join(
            p
            for p in env.get("PATH", "").split(os.pathsep)
            if p != uv_dir and ".local" not in p and "cargo" not in p
        )
    else:
        env["PATH"] = str(tmp_path)
    if os.name == "nt":
        command = [
            "C:\\Windows\\System32\\cmd.exe",
            "/d",
            "/c",
            str(ROOT / "bin" / "awino.cmd"),
        ]
    else:
        command = ["sh", str(ROOT / "bin" / "awino")]
    result = subprocess.run(
        command,
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
    version = load_json("plugin.json")["version"]
    plugin = tmp_path / "plugins" / "cache" / "awino" / "awino" / version
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


def test_launcher_isolates_backend_environment_and_keeps_target_project(tmp_path: Path) -> None:
    version = load_json("plugin.json")["version"]
    plugin = tmp_path / "plugins" / "cache" / "awino" / "awino" / version
    shutil.copytree(
        ROOT,
        plugin,
        ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", ".ruff_cache"),
    )
    project = tmp_path / "target"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "target"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (project / "target.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["uv", "lock"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(["uv", "sync"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=project,
        check=True,
    )

    env = os.environ.copy()
    env.pop("AWINO_PROJECT", None)
    env.pop("SMITH_PROJECT", None)
    env["VIRTUAL_ENV"] = str(project / ".venv")
    env["CONDA_PREFIX"] = str(project / ".venv")
    env["UV_LINK_MODE"] = "copy"
    if os.name == "nt":
        launcher = [
            "C:\\Windows\\System32\\cmd.exe",
            "/d",
            "/c",
            str(plugin / "bin" / "awino.cmd"),
        ]
    else:
        launcher = ["sh", str(plugin / "bin" / "awino")]

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*launcher, *args],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )

    context = run("context")
    assert context.returncode == 0, context.stdout + context.stderr
    assert f"project       {project}" in context.stdout
    assert "VIRTUAL_ENV" not in context.stderr
    assert "does not match the project environment path" not in context.stderr

    plan = project / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    opened = run(
        "gate",
        "open",
        "code-change",
        "launcher regression",
        "--scope",
        "target.txt",
        "--plan",
        str(plan),
    )
    assert opened.returncode == 0, opened.stdout + opened.stderr
    approved = run("gate", "plan", "approve", "--by", "test")
    assert approved.returncode == 0, approved.stdout + approved.stderr
    recorded = run(
        "gate",
        "record",
        "tested",
        "--cmd",
        'uv run python -c "import pathlib,sys; print(pathlib.Path(sys.prefix).resolve())"',
    )
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    assert str((project / ".venv").resolve()) in recorded.stdout
    (project / "target.txt").write_text("after\n", encoding="utf-8")
    checked = run("gate", "check", "--diff-base", "HEAD")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "GIT_DIFF_FAILED" not in checked.stdout
    assert "TESTS_NOT_WEAKENED  ok" in checked.stdout
    assert "SCOPE_RESPECTED  ok" in checked.stdout


def test_claude_cli_strictly_validates_plugin_and_marketplace() -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not installed")
    result = subprocess.run(
        ["claude", "plugin", "validate", ".", "--strict"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
