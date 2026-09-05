"""Global pytest configuration and environment fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _configure_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure git operations always succeed in isolated test subprocesses on
    clean CI runners without relying on machine-global git config."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "A.W.I.N.O. Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "A.W.I.N.O. Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
