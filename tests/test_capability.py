"""Self-knowledge must be probed, not declared.

This module exists because of a real failure: the persona claimed Smith "spawns
scoped subagents" while no spawn code existed, and nothing caught it. Prose
describing a capability is indistinguishable from prose describing an aspiration.

The load-bearing test here is ``test_probe_catches_a_missing_capability``. If that
ever passes trivially, the whole mechanism is decoration.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from smith import capability
from smith.capability import State
from smith.paths import SmithPaths
from smith.spawn import (
    Assignment,
    Role,
    Runner,
    SpawnResult,
    check_ownership,
    detect_runner,
    plan_waves,
)


@pytest.fixture
def paths() -> SmithPaths:
    return SmithPaths.discover()


class TestProbesAreReal:
    """A probe that cannot fail is not a probe."""

    def test_probe_catches_a_missing_capability(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The exact scenario that shipped a lie: no agent CLI, but the document
        # says Smith spawns subagents.
        real = shutil.which
        monkeypatch.setattr(
            shutil, "which", lambda n: None if n in {"claude", "goose", "codex"} else real(n)
        )
        cap = capability.probe_spawn(SmithPaths.discover())
        assert cap.state is State.ABSENT
        assert not cap.state.claimable
        assert "cannot spawn" in cap.honest_claim

    def test_probe_confirms_a_present_capability(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda n: f"/usr/bin/{n}")
        cap = capability.probe_spawn(SmithPaths.discover())
        assert cap.state is State.REAL

    def test_missing_registry_reports_absent(self, tmp_path: Path) -> None:
        blank = SmithPaths(root=tmp_path)
        blank.ensure_scaffold()
        assert capability.probe_knowledge(blank).state is State.ABSENT

    def test_empty_ledger_is_degraded_not_real(self, tmp_path: Path) -> None:
        # An agent with no recorded lessons must not claim experience.
        blank = SmithPaths(root=tmp_path)
        blank.ensure_scaffold()
        blank.lessons.write_text("# Lessons\n\nnothing yet\n", encoding="utf-8")
        cap = capability.probe_memory(blank)
        assert cap.state is State.DEGRADED
        assert "do not claim experience" in cap.limit

    def test_a_crashing_probe_reports_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A probe that raises proves the capability is not dependable, which is
        # information. Swallowing it would recreate the original bug.
        def explode(_paths: SmithPaths):
            raise RuntimeError("boom")

        monkeypatch.setattr(capability, "PROBES", (explode,))
        caps = capability.assess()
        assert caps[0].state is State.ABSENT
        assert "probe failed" in caps[0].detail


class TestHonestLimits:
    """Degraded capabilities must carry their limit into the claim."""

    def test_every_degraded_capability_states_a_limit(self, paths: SmithPaths) -> None:
        for cap in capability.assess(paths):
            if cap.state is State.DEGRADED:
                assert cap.limit, f"{cap.name} is degraded with no stated limit"

    def test_degraded_claim_includes_the_limit(self, paths: SmithPaths) -> None:
        for cap in capability.assess(paths):
            if cap.state is State.DEGRADED:
                assert "but" in cap.honest_claim

    def test_self_improvement_does_not_overclaim(self, paths: SmithPaths) -> None:
        # Refreshing an index is version tracking, not learning. Calling it
        # learning would be the wishful labelling this file guards against.
        cap = capability.probe_self_improvement(paths)
        assert cap.state is State.DEGRADED
        assert "does not discover" in cap.limit

    def test_autonomy_does_not_claim_loop_level(self, paths: SmithPaths) -> None:
        cap = capability.probe_autonomy(paths)
        assert cap.state is State.DEGRADED
        assert "no scheduler" in cap.limit

    def test_diagrams_are_honestly_absent(self, paths: SmithPaths) -> None:
        assert capability.probe_diagrams(paths).state is State.ABSENT

    def test_summary_counts_every_capability(self, paths: SmithPaths) -> None:
        caps = capability.assess(paths)
        assert sum(capability.summary(caps).values()) == len(caps)

    def test_every_documented_claim_has_a_probe(self) -> None:
        # A claim with no probe is a claim nothing can falsify.
        assert capability.CLAIMED_IN_DOCS
        for claim, probe in capability.CLAIMED_IN_DOCS.items():
            assert callable(probe), f"{claim} has no probe"


class TestDelegationRefuses:
    """The refusals are the feature."""

    def _builder(self, agent_id: str, scope: list[str], **kw) -> Assignment:
        return Assignment(
            agent_id=agent_id,
            role=Role.BUILDER,
            objective="do the thing",
            file_scope=scope,
            verification="pytest -q",
            **kw,
        )

    def test_overlapping_file_ownership_is_caught(self) -> None:
        # The single most destructive delegation failure: two agents editing one
        # file overwrite each other with no error.
        conflicts = check_ownership(
            [self._builder("a", ["src/x.py"]), self._builder("b", ["src/x.py", "src/y.py"])]
        )
        assert len(conflicts) == 1
        assert "src/x.py" in conflicts[0]

    def test_disjoint_ownership_passes(self) -> None:
        assert (
            check_ownership([self._builder("a", ["src/x.py"]), self._builder("b", ["src/y.py"])])
            == []
        )

    def test_separator_style_does_not_hide_a_conflict(self) -> None:
        conflicts = check_ownership(
            [self._builder("a", ["src\\x.py"]), self._builder("b", ["src/x.py"])]
        )
        assert conflicts

    def test_assignment_without_verification_is_refused(self) -> None:
        bad = Assignment(agent_id="a", role=Role.BUILDER, objective="x", file_scope=["y.py"])
        assert any("verification" in p for p in bad.problems())

    def test_writing_role_without_scope_is_refused(self) -> None:
        bad = Assignment(agent_id="a", role=Role.BUILDER, objective="x", verification="pytest")
        assert any("file scope" in p for p in bad.problems())

    def test_read_only_role_with_scope_is_refused(self) -> None:
        # A reviewer that can write is reviewing its own work.
        bad = Assignment(
            agent_id="a",
            role=Role.REVIEWER,
            objective="x",
            file_scope=["y.py"],
            verification="pytest",
        )
        assert any("read-only" in p for p in bad.problems())

    def test_reviewers_and_scouts_are_read_only(self) -> None:
        assert Role.REVIEWER.read_only
        assert Role.SCOUT.read_only
        assert not Role.BUILDER.read_only

    def test_valid_assignment_has_no_problems(self) -> None:
        assert self._builder("a", ["src/x.py"]).problems() == []


class TestWavePlanning:
    def test_independent_work_lands_in_one_wave(self) -> None:
        # Sequential spawning of independent work is FALSE_PARALLELISM.
        waves = plan_waves(
            [
                Assignment("a", Role.BUILDER, "x", ["a.py"], verification="t"),
                Assignment("b", Role.BUILDER, "y", ["b.py"], verification="t"),
            ]
        )
        assert len(waves) == 1

    def test_dependencies_create_ordered_waves(self) -> None:
        waves = plan_waves(
            [
                Assignment(
                    "build", Role.BUILDER, "x", ["a.py"], verification="t", depends_on=["design"]
                ),
                Assignment("design", Role.SCOUT, "y", verification="t"),
            ]
        )
        assert [a.agent_id for a in waves[0]] == ["design"]
        assert [a.agent_id for a in waves[1]] == ["build"]

    def test_a_cycle_terminates_instead_of_looping(self) -> None:
        waves = plan_waves(
            [
                Assignment("a", Role.BUILDER, "x", ["a.py"], verification="t", depends_on=["b"]),
                Assignment("b", Role.BUILDER, "y", ["b.py"], verification="t", depends_on=["a"]),
            ]
        )
        assert waves


class TestNoBlindTrust:
    def test_a_completion_claim_alone_is_not_trustworthy(self) -> None:
        # This is the difference between delegation and hoping.
        result = SpawnResult("a", "CLAIMED", 0, 10, "", claimed_complete=True)
        assert not result.trustworthy

    def test_only_independent_verification_earns_trust(self) -> None:
        result = SpawnResult("a", "CLAIMED", 0, 10, "", claimed_complete=True)
        result.verified = True
        assert result.trustworthy

    def test_failed_verification_is_not_trustworthy(self) -> None:
        result = SpawnResult("a", "CLAIMED", 0, 10, "", claimed_complete=True)
        result.verified = False
        assert not result.trustworthy

    def test_nesting_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Subagents are leaf nodes. Runaway delegation is expensive and hard to see.
        monkeypatch.setenv("SMITH_SPAWN_DEPTH", "1")
        from smith.spawn import spawn_one

        result = spawn_one(
            Assignment("a", Role.BUILDER, "x", ["a.py"], verification="t"),
            tmp_path,
            tmp_path,
            Runner.CLAUDE,
        )
        assert result.outcome == "REFUSED"
        assert "nesting" in result.output_tail


class TestRunnerDetection:
    def test_missing_runner_is_reported_not_assumed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _n: None)
        runner, reason = detect_runner()
        assert runner is Runner.NONE
        assert "no agent CLI" in reason

    def test_read_only_roles_get_restricted_tools(self, tmp_path: Path) -> None:
        command = Runner.CLAUDE.command(tmp_path / "p.md", read_only=True)
        assert "--allowedTools" in command

    def test_writing_roles_are_unrestricted(self, tmp_path: Path) -> None:
        command = Runner.CLAUDE.command(tmp_path / "p.md", read_only=False)
        assert "--allowedTools" not in command


class TestAssignmentPrompt:
    def test_prompt_is_self_contained(self, tmp_path: Path) -> None:
        # A subagent inherits no conversation, so anything omitted is absent.
        rendered = Assignment(
            "builder-1",
            Role.BUILDER,
            "add retry logic",
            ["src/client.py"],
            verification="pytest -q",
        ).render(tmp_path)
        assert "add retry logic" in rendered
        assert "src/client.py" in rendered
        assert "pytest -q" in rendered
        assert "builder-1 COMPLETE" in rendered
        assert "leaf node" in rendered
        assert "FILE_SCOPE_VIOLATION" in rendered

    def test_prompt_forbids_weakening_tests(self, tmp_path: Path) -> None:
        rendered = Assignment("a", Role.BUILDER, "x", ["y.py"], verification="pytest").render(
            tmp_path
        )
        assert "Do not modify tests to make them pass" in rendered


class TestClaimsGate:
    """A documented capability with no code must fail the ship gate.

    This is the check that was missing. The persona claimed Smith spawns subagents
    while no spawn code existed, and every gate passed.
    """

    def test_gate_blocks_on_an_unsupported_claim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from smith import health

        real = shutil.which
        monkeypatch.setattr(
            shutil, "which", lambda n: None if n in {"claude", "goose", "codex"} else real(n)
        )
        result = health.check_capability_claims(SmithPaths.discover())
        assert result.blocking
        assert "spawns scoped subagents" in result.detail
        assert result.remedy

    def test_gate_passes_when_every_claim_is_backed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from smith import health
        from smith.health import Health

        monkeypatch.setattr(shutil, "which", lambda n: f"/usr/bin/{n}")
        result = health.check_capability_claims(SmithPaths.discover())
        assert not result.blocking
        assert result.health in {Health.OK, Health.WARN}

    def test_gate_is_registered_in_the_suite(self) -> None:
        # A gate that exists but never runs is not a gate.
        from smith import health

        assert health.check_capability_claims in health.FAST_CHECKS
