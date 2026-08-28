"""Regression for a real bug found live: session_state used one global
session.json per project with no session_id component, so a second session
with a different id silently overwrote the first's run_id binding, and
enforce_one_task (the entire reason this module exists) could not tell two
genuinely concurrent sessions apart from one continuing session.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith import session_state


class TestPerSessionIsolation:
    def test_two_distinct_sessions_do_not_share_state(self, tmp_path: Path) -> None:
        session_state.start(tmp_path, "session-a")
        session_state.start(tmp_path, "session-b")
        session_state.bind_run(tmp_path, "run-1", enforce_one_task=True, session_id="session-a")
        session_state.bind_run(tmp_path, "run-2", enforce_one_task=True, session_id="session-b")

        state_a = session_state.load(tmp_path, "session-a")
        state_b = session_state.load(tmp_path, "session-b")
        assert state_a is not None
        assert state_b is not None
        assert state_a.run_id == "run-1"
        assert state_b.run_id == "run-2"

    def test_starting_a_second_session_does_not_erase_the_first_sessions_file(
        self, tmp_path: Path
    ) -> None:
        session_state.start(tmp_path, "session-a")
        session_state.bind_run(tmp_path, "run-1", enforce_one_task=True, session_id="session-a")
        session_state.start(tmp_path, "session-b")

        # The exact pre-fix bug: this used to be silently overwritten.
        state_a = session_state.load(tmp_path, "session-a")
        assert state_a is not None
        assert state_a.run_id == "run-1"

    def test_enforce_one_task_only_applies_within_one_sessions_own_history(
        self, tmp_path: Path
    ) -> None:
        session_state.start(tmp_path, "session-a")
        session_state.bind_run(tmp_path, "run-1", enforce_one_task=True, session_id="session-a")

        # A second, different session binding a different run must not be
        # blocked by the first session's run - they are not the same task.
        session_state.start(tmp_path, "session-b")
        session_state.bind_run(tmp_path, "run-2", enforce_one_task=True, session_id="session-b")

    def test_a_second_run_within_the_same_session_is_still_refused(self, tmp_path: Path) -> None:
        session_state.start(tmp_path, "session-a")
        session_state.bind_run(tmp_path, "run-1", enforce_one_task=True, session_id="session-a")
        with pytest.raises(RuntimeError, match="already served run run-1"):
            session_state.bind_run(tmp_path, "run-2", enforce_one_task=True, session_id="session-a")


class TestActiveSessionDefault:
    def test_load_with_no_session_id_returns_whichever_session_started_most_recently(
        self, tmp_path: Path
    ) -> None:
        session_state.start(tmp_path, "session-a")
        session_state.start(tmp_path, "session-b")
        current = session_state.load(tmp_path)
        assert current is not None
        assert current.session_id == "session-b"

    def test_restarting_an_earlier_session_makes_it_active_again(self, tmp_path: Path) -> None:
        session_state.start(tmp_path, "session-a")
        session_state.start(tmp_path, "session-b")
        session_state.start(tmp_path, "session-a")
        current = session_state.load(tmp_path)
        assert current is not None
        assert current.session_id == "session-a"

    def test_no_session_started_yet_returns_none(self, tmp_path: Path) -> None:
        assert session_state.load(tmp_path) is None

    def test_bind_run_with_no_session_id_binds_the_active_session(self, tmp_path: Path) -> None:
        session_state.start(tmp_path, "session-a")
        session_state.start(tmp_path, "session-b")
        session_state.bind_run(tmp_path, "run-1", enforce_one_task=True)
        state_b = session_state.load(tmp_path, "session-b")
        state_a = session_state.load(tmp_path, "session-a")
        assert state_b is not None and state_b.run_id == "run-1"
        assert state_a is not None and state_a.run_id is None
