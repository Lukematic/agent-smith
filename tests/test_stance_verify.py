"""WS-E: the stance critic (layer 3).

verify() is a deterministic keyword critic: a pass means no known violation
was found, not that the response is genuinely in the stance's spirit. These
tests pin both sides: genuinely compliant responses pass, sycophantic or
structurally wrong ones fail with the exact named rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import stance_verify
from awino.cli import app as cli_app

# ── fixtures ─────────────────────────────────────────────────────────────────

STEEL_MAN_COMPLIANT = """\
On the other hand, the strongest case against rewriting the loader is that
the current design has survived three years of production load, and every
failure mode is understood. The counterargument you should take most
seriously is the migration risk: a rewrite discards that hard-won knowledge.
My view is that you should only rewrite if you can name what the new design
proves that the old one cannot."""

STEEL_MAN_OWN_VIEW_FIRST = """\
My view is that we should rewrite the loader. On the other hand, the
strongest case against is the migration risk, and the counterargument is
that we lose three years of operational knowledge."""

STEEL_MAN_SYCOPHANTIC = """\
Great question! You're absolutely right that the loader is the bottleneck.
On the other hand, the opposite view says rewrites are risky. My take: go
ahead and rewrite it."""

TEACH_BACK_COMPLIANT = """\
Here is the mental map: a ledger is an append-only log, and every gate reads
from it instead of trusting claims. Three examples a beginner would
recognize: a bank statement, a lab notebook, a ship's log. The 20% that
carries 80% of the understanding is this: evidence is written once and never
edited. Now, can you explain it back to me in your own words?"""

TEACH_BACK_NO_QUESTION = """\
Here is the mental map: a ledger is an append-only log, and every gate reads
from it instead of trusting claims. Three examples a beginner would
recognize: a bank statement, a lab notebook, a ship's log. The 20% that
carries 80% of the understanding is this: evidence is written once and never
edited. That is the whole idea."""

ADVISOR_COMPLIANT = """\
I disagree: the rewrite is the risky path here. However, the current loader's
hot loop is the real bottleneck, so the risk in your approach is the
migration, not the design. The uncomfortable truth is that no rewrite ships
on schedule."""

ADVISOR_NO_DISAGREEMENT = """\
This is a solid plan. The loader rewrite will simplify the codebase, the
team is on board, and the timeline looks achievable."""

ADVISOR_SYCOPHANTIC = """\
Great question! You're absolutely right, this plan is excellent."""


# ── verify(): steel-man ──────────────────────────────────────────────────────


def test_steel_man_compliant_passes() -> None:
    assert stance_verify.verify("steel-man", STEEL_MAN_COMPLIANT) == []


def test_steel_man_fails_when_own_view_comes_first() -> None:
    failures = stance_verify.verify("steel-man", STEEL_MAN_OWN_VIEW_FIRST)
    assert "opposing case must precede own view" in failures


def test_steel_man_fails_on_own_view_with_no_opposing_case() -> None:
    failures = stance_verify.verify("steel-man", "My take is simple: rewrite it.")
    assert "opposing case must precede own view" in failures


def test_steel_man_fails_on_validation_phrases() -> None:
    failures = stance_verify.verify("steel-man", STEEL_MAN_SYCOPHANTIC)
    assert "no validation phrases" in failures


# ── verify(): teach-back ─────────────────────────────────────────────────────


def test_teach_back_compliant_passes() -> None:
    assert stance_verify.verify("teach-back", TEACH_BACK_COMPLIANT) == []


def test_teach_back_fails_when_it_does_not_end_in_a_question() -> None:
    failures = stance_verify.verify("teach-back", TEACH_BACK_NO_QUESTION)
    assert "must end asking the human to explain it back" in failures


def test_teach_back_fails_on_validation_phrases() -> None:
    failures = stance_verify.verify(
        "teach-back", "Great question! Now can you explain it back to me?"
    )
    assert "no validation phrases" in failures


# ── verify(): advisor ────────────────────────────────────────────────────────


def test_advisor_compliant_passes() -> None:
    assert stance_verify.verify("advisor", ADVISOR_COMPLIANT) == []


def test_advisor_fails_without_a_labeled_disagreement() -> None:
    failures = stance_verify.verify("advisor", ADVISOR_NO_DISAGREEMENT)
    assert "must contain a labeled disagreement" in failures


def test_advisor_fails_on_validation_phrases() -> None:
    failures = stance_verify.verify("advisor", ADVISOR_SYCOPHANTIC)
    assert "no validation phrases" in failures


# ── verify(): shared check only ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "stance", ["first-principles", "assumption-audit", "research-intake", "expert"]
)
def test_other_stances_apply_only_the_shared_check(stance: str) -> None:
    # Clean text passes; the full rules of these stances have no keyword
    # signature, so only the validation-phrase check is applied.
    assert stance_verify.verify(stance, "Here is the analysis you asked for.") == []
    failures = stance_verify.verify(
        stance, "Great question! You're absolutely right, excellent point."
    )
    assert failures == ["no validation phrases"]


def test_future_stance_falls_back_to_the_shared_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The safety net at the end of verify(): a stance not in the shared-only
    # set must not silently pass everything.
    monkeypatch.setattr(stance_verify, "_SHARED_CHECK_ONLY", frozenset())
    failures = stance_verify.verify("expert", "Great question, let me answer.")
    assert failures == ["no validation phrases"]


def test_unknown_stance_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown stance or thinking mode"):
        stance_verify.verify("hype-man", "Some text.")


# ── CLI: awino stance --verify --response ─────────────────────────────────────


@pytest.fixture()
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.delenv("AWINO_HOME", raising=False)
    monkeypatch.delenv("SMITH_HOME", raising=False)
    monkeypatch.delenv("AWINO_PROJECT", raising=False)
    monkeypatch.delenv("SMITH_PROJECT", raising=False)
    return CliRunner()


def _response_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "response.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_compliant_exits_zero(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    response = _response_file(tmp_path, ADVISOR_COMPLIANT)
    result = cli_runner.invoke(
        cli_app, ["stance", "--verify", "advisor", "--response", str(response)]
    )
    assert result.exit_code == 0, result.output
    assert "STANCE_VERIFY  advisor  compliant" in result.output


def test_cli_non_compliant_exits_one_with_one_line_per_failure(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    response = _response_file(tmp_path, STEEL_MAN_SYCOPHANTIC)
    result = cli_runner.invoke(
        cli_app, ["stance", "--verify", "steel-man", "--response", str(response)]
    )
    assert result.exit_code == 1, result.output
    assert "STANCE_VERIFY  steel-man  NON-COMPLIANT" in result.output
    assert "  - no validation phrases" in result.output


def test_cli_unknown_stance_refuses_with_exit_two(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    response = _response_file(tmp_path, "Some text.")
    result = cli_runner.invoke(
        cli_app, ["stance", "--verify", "hype-man", "--response", str(response)]
    )
    assert result.exit_code == 2, result.output
    assert "REFUSED" in result.output


def test_cli_missing_response_option_refuses_with_exit_two(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(cli_app, ["stance", "--verify", "advisor"])
    assert result.exit_code == 2, result.output
    assert "REFUSED" in result.output


def test_cli_unreadable_response_file_refuses_with_exit_two(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(
        cli_app,
        ["stance", "--verify", "advisor", "--response", str(tmp_path / "missing.txt")],
    )
    assert result.exit_code == 2, result.output
    assert "REFUSED" in result.output


def test_cli_existing_set_and_for_behavior_is_untouched(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(cli_app, ["stance", "--set", "advisor"])
    assert result.exit_code == 0, result.output
    assert "STANCE_DEFAULT  advisor" in result.output
    result = cli_runner.invoke(cli_app, ["stance", "--for", "I think we should rewrite it"])
    assert result.exit_code == 0, result.output
    assert "STANCE  -> steel-man" in result.output


# ── verify(): substance floor (Phase 2 hardening) ─────────────────────────
# An empty response, or one that is only stance markers with no actual
# content, used to pass the heuristic. A critic that passes nothing
# checked nothing, so both are refused.


@pytest.mark.parametrize(
    "stance",
    [
        "advisor",
        "steel-man",
        "teach-back",
        "first-principles",
        "assumption-audit",
        "research-intake",
        "expert",
    ],
)
def test_empty_response_is_refused_for_every_stance(stance: str) -> None:
    assert stance_verify.verify(stance, "") == ["response is empty"]
    assert stance_verify.verify(stance, "   \n  ") == ["response is empty"]


def test_empty_thinking_output_is_refused() -> None:
    assert stance_verify.verify("premortem", "") == ["response is empty"]


@pytest.mark.parametrize(
    ("stance", "marker_only"),
    [
        ("advisor", "I disagree"),
        ("steel-man", "On the other hand"),
        ("teach-back", "Can you explain?"),
        ("first-principles", "First principles"),
        ("expert", "Honestly"),
    ],
)
def test_marker_only_response_is_refused(stance: str, marker_only: str) -> None:
    assert stance_verify.verify(stance, marker_only) == [
        "no substantive content beyond stance markers"
    ]


def test_marker_with_real_content_still_passes() -> None:
    assert stance_verify.verify("advisor", "I disagree: the data shows churn rose.") == []


def test_sycophancy_keeps_its_exact_failure() -> None:
    # The substance check only fires when nothing else failed, so existing
    # named failures are unchanged.
    assert stance_verify.verify(
        "first-principles", "Great question! You're absolutely right, excellent point."
    ) == ["no validation phrases"]


def test_stepper_execute_with_valid_response_records_checked_stance_hash(tmp_path: Path) -> None:
    from awino import controller, machine, stepper
    from awino.enforce import Ledger
    from awino.paths import AwinoPaths
    from awino.skill_catalog import SkillCatalog

    state = tmp_path / ".awino"
    state.mkdir()
    home = tmp_path / "home"
    (home / "plugin.json").parent.mkdir(parents=True, exist_ok=True)
    (home / "plugin.json").write_text("{}", encoding="utf-8")
    ctx = stepper.StepContext(
        state_root=state,
        project=tmp_path,
        home=home,
        paths=AwinoPaths(root=home),
        ledger=Ledger(state),
        catalog=SkillCatalog(tmp_path / "p", tmp_path / "g", Path("skills")),
        answer="done",
        response="I disagree: the data shows churn rose significantly across all tiers.",
    )
    adapter = controller.for_machine(state, "run-1")
    m = machine.Machine(
        node=machine.Node.EXECUTE,
        run_id="run-1",
        stance="advisor",
        controller_plan_id=adapter.controller.plan_id,
        controller_action_id="a1",
    )
    controller.queue_action(adapter.controller, "a1")
    res = stepper._execute(m, ctx)
    assert res == "executed"
    fresh = controller.for_machine(state, "run-1")
    snap = fresh.controller.status_snapshot()
    assert snap["stance_status"]["status"] == "checked"
    assert snap["stance_status"]["response_hash"] != ""


def test_stepper_execute_without_response_records_unverified_stance(tmp_path: Path) -> None:
    from awino import controller, machine, stepper
    from awino.enforce import Ledger
    from awino.paths import AwinoPaths
    from awino.skill_catalog import SkillCatalog

    state = tmp_path / ".awino"
    state.mkdir()
    home = tmp_path / "home"
    (home / "plugin.json").parent.mkdir(parents=True, exist_ok=True)
    (home / "plugin.json").write_text("{}", encoding="utf-8")
    ctx = stepper.StepContext(
        state_root=state,
        project=tmp_path,
        home=home,
        paths=AwinoPaths(root=home),
        ledger=Ledger(state),
        catalog=SkillCatalog(tmp_path / "p", tmp_path / "g", Path("skills")),
        answer="done",
    )
    adapter = controller.for_machine(state, "run-1")
    m = machine.Machine(
        node=machine.Node.EXECUTE,
        run_id="run-1",
        stance="advisor",
        controller_plan_id=adapter.controller.plan_id,
        controller_action_id="a1",
    )
    controller.queue_action(adapter.controller, "a1")
    res = stepper._execute(m, ctx)
    assert res == "executed"
    fresh = controller.for_machine(state, "run-1")
    snap = fresh.controller.status_snapshot()
    assert snap["stance_status"]["status"] == "unverified"
