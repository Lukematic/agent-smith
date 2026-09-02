"""Precondition gate: the mechanical form of "something is off with you - go to
this floor first."

Before any capability is dispatched into, this checks two independent things:
project health (via health.run_all) and the active run's own state (via
Ledger.inspect_current). Both are pure reads - a failing project must be
detectable without mutating anything, because a caller may check preconditions
speculatively before committing to a dispatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.dispatch import preflight
from smith.enforce import Gate, Ledger, TaskClass
from smith.health import Health, Result
from smith.paths import SmithPaths


class _FakeHealth:
    """A deterministic stand-in for health.run_all so preflight tests do not
    depend on this machine's actual toolchain state."""

    def __init__(self, results: list[Result]) -> None:
        self._results = results
        self.calls = 0

    def __call__(self, paths: SmithPaths, *, fast: bool = False) -> list[Result]:
        del paths, fast
        self.calls += 1
        return self._results


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path)


@pytest.fixture
def paths(tmp_path: Path) -> SmithPaths:
    root = tmp_path / "smith-home"
    root.mkdir()
    (root / "plugin.json").write_text("{}", encoding="utf-8")
    (root / "knowledge").mkdir()
    return SmithPaths(root=root)


class TestHealthPrecondition:
    def test_a_refused_project_blocks_with_the_failing_check_named(
        self, ledger: Ledger, paths: SmithPaths
    ) -> None:
        failing = _FakeHealth(
            [
                Result("uv_env", Health.OK, "fine"),
                Result(
                    "clone_freshness",
                    Health.FAIL,
                    "uncommitted tracked changes",
                    remedy="git push and/or git pull",
                ),
            ]
        )
        result = preflight(ledger, paths, health_check=failing)

        assert result.ok is False
        assert result.reroute_to is None
        assert "clone_freshness" in result.detail
        assert "git push" in result.detail
        assert failing.calls == 1

    def test_a_healthy_project_with_no_active_run_passes(
        self, ledger: Ledger, paths: SmithPaths
    ) -> None:
        healthy = _FakeHealth([Result("uv_env", Health.OK, "fine")])
        result = preflight(ledger, paths, health_check=healthy)

        assert result.ok is True
        assert result.blockers == ()
        assert result.reroute_to is None

    def test_a_warning_alone_does_not_block(self, ledger: Ledger, paths: SmithPaths) -> None:
        warned = _FakeHealth(
            [Result("structure", Health.WARN, "2 regenerable artifact(s)", remedy="just clean")]
        )
        result = preflight(ledger, paths, health_check=warned)

        assert result.ok is True


class TestActiveRunPrecondition:
    def test_a_failing_gate_already_recorded_reroutes_to_debug_before_the_requested_skill(
        self, ledger: Ledger, paths: SmithPaths
    ) -> None:
        run = ledger.open(TaskClass.BUGFIX, "fix it")
        ledger.record(run.run_id, Gate.TESTED, 'python -c "raise SystemExit(1)"')
        healthy = _FakeHealth([Result("uv_env", Health.OK, "fine")])

        result = preflight(ledger, paths, health_check=healthy)

        assert result.ok is False
        assert result.reroute_to == "awino-debug"
        assert "tested" in result.detail.lower()

    def test_a_pending_checkpoint_decision_blocks_and_surfaces_the_decision(
        self, ledger: Ledger, paths: SmithPaths
    ) -> None:
        run = ledger.open(TaskClass.BUGFIX, "fix it")
        ledger.checkpoint(
            run.run_id,
            phase="blocked",
            summary="stuck",
            next_action="human decides",
            pending_decision="How should this proceed?",
            options=["retry", "abandon"],
        )
        healthy = _FakeHealth([Result("uv_env", Health.OK, "fine")])

        result = preflight(ledger, paths, health_check=healthy)

        assert result.ok is False
        assert result.reroute_to is None
        assert "How should this proceed?" in result.detail


class TestPreflightIsReadOnly:
    def test_preflight_performs_no_filesystem_write(
        self, ledger: Ledger, paths: SmithPaths, tmp_path: Path
    ) -> None:
        marker = tmp_path / "canary.txt"
        marker.write_text("untouched", encoding="utf-8")
        before = marker.stat().st_mtime_ns

        healthy = _FakeHealth([Result("uv_env", Health.OK, "fine")])
        preflight(ledger, paths, health_check=healthy)
        preflight(ledger, paths, health_check=healthy)

        assert marker.stat().st_mtime_ns == before
        assert marker.read_text(encoding="utf-8") == "untouched"
