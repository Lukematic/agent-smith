"""Phase 4: hook recursion prevention.

A host hook that triggers its own event must be refused, not recursed.
These tests pin the guard in ``awino.hosts.recursion``: the allow path,
the nested-invocation refusal, the cross-process chain refusal, env
restoration, and the CLI-level refusal (exit 3, clear message) for the
real ``awino hook`` entry point.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from awino.hosts import recursion as R
from awino.hosts.recursion import (
    HookRecursionRefused,
    current_chain,
    current_depth,
    describe_refusal,
    guarded,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_hook_env(monkeypatch):
    monkeypatch.delenv(R.DEPTH_ENV, raising=False)
    monkeypatch.delenv(R.CHAIN_ENV, raising=False)


class TestAllowPath:
    def test_single_hook_run_is_allowed(self):
        with guarded("prompt"):
            assert current_depth() == 1
            assert current_chain() == ["prompt"]

    def test_env_is_restored_afterwards(self):
        with guarded("session-start"):
            pass
        assert R.DEPTH_ENV not in os.environ
        assert R.CHAIN_ENV not in os.environ
        assert current_depth() == 0
        assert current_chain() == []

    def test_env_is_restored_on_exception(self):
        with pytest.raises(ValueError, match="boom"), guarded("prompt"):
            raise ValueError("boom")
        assert current_depth() == 0

    def test_sequential_hooks_each_run_once(self):
        with guarded("prompt"):
            pass
        with guarded("prompt"):
            pass  # a finished hook never wedges later hooks

    def test_malformed_depth_is_treated_as_zero(self, monkeypatch):
        monkeypatch.setenv(R.DEPTH_ENV, "not-a-number")
        with guarded("prompt"):
            assert current_depth() == 1


class TestRefusal:
    @staticmethod
    def _reenter(event: str) -> None:
        with guarded(event):
            pass  # pragma: no cover - refused before the body runs

    def test_self_triggering_hook_is_stopped(self):
        # The exact failure: a hook whose body fires its own event.
        with guarded("prompt"), pytest.raises(HookRecursionRefused) as excinfo:
            TestRefusal._reenter("prompt")
        assert excinfo.value.event == "prompt"
        assert "already in the hook chain" in excinfo.value.reason

    def test_nested_different_event_is_stopped(self):
        # A hook firing any other hook while one is running is nesting.
        with guarded("session-start"), pytest.raises(HookRecursionRefused) as excinfo:
            TestRefusal._reenter("pre-tool")
        assert "already running" in excinfo.value.reason
        assert excinfo.value.depth == 1

    def test_cross_process_chain_cycle_is_stopped(self, monkeypatch):
        # The host exec'd this hook as a child of another hook run: the
        # chain survives in the environment even though depth reset.
        monkeypatch.setenv(R.CHAIN_ENV, "session-start,prompt")
        with pytest.raises(HookRecursionRefused) as excinfo, guarded("prompt"):
            pass  # pragma: no cover - never reached
        assert excinfo.value.event == "prompt"

    def test_preset_depth_refuses(self, monkeypatch):
        # Simulates a child process inheriting a running hook's depth.
        monkeypatch.setenv(R.DEPTH_ENV, "1")
        monkeypatch.setenv(R.CHAIN_ENV, "other-event")
        with pytest.raises(HookRecursionRefused), guarded("prompt"):
            pass  # pragma: no cover - never reached

    def test_refusal_carries_evidence(self):
        with guarded("prompt"):
            try:
                with guarded("prompt"):
                    pass  # pragma: no cover
            except HookRecursionRefused as exc:
                message = describe_refusal(exc)
        assert "prompt" in message
        assert "chain=prompt -> prompt" in message
        assert "must never trigger its own event" in message

    def test_outer_hook_survives_inner_refusal(self):
        with guarded("prompt"), pytest.raises(HookRecursionRefused), guarded("prompt"):
            pass  # pragma: no cover
            # The outer hook continues and restores cleanly.
        assert current_depth() == 0


def _run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "awino.cli", *args],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


class TestCliRefusal:
    def test_recursive_hook_invocation_exits_3_with_clear_message(self, tmp_path):
        env = {**os.environ, R.DEPTH_ENV: "1", R.CHAIN_ENV: "prompt"}
        result = _run_cli(["hook", "prompt"], env=env)
        assert result.returncode == 3, result.stderr + result.stdout
        combined = result.stdout + result.stderr
        assert "recurs" in combined.lower()

    def test_refusal_happens_before_any_project_setup(self, tmp_path):
        # With the guard tripped, the hook must not touch the project at
        # all: no workspace discovery, no onboarding, no output besides
        # the refusal.
        env = {**os.environ, R.DEPTH_ENV: "1", R.CHAIN_ENV: "other"}
        result = _run_cli(["hook", "session-start"], env=env)
        assert result.returncode == 3
        assert "PROJECT_SETUP_REQUIRED" not in result.stdout
