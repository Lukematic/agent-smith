"""Self-healing must diagnose real failures, apply real remedies, and know when to
stop trying.

Grounded in chapters/6-harnesses/5-harness-engineering.md (structural fix once,
never rediscovered) and chapters/9-mental-models/8-loop-engineering.md (verification
bounds how far a loop may run unattended). Every test here traces to a real failure
this project hit: an unauthenticated spawn reported only ``FAILED`` with no
diagnosis, and an unhealable failure burned three attempts before this file existed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.healing import (
    MAX_HEAL_ATTEMPTS,
    Failure,
    diagnose,
    heal_stale_lock,
    run_with_healing,
)


class TestDiagnosis:
    """A wrong diagnosis sends effort at the wrong surface, which is worse than none."""

    def test_auth_missing_is_recognised(self) -> None:
        # The exact output that started this: a spawn failed with only "FAILED".
        d = diagnose("Not logged in. Please run /login")
        assert d.failure is Failure.AUTH_MISSING
        assert not d.self_healable
        assert "your" in d.human_action or "credentials" in d.human_action

    def test_hardlink_refused_is_healable(self) -> None:
        d = diagnose("failed to hardlink file from ... incompatible hardlinks (os error 396)")
        assert d.failure is Failure.HARDLINK_REFUSED
        assert d.self_healable

    def test_module_not_found_maps_to_env_unsynced(self) -> None:
        d = diagnose("ModuleNotFoundError: No module named 'coverage'")
        assert d.failure is Failure.ENV_UNSYNCED
        assert d.self_healable

    def test_rate_limit_is_not_self_healable(self) -> None:
        # Waiting is the only remedy, and Smith does not sleep on your behalf.
        d = diagnose("429 Too Many Requests")
        assert d.failure is Failure.RATE_LIMITED
        assert not d.self_healable

    def test_missing_test_target_needs_a_decision(self) -> None:
        d = diagnose("ERROR: file or directory not found: tests/does_not_exist.py")
        assert d.failure is Failure.NO_TEST_TARGET
        assert not d.self_healable

    def test_unrecognised_output_is_unknown_not_guessed(self) -> None:
        d = diagnose("some completely novel error string nobody has seen before xyz123")
        assert d.failure is Failure.UNKNOWN
        assert not d.self_healable
        assert "xyz123" in d.evidence or "novel" in d.evidence

    def test_matching_is_case_insensitive(self) -> None:
        assert diagnose("NOT LOGGED IN").failure is Failure.AUTH_MISSING

    def test_specific_signature_wins_over_generic_words(self) -> None:
        # "not found" is generic enough to appear in many messages; the specific
        # phrase must be checked first or it gets swallowed.
        d = diagnose("command not found: cowsay")
        assert d.failure is Failure.CLI_MISSING

    def test_every_signature_with_a_remedy_has_no_human_action_text(self) -> None:
        # A self-healable diagnosis should not also demand a human decision; that
        # would make its own self_healable flag meaningless.
        from smith.healing import SIGNATURES

        for failure, _patterns, _desc, remedy, human in SIGNATURES:
            if remedy is not None:
                assert human == "", f"{failure} has a remedy but also a human_action"

    def test_report_format_differs_for_healable_vs_not(self) -> None:
        healable = diagnose("ModuleNotFoundError: No module named 'x'")
        blocked = diagnose("Not logged in")
        assert "healing by" in healable.report
        assert "needs you" in blocked.report


class TestStaleLockRemedy:
    def test_removes_lock_files(self, tmp_path: Path) -> None:
        (tmp_path / "uv.lock.tmp").write_text("", encoding="utf-8")
        seeds = tmp_path / ".seeds"
        seeds.mkdir()
        (seeds / "a.lock").write_text("", encoding="utf-8")

        healed, _detail = heal_stale_lock(tmp_path)
        assert healed
        assert not (seeds / "a.lock").exists()

    def test_reports_false_when_nothing_to_remove(self, tmp_path: Path) -> None:
        # A remedy that changes nothing must say so, not claim success.
        healed, detail = heal_stale_lock(tmp_path)
        assert not healed
        assert "no stale locks" in detail

    def test_idempotent_on_second_call(self, tmp_path: Path) -> None:
        (tmp_path / ".seeds").mkdir()
        (tmp_path / ".seeds" / "a.lock").write_text("", encoding="utf-8")
        heal_stale_lock(tmp_path)
        healed_again, _ = heal_stale_lock(tmp_path)
        assert not healed_again  # nothing left to remove, and it does not error


class TestRunWithHealing:
    """The loop must know the difference between fixed, blocked, and hopeless."""

    def test_a_command_that_already_succeeds_needs_no_healing(self, tmp_path: Path) -> None:
        run = run_with_healing('python -c "print(1)"', tmp_path)
        assert run.succeeded
        assert run.attempts == []
        assert run.summary() == "succeeded on the first attempt"

    def test_unhealable_failure_stops_on_the_first_attempt(self, tmp_path: Path) -> None:
        # Retrying a credential problem three times produces three identical
        # failures and a bill for nothing.
        run = run_with_healing('python -c "import sys; sys.exit(1)"', tmp_path)
        # This particular failure is UNKNOWN, which is also not self-healable.
        assert len(run.attempts) == 1
        assert run.blocked_on_human

    def test_recurring_failure_after_its_own_remedy_stops_early(self, tmp_path: Path) -> None:
        # The real bug this file exists to prevent: a genuinely missing module
        # cannot be fixed by re-syncing, so re-syncing a second and third time
        # only spends money proving that again. Whether the remedy itself failed
        # to apply (e.g. no network for uv sync) or ran and the failure recurred,
        # the loop must stop before the ceiling and say something other than the
        # generic "did not succeed".
        run = run_with_healing(
            'python -c "import nonexistent_module_xyz_probe"', tmp_path, max_attempts=3
        )
        assert not run.succeeded
        assert len(run.attempts) < MAX_HEAL_ATTEMPTS
        assert run.summary() != "did not succeed"

    def test_never_exceeds_max_attempts(self, tmp_path: Path) -> None:
        run = run_with_healing(
            'python -c "import nonexistent_module_xyz_probe"', tmp_path, max_attempts=2
        )
        assert len(run.attempts) <= 2

    def test_final_output_is_captured_for_the_human(self, tmp_path: Path) -> None:
        run = run_with_healing('python -c "import sys; sys.exit(1)"', tmp_path)
        assert run.final_output != "" or run.attempts

    def test_summary_names_what_was_healed_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from smith import healing as healing_module

        calls = {"n": 0}

        def fake_run(cmd, shell, capture_output, text, encoding, errors, cwd, timeout, check):
            calls["n"] += 1

            class Result:
                returncode = 1 if calls["n"] == 1 else 0
                stdout = "ModuleNotFoundError: No module named 'x'" if calls["n"] == 1 else ""
                stderr = ""

            return Result()

        def fake_remedy(_project):
            return True, "synced"

        monkeypatch.setattr(healing_module.subprocess, "run", fake_run)
        monkeypatch.setattr(healing_module, "heal_env_unsynced", fake_remedy)
        # Rebuild SIGNATURES entry pointing at the patched remedy for this test only.
        patched = tuple(
            (f, pats, desc, fake_remedy if f is Failure.ENV_UNSYNCED else rem, human)
            for f, pats, desc, rem, human in healing_module.SIGNATURES
        )
        monkeypatch.setattr(healing_module, "SIGNATURES", patched)

        run = run_with_healing("anything", tmp_path)
        assert run.succeeded
        assert "ENV_UNSYNCED" in run.summary()
