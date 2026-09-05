"""Regression for a real, costly incident: the agent re-asked a question the
user had already answered, forgot an explicit instruction mid-session, and
produced wrong output followed by an apology instead of a fix. A three-expert
review converged on one root cause: TaskClass.QUESTION opens zero gates, so
ordinary conversation never touched any durable state - everything lived only
as tokens in the model's own context. This module is the minimum fix: an
always-on, session-scoped log independent of whether a gate run is open.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from smith import session_log


class TestNormalization:
    def test_case_and_punctuation_do_not_prevent_a_match(self) -> None:
        assert session_log._normalize("Never Rename the config keys!") == session_log._normalize(
            "never rename config keys"
        )

    def test_stopwords_are_stripped(self) -> None:
        assert "the" not in session_log._normalize("what is the deadline for this").split()


class TestAppendAndReadBack:
    def test_turns_increment_monotonically(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", "first thing")
        second = session_log.append(tmp_path, "s1", "user_turn", "second thing")
        assert second.turn == 2

    def test_separate_sessions_do_not_share_turn_numbers(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", "a")
        first_in_other_session = session_log.append(tmp_path, "s2", "user_turn", "b")
        assert first_in_other_session.turn == 1


class TestDuplicateDetection:
    """The exact mechanism this incident needed: a deterministic check the
    agent cannot skip by simply not noticing, the same asymmetry the Ledger
    already uses elsewhere (agent supplies content, harness computes fact)."""

    def test_a_reworded_repeat_of_a_real_instruction_is_detected(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", "never rename the config keys")
        found = session_log.find_duplicate_question(
            tmp_path, "s1", "please don't rename config keys"
        )
        assert found is not None
        assert found.turn == 1

    def test_the_exact_reported_incident_text_is_detected_verbatim(self, tmp_path: Path) -> None:
        text = "never rename the config keys in this project"
        session_log.append(tmp_path, "s1", "user_turn", text)
        found = session_log.find_duplicate_question(tmp_path, "s1", text)
        assert found is not None

    def test_a_genuinely_different_turn_is_not_flagged(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", "never rename the config keys")
        found = session_log.find_duplicate_question(
            tmp_path, "s1", "what testing framework does this project use"
        )
        assert found is None

    def test_an_empty_session_never_flags_the_first_turn(self, tmp_path: Path) -> None:
        found = session_log.find_duplicate_question(tmp_path, "s1", "anything at all")
        assert found is None

    def test_matching_against_a_different_session_is_not_a_false_positive(
        self, tmp_path: Path
    ) -> None:
        session_log.append(tmp_path, "s1", "user_turn", "never rename the config keys")
        found = session_log.find_duplicate_question(tmp_path, "s2", "never rename the config keys")
        assert found is None

    def test_default_kind_only_matches_user_turns(self, tmp_path: Path) -> None:
        # A correction with the same words must not be mistaken for the
        # human repeating themselves.
        session_log.append(tmp_path, "s1", "correction", "never rename the config keys")
        found = session_log.find_duplicate_question(tmp_path, "s1", "never rename the config keys")
        assert found is None

    def test_agent_question_kind_detects_the_agent_reasking_its_own_question(
        self, tmp_path: Path
    ) -> None:
        # Regression for a real bug found live: find_duplicate_question was
        # hardcoded to only check user_turn entries, so 'awino ask' checking
        # agent_question entries with the default kind never matched
        # anything - the exact reworded database question from the incident
        # this command exists to prevent went undetected until caught live.
        session_log.append(
            tmp_path, "s1", "agent_question", "which database should this project use?"
        )
        found = session_log.find_duplicate_question(
            tmp_path,
            "s1",
            "what database should we use for this project?",
            kind="agent_question",
        )
        assert found is not None

    def test_agent_question_kind_does_not_match_a_user_turn(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", "which database should this project use?")
        found = session_log.find_duplicate_question(
            tmp_path, "s1", "which database should this project use?", kind="agent_question"
        )
        assert found is None


class TestCorrections:
    def test_corrections_are_returned_most_recent_first(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "correction", "first correction")
        session_log.append(tmp_path, "s1", "correction", "second correction")
        found = session_log.corrections(tmp_path, "s1")
        assert [c.text for c in found] == ["second correction", "first correction"]

    def test_user_turns_are_not_mistaken_for_corrections(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", "just a normal question")
        assert session_log.corrections(tmp_path, "s1") == []


class TestUnresolvedQuestions:
    def test_an_agent_question_with_no_resolved_by_turn_is_unresolved(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "agent_question", "what date format do you want?")
        found = session_log.unresolved_questions(tmp_path, "s1")
        assert len(found) == 1


def _run_hook(project: Path, event: str, payload: dict) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AWINO_PROJECT"] = str(project)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", "hook", event],
        cwd=project,
        env=env,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def _onboard(project: Path, **fields: str) -> None:
    env = os.environ.copy()
    env["AWINO_PROJECT"] = str(project)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    args = [sys.executable, "-m", "smith.cli", "onboard"]
    for key, value in fields.items():
        args += ["--set", f"{key}={value}"]
    args.append("--confirm")
    subprocess.run(args, cwd=project, env=env, capture_output=True, text=True, timeout=30)


class TestLiveIncidentReplay:
    """Real subprocess replay of the exact reported failure: the user gives
    an instruction, then later says something that would make the agent ask
    the user to repeat it. This must now be mechanically detectable."""

    def test_repeating_the_same_instruction_is_flagged_in_the_hook_output(
        self, tmp_path: Path
    ) -> None:
        _onboard(
            tmp_path,
            primary_user="tester",
            goals="ship correctly",
            tenets="never rename config keys",
            success_metric="user does not repeat instructions",
        )
        session_id = "sess-incident-replay"
        _run_hook(tmp_path, "session-start", {"session_id": session_id})

        first = _run_hook(
            tmp_path,
            "prompt",
            {"session_id": session_id, "prompt": "never rename the config keys in this project"},
        )
        assert first.returncode == 0, first.stdout + first.stderr
        assert "already have" not in first.stdout

        second = _run_hook(
            tmp_path,
            "prompt",
            {"session_id": session_id, "prompt": "never rename the config keys in this project"},
        )
        assert second.returncode == 0, second.stdout + second.stderr
        assert "this looks similar to what you said at turn 1" in second.stdout

        unrelated = _run_hook(
            tmp_path,
            "prompt",
            {"session_id": session_id, "prompt": "what testing framework does this project use"},
        )
        assert "this looks similar" not in unrelated.stdout

    def test_note_and_session_log_surface_a_real_correction(self, tmp_path: Path) -> None:
        env = os.environ.copy()
        env["AWINO_PROJECT"] = str(tmp_path)
        env.pop("VIRTUAL_ENV", None)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        noted = subprocess.run(
            [
                sys.executable,
                "-m",
                "smith.cli",
                "note",
                "you renamed config_key to configKey after I told you not to",
                "--as",
                "correction",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert noted.returncode == 0, noted.stdout + noted.stderr

        shown = subprocess.run(
            [sys.executable, "-m", "smith.cli", "session-log", "--session", "unknown"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert shown.returncode == 0, shown.stdout + shown.stderr
        assert "CORRECTIONS  1" in shown.stdout
        assert "renamed config_key to configKey" in shown.stdout


class TestConcurrentAppendsGetDistinctTurns:
    """2750: two writers appending at once must never share a turn number."""

    def test_parallel_appends_yield_unique_monotonic_turns(self, tmp_path: Path) -> None:
        import concurrent.futures as cf

        from smith import session_log

        def one(i: int) -> int:
            return session_log.append(tmp_path, "sess", "user_turn", f"turn {i}").turn

        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            turns = sorted(pool.map(one, range(40)))
        assert turns == list(range(1, 41))
        rows = session_log._read_all(session_log.log_path(tmp_path, "sess"))
        assert len(rows) == 40
