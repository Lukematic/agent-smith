"""Mental models must change decisions, or they are decoration.

Each test asserts that a model produces a *different* recommendation under
different conditions. A model that returns the same answer regardless of input
is a document pretending to be a function.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smith.enforce import Evidence, Gate, TaskClass
from smith.models import (
    AntiPattern,
    Autonomy,
    Constraint,
    Rung,
    assess_verifier,
    audit_pit_of_success,
    build_plan,
    detect_anti_patterns,
    detect_rung,
    locate_constraint,
)


def evidence(
    gate: Gate, *, passed: bool = True, attested: bool = False, attempt: int = 1
) -> Evidence:
    return Evidence(
        gate=str(gate),
        command=("ATTEST a note" if attested else "uv run pytest"),
        exit_code=0 if passed else 1,
        output_hash="abc123",
        output_head="",
        duration_ms=1,
        recorded_at=datetime.now(UTC).isoformat(),
        attempt=attempt,
    )


def code_change_evidence(*, executed: bool) -> list[Evidence]:
    """Every code-change gate satisfied, either executed or merely attested."""
    return [
        evidence(Gate.PLANNED, attested=True),
        evidence(Gate.TESTED, attested=not executed),
        evidence(Gate.LINTED, attested=not executed),
        evidence(Gate.TESTS_NOT_WEAKENED, attested=not executed),
        evidence(Gate.SCOPE_RESPECTED, attested=not executed),
    ]


class TestLeverageLadder:
    """The common error is treating a higher-rung problem at a lower rung."""

    @pytest.mark.parametrize(
        ("request_text", "expected"),
        [
            ("rename this variable", Rung.PROMPT),
            ("the context window keeps overflowing", Rung.CONTEXT),
            ("the agent keeps writing to the wrong directory every time", Rung.HARNESS),
            ("every night find and fix flaky tests without me", Rung.LOOP),
            ("roll this lint config out across all our repos", Rung.FACTORY),
        ],
    )
    def test_rung_detection(self, request_text: str, expected: Rung) -> None:
        assert detect_rung(request_text).actual is expected

    def test_repeated_behaviour_is_a_harness_problem_not_a_prompt_problem(self) -> None:
        # This is the whole point: "it keeps doing X" is an environment defect.
        verdict = detect_rung("it always forgets to run the linter")
        assert verdict.actual is Rung.HARNESS
        assert verdict.misaligned
        assert "environment" in verdict.advice

    def test_single_turn_work_is_not_misaligned(self) -> None:
        assert not detect_rung("add a docstring to this function").misaligned

    def test_ladder_is_ordered_by_leverage(self) -> None:
        assert Rung.PROMPT < Rung.CONTEXT < Rung.HARNESS < Rung.LOOP < Rung.FACTORY

    def test_every_rung_names_its_artifact_and_position(self) -> None:
        for rung in Rung:
            assert rung.authored_artifact
            assert rung.human_position


class TestVerifierStrength:
    """Verification is the binding constraint on autonomy. So measure it."""

    def test_no_evidence_means_supervised_only(self) -> None:
        verdict = assess_verifier(TaskClass.CODE_CHANGE, [])
        assert verdict.max_autonomy is Autonomy.SUPERVISED
        assert verdict.missing

    def test_attestations_alone_cannot_close_a_loop(self) -> None:
        # An agent grading its own work is an open loop regardless of how many
        # boxes it ticks.
        verdict = assess_verifier(TaskClass.CODE_CHANGE, code_change_evidence(executed=False))
        assert verdict.max_autonomy is Autonomy.SUPERVISED
        assert verdict.open_loop
        assert "grading its own work" in " ".join(verdict.reasons)

    def test_mixed_evidence_earns_checkpointed_not_unattended(self) -> None:
        mixed = [
            evidence(Gate.PLANNED, attested=True),
            evidence(Gate.TESTED),
            evidence(Gate.LINTED),
            evidence(Gate.TESTS_NOT_WEAKENED, attested=True),
            evidence(Gate.SCOPE_RESPECTED, attested=True),
        ]
        verdict = assess_verifier(TaskClass.CODE_CHANGE, mixed)
        assert verdict.max_autonomy is Autonomy.CHECKPOINTED
        assert not verdict.open_loop

    def test_all_executed_with_independent_check_earns_unattended(self) -> None:
        full = [
            evidence(Gate.PLANNED, attested=True),
            evidence(Gate.TESTED),
            evidence(Gate.LINTED),
            evidence(Gate.TESTS_NOT_WEAKENED),
            evidence(Gate.SCOPE_RESPECTED),
        ]
        verdict = assess_verifier(TaskClass.CODE_CHANGE, full)
        assert verdict.max_autonomy is Autonomy.UNATTENDED
        assert verdict.has_independent_check

    def test_failing_gate_drops_to_supervised(self) -> None:
        broken = code_change_evidence(executed=True)
        broken[1] = evidence(Gate.TESTED, passed=False)
        assert assess_verifier(TaskClass.CODE_CHANGE, broken).max_autonomy is Autonomy.SUPERVISED

    def test_latest_attempt_wins(self) -> None:
        # A gate that failed then passed is satisfied. History is kept, not scored.
        history = [
            evidence(Gate.RESEARCHED, passed=False, attempt=1),
            evidence(Gate.RESEARCHED, passed=True, attempt=2),
        ]
        verdict = assess_verifier(TaskClass.RESEARCH, history)
        assert not verdict.missing
        assert verdict.executed == ["researched"]

    def test_research_can_never_run_unattended(self) -> None:
        # Research has no executable gate, so correctness rests on human
        # judgement. Capping it at supervised is the model working, not failing.
        history = [evidence(Gate.RESEARCHED)]
        verdict = assess_verifier(TaskClass.RESEARCH, history)
        assert verdict.max_autonomy is Autonomy.SUPERVISED
        assert "human judgement" in " ".join(verdict.reasons)

    def test_inherently_attested_gates_are_not_penalised(self) -> None:
        # A plan document either exists or it does not; no command judges it.
        # Penalising that attestation would cap every task forever.
        full = [
            evidence(Gate.PLANNED, attested=True),
            evidence(Gate.TESTED),
            evidence(Gate.LINTED),
            evidence(Gate.TESTS_NOT_WEAKENED),
            evidence(Gate.SCOPE_RESPECTED),
        ]
        verdict = assess_verifier(TaskClass.CODE_CHANGE, full)
        assert "planned" in verdict.attested
        assert verdict.weakly_attested == []
        assert verdict.max_autonomy is Autonomy.UNATTENDED

    def test_autonomy_levels_are_ordered(self) -> None:
        assert Autonomy.SUPERVISED < Autonomy.CHECKPOINTED < Autonomy.BOUNDED < Autonomy.UNATTENDED


class TestDesignAsBottleneck:
    """Implementation is the last thing to become scarce."""

    def test_undocumented_system_makes_understanding_the_constraint(self) -> None:
        verdict = locate_constraint(
            understood=False, interfaces_settled=True, units_disjoint=True, verifier_strong=True
        )
        assert verdict.constraint is Constraint.UNDERSTANDING
        assert not verdict.parallelisable

    def test_moving_interfaces_make_design_the_constraint(self) -> None:
        verdict = locate_constraint(
            understood=True, interfaces_settled=False, units_disjoint=True, verifier_strong=True
        )
        assert verdict.constraint is Constraint.DESIGN

    def test_weak_verifier_outranks_decomposition(self) -> None:
        # More parallel output with no objective check is more unverified output.
        verdict = locate_constraint(
            understood=True, interfaces_settled=True, units_disjoint=False, verifier_strong=False
        )
        assert verdict.constraint is Constraint.VERIFICATION

    def test_shared_files_make_decomposition_the_constraint(self) -> None:
        verdict = locate_constraint(
            understood=True, interfaces_settled=True, units_disjoint=False, verifier_strong=True
        )
        assert verdict.constraint is Constraint.DECOMPOSITION
        assert not verdict.parallelisable

    def test_everything_settled_means_just_build_it(self) -> None:
        verdict = locate_constraint(
            understood=True, interfaces_settled=True, units_disjoint=True, verifier_strong=True
        )
        assert verdict.constraint is Constraint.IMPLEMENTATION
        assert verdict.parallelisable

    def test_every_constraint_names_where_to_spend_effort(self) -> None:
        for constraint in Constraint:
            assert constraint.spend_effort_on


class TestPitOfSuccess:
    def test_divergent_paths_are_flagged(self) -> None:
        verdict = audit_pit_of_success("say done and move on", "run the gate and paste output")
        assert not verdict.aligned
        assert "decay" in verdict.advice

    def test_aligned_paths_hold_without_vigilance(self) -> None:
        verdict = audit_pit_of_success("run the gate", "run the gate")
        assert verdict.aligned
        assert "without vigilance" in verdict.advice

    def test_comparison_ignores_whitespace_and_case(self) -> None:
        assert audit_pit_of_success("Run  The Gate", "run the gate").aligned


class TestAntiPatterns:
    def test_weak_verifier_is_an_open_loop(self) -> None:
        weak = assess_verifier(TaskClass.CODE_CHANGE, code_change_evidence(executed=False))
        hits = detect_anti_patterns(
            verifier=weak, knowledge_age_days=1, stale_after_days=14, human_inspected_recently=True
        )
        assert AntiPattern.OPEN_LOOP in [h.pattern for h in hits]

    def test_stale_knowledge_is_rot(self) -> None:
        strong = assess_verifier(TaskClass.CODE_CHANGE, code_change_evidence(executed=True))
        hits = detect_anti_patterns(
            verifier=strong,
            knowledge_age_days=40,
            stale_after_days=14,
            human_inspected_recently=True,
        )
        assert AntiPattern.KNOWLEDGE_ROT in [h.pattern for h in hits]

    def test_uninspected_autonomy_is_cognitive_surrender(self) -> None:
        strong = assess_verifier(TaskClass.CODE_CHANGE, code_change_evidence(executed=True))
        hits = detect_anti_patterns(
            verifier=strong,
            knowledge_age_days=1,
            stale_after_days=14,
            human_inspected_recently=False,
        )
        assert strong.max_autonomy >= Autonomy.BOUNDED
        assert AntiPattern.COGNITIVE_SURRENDER in [h.pattern for h in hits]

    def test_supervised_work_is_not_cognitive_surrender(self) -> None:
        # A human is in every turn, so nothing has been surrendered.
        weak = assess_verifier(TaskClass.CODE_CHANGE, [])
        hits = detect_anti_patterns(
            verifier=weak, knowledge_age_days=1, stale_after_days=14, human_inspected_recently=False
        )
        assert AntiPattern.COGNITIVE_SURRENDER not in [h.pattern for h in hits]

    def test_cold_cache_is_not_rot(self) -> None:
        strong = assess_verifier(TaskClass.CODE_CHANGE, code_change_evidence(executed=True))
        hits = detect_anti_patterns(
            verifier=strong,
            knowledge_age_days=None,
            stale_after_days=14,
            human_inspected_recently=True,
        )
        assert AntiPattern.KNOWLEDGE_ROT not in [h.pattern for h in hits]

    def test_every_hit_carries_a_fix(self) -> None:
        weak = assess_verifier(TaskClass.CODE_CHANGE, [])
        hits = detect_anti_patterns(
            verifier=weak, knowledge_age_days=99, stale_after_days=14, human_inspected_recently=True
        )
        for hit in hits:
            assert hit.fix


class TestComposedPlan:
    """The question that started this: thirty agents, is that wise?"""

    def test_fan_out_refused_when_design_is_unsettled(self) -> None:
        plan = build_plan(
            "parallelise the migration across 30 agents",
            task_class=TaskClass.CODE_CHANGE,
            evidence=code_change_evidence(executed=True),
            understood=True,
            interfaces_settled=False,
            units_disjoint=True,
        )
        assert not plan.may_fan_out
        assert plan.constraint.constraint is Constraint.DESIGN

    def test_fan_out_refused_when_units_share_files(self) -> None:
        plan = build_plan(
            "run 30 agents over this",
            task_class=TaskClass.CODE_CHANGE,
            evidence=code_change_evidence(executed=True),
            understood=True,
            interfaces_settled=True,
            units_disjoint=False,
        )
        assert not plan.may_fan_out

    def test_fan_out_refused_when_verification_is_only_attested(self) -> None:
        # Design readiness alone is not enough: verification bounds parallelism.
        plan = build_plan(
            "run 30 agents over this",
            task_class=TaskClass.CODE_CHANGE,
            evidence=code_change_evidence(executed=False),
            understood=True,
            interfaces_settled=True,
            units_disjoint=True,
        )
        assert not plan.may_fan_out
        assert plan.verifier.open_loop

    def test_fan_out_allowed_when_everything_holds(self) -> None:
        plan = build_plan(
            "run 30 agents over this",
            task_class=TaskClass.CODE_CHANGE,
            evidence=code_change_evidence(executed=True),
            understood=True,
            interfaces_settled=True,
            units_disjoint=True,
        )
        assert plan.may_fan_out
        assert plan.verifier.max_autonomy is Autonomy.UNATTENDED

    def test_rung_misalignment_preempts_everything_else(self) -> None:
        # No point optimising execution of the wrong artifact.
        plan = build_plan(
            "every night automatically fix the flaky tests without me",
            task_class=TaskClass.CODE_CHANGE,
            evidence=code_change_evidence(executed=True),
            understood=True,
            interfaces_settled=True,
            units_disjoint=True,
        )
        assert plan.rung.misaligned
        assert plan.next_action.startswith("Reframe first")

    def test_anti_patterns_preempt_proceeding(self) -> None:
        # The request must be rung-aligned to isolate what this asserts: an earlier
        # fixture said "keep improving the code", which now correctly reads as a
        # harness problem, and the ladder short-circuited before anti-patterns.
        result = build_plan(
            "add a retry helper to the client",
            task_class=TaskClass.CODE_CHANGE,
            evidence=code_change_evidence(executed=False),
            understood=True,
            interfaces_settled=True,
            units_disjoint=True,
        )
        assert not result.rung.misaligned
        assert result.anti_patterns
        assert "Fix" in result.next_action

    def test_open_loop_does_not_preempt_understanding_before_execution(self) -> None:
        result = build_plan(
            "design a research MVP",
            task_class=TaskClass.CODE_CHANGE,
            evidence=[],
            understood=False,
            interfaces_settled=False,
            units_disjoint=False,
            pre_execution=True,
        )
        assert any(hit.pattern is AntiPattern.OPEN_LOOP for hit in result.anti_patterns)
        assert result.next_action.startswith("Constraint is understanding")


class TestRealWorldPhrasing:
    """Rung detection against phrasings taken from actual user requests.

    An earlier version listed harness verbs explicitly ("keeps doing", "keeps
    writing") and missed "keeps citing", which is the same failure needing the same
    structural fix. Every case here traces to a real request that was misrouted.
    """

    @pytest.mark.parametrize(
        ("request_text", "expected"),
        [
            # Instruction-defiance: the rule exists and is not binding, so wording
            # it harder cannot help. This is the definition of a harness problem.
            (
                "the QA agent keeps citing dates from filenames even though the prompt forbids it",
                Rung.HARNESS,
            ),
            ("the prompt says never use external knowledge but it still does", Rung.HARNESS),
            ("it ignores the rule about entity locking", Rung.HARNESS),
            ("the agent violates our instruction about citations", Rung.HARNESS),
            # Open-ended repetition, not an enumerated verb list.
            ("it keeps hallucinating company names", Rung.HARNESS),
            ("it keeps skipping the lint step", Rung.HARNESS),
            # Triggers, not repetition.
            ("automatically triage failures without me", Rung.LOOP),
            ("check the pipeline while I sleep", Rung.LOOP),
            # Context wins over the repetition signal it also contains.
            ("the context window keeps overflowing", Rung.CONTEXT),
            ("it forgets earlier instructions in long sessions", Rung.CONTEXT),
            # Ordinary bounded work stays at prompt.
            ("audit the anti-hallucination rules in prompts.py", Rung.PROMPT),
            ("write a docstring for this function", Rung.PROMPT),
        ],
    )
    def test_real_request_routes_correctly(self, request_text: str, expected: Rung) -> None:
        assert detect_rung(request_text).actual is expected

    def test_context_beats_harness_when_both_signals_present(self) -> None:
        # "keeps overflowing" is a repetition signal, but the fix is what the agent
        # sees, not the environment. A greedy harness rule would send effort to the
        # wrong surface.
        verdict = detect_rung("the context window keeps overflowing every time")
        assert verdict.actual is Rung.CONTEXT

    def test_harness_advice_names_the_environment(self) -> None:
        verdict = detect_rung("it keeps citing filenames even though the prompt forbids it")
        assert verdict.misaligned
        assert "environment" in verdict.reason
