"""Phase 3: shared planning and assignment contract.

Covers normalization, invalid inputs, versioning/template sync, staleness
and rebase, approval binding (grant/edit/invalidate), the prefilled brief
round-trip, persistence with revision history, the spawn-time refusal of
stale/unapproved contracts, and the dispatch-time approval gate.
"""

from __future__ import annotations

import copy
from dataclasses import fields
from pathlib import Path

import pytest

from awino.controller import PlanController
from awino.dispatch import DispatchOutcome, _build_assignment, run_dispatch
from awino.enforce import Ledger
from awino.health import Health, Result
from awino.paths import AwinoPaths, project_state_dir
from awino.skill_catalog import SkillCatalog
from awino.spawn import Assignment, Role, Runner, SpawnResult, spawn_one
from awino.task_contract import (
    APPROVED,
    BRIEF_TYPE,
    CONTRACT_SCHEMA_VERSION,
    DRAFT,
    INVALIDATED,
    MATERIAL_FIELDS,
    STATES,
    VALID_TRANSITIONS,
    BriefParseError,
    ContractError,
    ContractNotFound,
    ContractRef,
    InvalidContract,
    StaleContract,
    TaskContract,
    apply_brief_edits,
    check_contract_ref,
    contract_history,
    create_contract,
    grant_contract_approval,
    load_contract,
    load_template,
    rebase_contract,
    record_contract_edit,
    render_contract_block,
    render_prefilled_draft,
    request_contract_approval,
    revision_hash,
    save_contract,
    supersede_contract,
)

SMITH_ROOT = Path(__file__).resolve().parents[1]


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def controller(state_root: Path) -> PlanController:
    return PlanController.create(
        state_root, "plan-1", budgets={"spawns": 3}, verifier="human"
    )


def make_contract(plan_revision_seen: int = 0, **overrides) -> TaskContract:
    params = {
        "contract_id": "c-1",
        "plan_id": "plan-1",
        "role": "builder",
        "objective": "Fix the flaky retry test",
        "file_scope": ["src/awino/loops.py"],
        "context_paths": ["docs/PHILOSOPHY.md"],
        "verification": "uv run --frozen pytest tests/test_loops.py -q",
        "budgets": {"spawns": 2},
        "verifier": "human",
        "plan_revision_seen": plan_revision_seen,
    }
    params.update(overrides)
    return create_contract(**params)


def approve(controller: PlanController, contract: TaskContract, by: str = "luke") -> TaskContract:
    save_contract(controller, contract)
    grant_contract_approval(controller, contract, by=by)
    return contract


# ── normalization ───────────────────────────────────────────────────────


class TestNormalization:
    def test_whitespace_collapses_and_role_lowercases(self) -> None:
        c = make_contract(objective="  Fix   the\nbug ", role="Builder")
        assert c.objective == "Fix the bug"
        assert c.role == "builder"

    def test_scopes_are_sorted_unique_and_slash_normalized(self) -> None:
        c = make_contract(file_scope=["b.py", "a.py", "b.py", "a.py"])
        assert c.file_scope == ["a.py", "b.py"]

    def test_identical_contracts_hash_identically(self) -> None:
        a = make_contract(objective="Fix  the bug")
        b = make_contract(objective="Fix the bug")
        assert revision_hash(a) == revision_hash(b)


# ── invalid inputs ──────────────────────────────────────────────────────


class TestInvalidInputs:
    def test_empty_objective_is_rejected(self) -> None:
        with pytest.raises(InvalidContract, match="objective is empty"):
            make_contract(objective="   ")

    def test_unknown_role_is_rejected(self) -> None:
        with pytest.raises(InvalidContract, match="not one of"):
            make_contract(role="manager")

    def test_builder_without_scope_is_rejected(self) -> None:
        with pytest.raises(InvalidContract, match="file scope"):
            make_contract(file_scope=[])

    def test_builder_without_verification_is_rejected(self) -> None:
        with pytest.raises(InvalidContract, match="verification command"):
            make_contract(verification="")

    def test_builder_without_verifier_is_rejected(self) -> None:
        with pytest.raises(InvalidContract, match="name a verifier"):
            make_contract(verifier="")

    def test_read_only_role_with_scope_is_rejected(self) -> None:
        with pytest.raises(InvalidContract, match="read-only but declares"):
            make_contract(role="reviewer")

    def test_reviewer_contract_is_valid_without_scope(self) -> None:
        c = make_contract(role="reviewer", file_scope=[])
        assert c.validate() == []

    def test_nonpositive_budget_is_rejected(self) -> None:
        with pytest.raises(InvalidContract, match="must be positive"):
            make_contract(budgets={"spawns": 0})

    def test_empty_ids_are_rejected(self) -> None:
        with pytest.raises(InvalidContract, match="contract_id is empty"):
            make_contract(contract_id=" ")


# ── versioning and template sync ────────────────────────────────────────


class TestTemplateSync:
    def test_template_parses_and_names_this_schema(self) -> None:
        template = load_template()
        assert template["schema_version"] == CONTRACT_SCHEMA_VERSION
        assert template["brief_type"] == BRIEF_TYPE

    def test_template_fields_exist_on_the_dataclass(self) -> None:
        template = load_template()
        declared = {f.name for f in fields(TaskContract)}
        documented = {f["name"] for f in template["fields"]}
        assert documented <= declared, f"documented but missing: {documented - declared}"

    def test_template_material_fields_match_the_module(self) -> None:
        template = load_template()
        assert set(template["material_fields"]) == set(MATERIAL_FIELDS)

    def test_template_states_and_transitions_match_the_module(self) -> None:
        template = load_template()
        assert set(template["states"]) == set(STATES)
        assert template["transitions"]["approved"] == list(VALID_TRANSITIONS["approved"])
        assert template["transitions"]["superseded"] == []

    def test_template_brief_sections_are_the_parsed_sections(self) -> None:
        from awino.task_contract import _BRIEF_HEADERS

        template = load_template()
        assert list(template["brief"]["sections"]) == list(_BRIEF_HEADERS)


# ── human edits: provenance, wording, materiality ───────────────────────


class TestHumanEdits:
    def test_edit_keeps_user_wording_and_records_provenance(
        self, controller: PlanController
    ) -> None:
        c = make_contract()
        record = record_contract_edit(
            controller, c, "objective", "Fix the flaky retry test, for real", by="luke"
        )
        assert c.objective == "Fix the flaky retry test, for real"
        assert record["by"] == "luke"
        assert record["field"] == "objective"
        assert record["before"] == "Fix the flaky retry test"
        assert record["at"]
        assert record["material"] is True
        assert c.contract_revision == 2

    def test_edit_on_draft_does_not_invalidate(
        self, controller: PlanController
    ) -> None:
        c = make_contract()
        record = record_contract_edit(controller, c, "objective", "New wording", by="luke")
        assert c.state == DRAFT
        assert "invalidated_approval" not in record

    def test_material_edit_on_approved_invalidates(
        self, controller: PlanController
    ) -> None:
        c = approve(controller, make_contract())
        record = record_contract_edit(
            controller, c, "file_scope", ["src/awino/loops.py", "src/awino/stepper.py"], by="luke"
        )
        assert record["invalidated_approval"] is True
        assert c.state == INVALIDATED
        assert not c.approval_covers()

    def test_cosmetic_edit_keeps_approval(self, controller: PlanController) -> None:
        c = approve(controller, make_contract())
        record = record_contract_edit(
            controller,
            c,
            "objective",
            "Fix the flaky retry test.",  # punctuation-only reword, caller-declared cosmetic
            by="luke",
            material=False,
        )
        assert record["material"] is False
        assert c.state == APPROVED
        # The grant still covers the contract: the only post-grant change is
        # a recorded, explicitly cosmetic edit with full provenance.
        assert c.approval_covers()

    def test_system_fields_cannot_be_edited(self, controller: PlanController) -> None:
        c = make_contract()
        with pytest.raises(ContractError, match="system-managed"):
            record_contract_edit(controller, c, "contract_revision", 99, by="luke")

    def test_unknown_field_is_rejected(self, controller: PlanController) -> None:
        c = make_contract()
        with pytest.raises(ContractError, match="no field"):
            record_contract_edit(controller, c, "frobnicate", "x", by="luke")

    def test_edit_that_breaks_validation_rolls_back(
        self, controller: PlanController
    ) -> None:
        c = make_contract()
        with pytest.raises(InvalidContract):
            record_contract_edit(controller, c, "file_scope", [], by="luke")
        assert c.file_scope == ["src/awino/loops.py"]
        assert c.contract_revision == 1
        assert c.human_edits == []

    def test_edit_needs_a_name(self, controller: PlanController) -> None:
        c = make_contract()
        with pytest.raises(ContractError, match="needs a name"):
            record_contract_edit(controller, c, "objective", "x", by="  ")


# ── approval binding ────────────────────────────────────────────────────


class TestApprovalBinding:
    def test_grant_binds_revision_and_hash(self, controller: PlanController) -> None:
        c = approve(controller, make_contract())
        assert c.state == APPROVED
        assert c.approval is not None
        assert c.approval["contract_revision"] == c.contract_revision
        assert c.approval["revision_hash"] == c.revision_hash()
        assert c.approval["by"] == "luke"
        assert c.approval_covers()

    def test_grant_needs_a_name(self, controller: PlanController) -> None:
        c = make_contract()
        save_contract(controller, c)
        with pytest.raises(ContractError, match="needs a name"):
            grant_contract_approval(controller, c, by="  ")

    def test_double_approve_is_refused(self, controller: PlanController) -> None:
        c = approve(controller, make_contract())
        with pytest.raises(ContractError, match="only a draft or invalidated"):
            grant_contract_approval(controller, c, by="luke")

    def test_tampered_contract_fails_the_binding(self, controller: PlanController) -> None:
        c = approve(controller, make_contract())
        # Bypass the edit path (simulates a hand-edited file): the change is
        # unrecorded, so the binding must fail.
        c.objective = "Do something else entirely"
        assert not c.approval_covers()

    def test_reapproval_after_invalidation_binds_the_new_revision(
        self, controller: PlanController
    ) -> None:
        c = approve(controller, make_contract())
        record_contract_edit(controller, c, "budgets", {"spawns": 5}, by="luke")
        assert c.state == INVALIDATED
        grant_contract_approval(controller, c, by="luke")
        assert c.state == APPROVED
        assert c.approval_covers()
        assert c.approval["contract_revision"] == 2

    def test_approval_ask_is_recorded_at_brief_review_time(
        self, controller: PlanController
    ) -> None:
        c = make_contract()
        save_contract(controller, c)
        action_id = request_contract_approval(
            controller, c, action="approve contract c-1", by="luke"
        )
        assert action_id == f"contract-c-1-r{c.contract_revision}"
        pending = [a["action_id"] for a in controller.state.pending_approvals]
        assert action_id in pending

    def test_supersede_is_terminal(self, controller: PlanController) -> None:
        c = approve(controller, make_contract())
        supersede_contract(controller, c, by="luke")
        from awino.task_contract import SUPERSEDED

        assert c.state == SUPERSEDED
        with pytest.raises(ContractError, match="only a draft or invalidated"):
            grant_contract_approval(controller, c, by="luke")


# ── staleness ───────────────────────────────────────────────────────────


class TestStaleness:
    def test_current_contract_passes(self, controller: PlanController) -> None:
        c = approve(controller, make_contract())
        c.assert_current(controller.state.plan_revision)  # no raise

    def test_stale_contract_raises(self, controller: PlanController) -> None:
        c = make_contract(plan_revision_seen=0)
        with pytest.raises(StaleContract, match="plan is at"):
            c.assert_current(4)

    def test_rebase_adopts_new_revision_and_voids_approval(
        self, controller: PlanController
    ) -> None:
        from awino.controller import queue_action

        c = approve(controller, make_contract())
        queue_action(controller, "unrelated-1")
        queue_action(controller, "unrelated-2")
        old_rev = c.contract_revision
        rebase_contract(controller, c, by="luke")
        assert c.plan_revision_seen == controller.state.plan_revision
        assert c.contract_revision == old_rev + 1
        assert c.state == INVALIDATED
        assert not c.approval_covers()
        # History is unbroken: the rebase is in the edit log.
        assert c.human_edits[-1]["rebase"] is True

    def test_rebase_with_no_plan_movement_is_refused(
        self, controller: PlanController
    ) -> None:
        c = approve(controller, make_contract())
        with pytest.raises(ContractError, match="nothing to rebase"):
            rebase_contract(controller, c, by="luke")

    def test_rebase_of_draft_keeps_draft(self, controller: PlanController) -> None:
        from awino.controller import queue_action

        c = make_contract(plan_revision_seen=0)
        save_contract(controller, c)
        queue_action(controller, "unrelated")
        rebase_contract(controller, c, by="luke")
        assert c.state == DRAFT
        assert c.plan_revision_seen == controller.state.plan_revision


# ── persistence ─────────────────────────────────────────────────────────


class TestPersistence:
    def test_save_load_roundtrip(self, controller: PlanController, state_root: Path) -> None:
        c = approve(controller, make_contract())
        loaded = load_contract(state_root, "plan-1", "c-1")
        assert loaded.contract_id == c.contract_id
        assert loaded.contract_revision == c.contract_revision
        assert loaded.approval_covers()

    def test_revision_history_is_preserved(
        self, controller: PlanController, state_root: Path
    ) -> None:
        c = make_contract()
        save_contract(controller, c)
        record_contract_edit(controller, c, "objective", "v2 wording", by="luke")
        record_contract_edit(controller, c, "objective", "v3 wording", by="luke")
        assert contract_history(state_root, "plan-1", "c-1") == [1, 2, 3]

    def test_missing_contract_raises(self, state_root: Path) -> None:
        with pytest.raises(ContractNotFound, match="no contract"):
            load_contract(state_root, "plan-1", "nope")

    def test_plan_snapshot_carries_contract_state(
        self, controller: PlanController
    ) -> None:
        c = approve(controller, make_contract())
        snapshot = controller.status_snapshot()
        assert snapshot["contracts"]["c-1"]["state"] == "approved"
        assert snapshot["contracts"]["c-1"]["revision"] == c.contract_revision

    def test_reload_from_disk_keeps_approval(
        self, controller: PlanController, state_root: Path
    ) -> None:
        approve(controller, make_contract())
        fresh = PlanController.load(state_root, "plan-1")
        reloaded = load_contract(state_root, "plan-1", "c-1")
        assert not reloaded.is_stale(fresh.state.plan_revision)
        assert reloaded.approval_covers()

# ── the prefilled brief round-trip ──────────────────────────────────────


class TestPlanningBrief:
    def test_draft_is_prefilled_from_the_contract(
        self, controller: PlanController
    ) -> None:
        c = make_contract()
        brief = render_prefilled_draft(c)
        assert brief.brief_type == BRIEF_TYPE
        assert brief.contract_revision == c.contract_revision
        assert "Fix the flaky retry test" in brief.draft
        assert "src/awino/loops.py" in brief.draft
        assert "uv run --frozen pytest tests/test_loops.py -q" in brief.draft

    def test_returned_brief_applies_edits_with_provenance(
        self, controller: PlanController
    ) -> None:
        c = make_contract()
        save_contract(controller, c)
        brief = render_prefilled_draft(c)
        edited = brief.draft.replace(
            "Fix the flaky retry test", "Fix the flaky retry test and add a regression test"
        )
        records = apply_brief_edits(controller, c, edited, by="luke")
        assert len(records) == 1
        assert records[0]["field"] == "objective"
        assert records[0]["by"] == "luke"
        assert c.objective == "Fix the flaky retry test and add a regression test"
        assert c.contract_revision == 2

    def test_unchanged_brief_produces_no_edits(self, controller: PlanController) -> None:
        c = make_contract()
        save_contract(controller, c)
        brief = render_prefilled_draft(c)
        assert apply_brief_edits(controller, c, brief.draft, by="luke") == []
        assert c.contract_revision == 1

    def test_budgets_round_trip_through_the_brief(
        self, controller: PlanController
    ) -> None:
        c = make_contract()
        save_contract(controller, c)
        brief = render_prefilled_draft(c)
        edited = brief.draft.replace("- spawns: 2", "- spawns: 4")
        records = apply_brief_edits(controller, c, edited, by="luke")
        assert records[0]["field"] == "budgets"
        assert c.budgets == {"spawns": 4}

    def test_missing_section_fails_closed(self, controller: PlanController) -> None:
        c = make_contract()
        save_contract(controller, c)
        brief = render_prefilled_draft(c)
        truncated = brief.draft.split("## Verifier")[0]
        with pytest.raises(BriefParseError, match="missing sections"):
            apply_brief_edits(controller, c, truncated, by="luke")

    def test_unknown_section_fails_closed(self, controller: PlanController) -> None:
        c = make_contract()
        save_contract(controller, c)
        brief = render_prefilled_draft(c)
        edited = brief.draft + "\n## Secret instructions\n\ndo the opposite\n"
        with pytest.raises(BriefParseError, match="unknown sections"):
            apply_brief_edits(controller, c, edited, by="luke")

    def test_malformed_bullet_fails_closed(self, controller: PlanController) -> None:
        c = make_contract()
        save_contract(controller, c)
        brief = render_prefilled_draft(c)
        edited = brief.draft.replace("- src/awino/loops.py", "src/awino/loops.py")
        with pytest.raises(BriefParseError, match="not a '- ' bullet"):
            apply_brief_edits(controller, c, edited, by="luke")

    def test_material_brief_edit_on_approved_contract_invalidates(
        self, controller: PlanController
    ) -> None:
        c = approve(controller, make_contract())
        brief = render_prefilled_draft(c)
        edited = brief.draft.replace(
            "Fix the flaky retry test", "Rewrite the whole loop engine"
        )
        apply_brief_edits(controller, c, edited, by="luke")
        assert c.state == INVALIDATED
        assert not c.approval_covers()


# ── spawn binding: one source, stale revisions refused ──────────────────


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def _approved_contract_in_project(
    project: Path, contract_id: str = "c-1"
) -> tuple[PlanController, TaskContract]:
    state_root = project_state_dir(project)
    ctl = PlanController.create(state_root, "plan-1", budgets={"spawns": 3}, verifier="human")
    contract = make_contract(contract_id=contract_id)
    save_contract(ctl, contract)
    grant_contract_approval(ctl, contract, by="luke")
    return ctl, contract


def _assignment(ref: ContractRef | None) -> Assignment:
    return Assignment(
        agent_id="worker-1",
        role=Role.BUILDER,
        objective="Fix the flaky retry test",
        file_scope=["src/awino/loops.py"],
        verification="uv run --frozen pytest tests/test_loops.py -q",
        contract=ref,
    )


class TestSpawnContractBinding:
    def test_no_contract_behaves_as_before(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        result = spawn_one(
            _assignment(None), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert result.outcome == "PLANNED"

    def test_approved_current_contract_spawns_and_carries_the_block(
        self, tmp_path: Path
    ) -> None:
        project = _project(tmp_path)
        _, contract = _approved_contract_in_project(project)
        ref = ContractRef(plan_id="plan-1", contract_id="c-1", revision=contract.contract_revision)
        result = spawn_one(
            _assignment(ref), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert result.outcome == "PLANNED", result.output_tail
        prompt_path = Path(result.output_tail.split(": ", 1)[1])
        prompt = prompt_path.read_text(encoding="utf-8")
        assert "## Task contract (authoritative)" in prompt
        assert "contract: c-1" in prompt
        assert f"r{contract.contract_revision}" in prompt
        assert BRIEF_TYPE in prompt

    def test_stale_revision_is_refused(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        ctl, contract = _approved_contract_in_project(project)
        record_contract_edit(ctl, contract, "objective", "New objective", by="luke")
        grant_contract_approval(ctl, contract, by="luke")
        stale = ContractRef(plan_id="plan-1", contract_id="c-1", revision=1)
        result = spawn_one(
            _assignment(stale), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert result.outcome == "REFUSED"
        assert "stale contract revision" in result.output_tail

    def test_unapproved_contract_is_refused(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        state_root = project_state_dir(project)
        ctl = PlanController.create(state_root, "plan-1", verifier="human")
        contract = make_contract()
        save_contract(ctl, contract)
        ref = ContractRef(plan_id="plan-1", contract_id="c-1", revision=1)
        result = spawn_one(
            _assignment(ref), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert result.outcome == "REFUSED"
        assert "not approved" in result.output_tail

    def test_missing_contract_is_refused(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        state_root = project_state_dir(project)
        PlanController.create(state_root, "plan-1", verifier="human")
        ref = ContractRef(plan_id="plan-1", contract_id="ghost", revision=1)
        result = spawn_one(
            _assignment(ref), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert result.outcome == "REFUSED"
        assert "no contract" in result.output_tail

    def test_plan_moved_on_is_refused_until_rebase(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        ctl, contract = _approved_contract_in_project(project)
        from awino.controller import queue_action

        queue_action(ctl, "unrelated-action")  # any event bumps the plan revision
        ref = ContractRef(
            plan_id="plan-1", contract_id="c-1", revision=contract.contract_revision
        )
        refused = spawn_one(
            _assignment(ref), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert refused.outcome == "REFUSED"
        assert "stale" in refused.output_tail.lower()
        # Rebase + re-approve recovers.
        rebase_contract(ctl, contract, by="luke")
        grant_contract_approval(ctl, contract, by="luke")
        fresh = ContractRef(
            plan_id="plan-1", contract_id="c-1", revision=contract.contract_revision
        )
        ok = spawn_one(
            _assignment(fresh), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert ok.outcome == "PLANNED", ok.output_tail

    def test_cosmetic_edit_still_spawns(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        ctl, contract = _approved_contract_in_project(project)
        record_contract_edit(
            ctl, contract, "objective", contract.objective + ".", by="luke", material=False
        )
        assert contract.approval_covers()
        ref = ContractRef(
            plan_id="plan-1", contract_id="c-1", revision=contract.contract_revision
        )
        result = spawn_one(
            _assignment(ref), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert result.outcome == "PLANNED", result.output_tail

    def test_edited_after_approval_is_refused(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        _, contract = _approved_contract_in_project(project)
        # Simulate a hand edit that bypassed the edit path: the stored file
        # no longer matches the grant hash.
        from awino.task_contract import _write_contract_files

        contract.objective = "Do something else"
        _write_contract_files(project_state_dir(project), contract)
        ref = ContractRef(
            plan_id="plan-1", contract_id="c-1", revision=contract.contract_revision
        )
        result = spawn_one(
            _assignment(ref), Path("/tmp"), project, Runner.NONE, dry_run=True
        )
        assert result.outcome == "REFUSED"
        assert "edited after its approval" in result.output_tail

    def test_check_contract_ref_is_the_single_source(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        _, contract = _approved_contract_in_project(project)
        ref = ContractRef(plan_id="plan-1", contract_id="c-1", revision=contract.contract_revision)
        assert check_contract_ref(project_state_dir(project), ref) == []


# ── dispatch binding ────────────────────────────────────────────────────


def _ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger")


def _open_run(ledger: Ledger) -> str:
    from awino.enforce import TaskClass

    return ledger.open(TaskClass.QUESTION, "contract gate test").run_id


def _paths(tmp_path: Path) -> AwinoPaths:
    root = tmp_path / "awino-home"
    root.mkdir(exist_ok=True)
    (root / "plugin.json").write_text("{}", encoding="utf-8")
    (root / "knowledge").mkdir(exist_ok=True)
    return AwinoPaths(root=root)


def _catalog() -> SkillCatalog:
    return SkillCatalog(
        project_root=Path("/nonexistent-project-root"),
        global_root=Path("/nonexistent-global-root"),
        bundled_root=SMITH_ROOT / "skills",
    )


def _ok_health(paths: AwinoPaths, *, fast: bool = False) -> list[Result]:
    del paths, fast
    return [Result("test_only", Health.OK, "fine")]


def _request() -> str:
    # Routes high-confidence to awino-debug against the real catalog.
    return "pytest is failing with a ValueError in the loader"


# ── contract references: plan/contract[@revision] ───────────────────────


class TestContractRefParse:
    def test_bare_form_means_currently_stored(self) -> None:
        ref = ContractRef.parse("plan-1/c-1")
        assert ref.plan_id == "plan-1"
        assert ref.contract_id == "c-1"
        assert not ref.pinned

    def test_pinned_form_names_the_revision(self) -> None:
        ref = ContractRef.parse("plan-1/c-1@3")
        assert ref.revision == 3
        assert ref.pinned

    def test_whitespace_is_tolerated(self) -> None:
        ref = ContractRef.parse("  plan-1/c-1@2  ")
        assert (ref.plan_id, ref.contract_id, ref.revision) == ("plan-1", "c-1", 2)

    @pytest.mark.parametrize(
        "bad",
        ["", "plan-1", "plan-1/", "/c-1", "plan-1/c-1@x", "plan-1/c-1@0", "plan-1/c-1@-2"],
    )
    def test_bad_forms_are_refused(self, bad: str) -> None:
        with pytest.raises(ContractError):
            ContractRef.parse(bad)


class TestContractOption:
    def test_good_reference_returns_the_stored_contract(self, tmp_path: Path) -> None:
        from awino.cli.dispatch import _resolve_contract_option

        project = _project(tmp_path)
        _, contract = _approved_contract_in_project(project)
        got = _resolve_contract_option("plan-1/c-1", project)
        assert got is not None
        assert got.contract_id == "c-1"
        assert got.contract_revision == contract.contract_revision

    def test_pinned_reference_must_match_the_stored_revision(
        self, tmp_path: Path
    ) -> None:
        import typer

        from awino.cli.dispatch import _resolve_contract_option

        project = _project(tmp_path)
        _approved_contract_in_project(project)
        with pytest.raises(typer.Exit):
            _resolve_contract_option("plan-1/c-1@99", project)

    def test_malformed_reference_is_refused(self, tmp_path: Path) -> None:
        import typer

        from awino.cli.dispatch import _resolve_contract_option

        with pytest.raises(typer.Exit):
            _resolve_contract_option("nope", _project(tmp_path))

    def test_unknown_contract_is_refused(self, tmp_path: Path) -> None:
        import typer

        from awino.cli.dispatch import _resolve_contract_option

        with pytest.raises(typer.Exit):
            _resolve_contract_option("plan-1/ghost", _project(tmp_path))

    def test_absent_option_returns_none(self, tmp_path: Path) -> None:
        from awino.cli.dispatch import _resolve_contract_option

        assert _resolve_contract_option(None, _project(tmp_path)) is None


class TestDispatchContractGate:
    def test_unapproved_contract_blocks_before_any_floor(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        state_root = project_state_dir(project)
        ctl = PlanController.create(state_root, "plan-1", verifier="human")
        contract = make_contract()
        save_contract(ctl, contract)
        calls: list[Assignment] = []

        def execute(assignment: Assignment, awino_home: Path, proj: Path, runner: Runner):
            calls.append(assignment)
            return SpawnResult(assignment.agent_id, "NO_SIGNAL", 0, 1, "")

        ledger = _ledger(tmp_path)
        result = run_dispatch(
            ledger,
            _open_run(ledger),
            _request(),
            _catalog(),
            _paths(tmp_path),
            tmp_path / "awino-home",
            project,
            Runner.NONE,
            "echo ok",
            file_scope=[],
            confirmed_budget=True,
            max_floors=1,
            execute=execute,
            health_check=_ok_health,
            contract=contract,
        )
        assert result.outcome == DispatchOutcome.BLOCKED
        assert "prefilled brief" in result.reason
        assert calls == []

    def test_approved_contract_flows_and_workers_carry_the_ref(
        self, tmp_path: Path
    ) -> None:
        project = _project(tmp_path)
        _, contract = _approved_contract_in_project(project)
        calls: list[Assignment] = []

        def execute(assignment: Assignment, awino_home: Path, proj: Path, runner: Runner):
            calls.append(assignment)
            return SpawnResult(
                assignment.agent_id, "CLAIMED", 0, 1, "", claimed_complete=True
            )

        def verify_fn(spawned: SpawnResult, assignment: Assignment, proj: Path):
            spawned.verified = True
            return spawned

        ledger = _ledger(tmp_path)
        result = run_dispatch(
            ledger,
            _open_run(ledger),
            _request(),
            _catalog(),
            _paths(tmp_path),
            tmp_path / "awino-home",
            project,
            Runner.NONE,
            "echo ok",
            file_scope=[],
            confirmed_budget=True,
            max_floors=1,
            execute=execute,
            verify_fn=verify_fn,
            health_check=_ok_health,
            contract=contract,
        )
        assert result.outcome == DispatchOutcome.COMPLETE
        assert len(calls) == 1
        ref = calls[0].contract
        assert ref is not None
        assert (ref.plan_id, ref.contract_id, ref.revision) == (
            "plan-1",
            "c-1",
            contract.contract_revision,
        )
        # The assignment reads from the contract, not the request.
        assert calls[0].objective == contract.objective
        assert calls[0].file_scope == contract.file_scope

    def test_build_assignment_without_contract_is_unchanged(self) -> None:
        assignment = _build_assignment("do a thing", "awino-debug", 1, None, ["x.py"], "echo ok")
        assert assignment.contract is None
        assert assignment.role == Role.BUILDER

    def test_build_assignment_with_contract_reads_one_source(self) -> None:
        contract = make_contract()
        assignment = _build_assignment("do a thing", "awino-debug", 1, None, [], "", contract)
        assert assignment.role == Role.BUILDER
        assert assignment.objective == contract.objective
        assert assignment.file_scope == contract.file_scope
        assert assignment.verification == contract.verification
        assert assignment.contract == ContractRef("plan-1", "c-1", 1)

    def test_open_floor_refuses_unapproved_contract(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        ctl = PlanController.create(project_state_dir(project), "plan-1", verifier="human")
        contract = make_contract()  # draft
        save_contract(ctl, contract)
        with pytest.raises(ValueError, match="prefilled brief"):
            from awino.dispatch import open_floor

            open_floor(
                _ledger(tmp_path),
                "run-3",
                _request(),
                _catalog(),
                tmp_path / "awino-home",
                "echo ok",
                file_scope=[],
                project=project,
                contract=contract,
            )

    def test_open_floor_requires_project_with_contract(self, tmp_path: Path) -> None:
        contract = make_contract()
        with pytest.raises(ValueError, match="project is required"):
            from awino.dispatch import open_floor

            open_floor(
                _ledger(tmp_path),
                "run-4",
                _request(),
                _catalog(),
                tmp_path / "awino-home",
                "echo ok",
                file_scope=[],
                contract=contract,
            )

    def test_open_floor_validates_stored_state_not_the_passed_object(
        self, tmp_path: Path
    ) -> None:
        # The plan moved on after the contract was approved. The passed
        # object is a pre-move copy that looks fine; the stored state is
        # what refuses.
        from awino.controller import queue_action

        project = _project(tmp_path)
        ctl, contract = _approved_contract_in_project(project)
        approved_copy = copy.deepcopy(contract)
        queue_action(ctl, "unrelated")
        with pytest.raises(ValueError, match="stale"):
            from awino.dispatch import open_floor

            open_floor(
                _ledger(tmp_path),
                "run-5",
                _request(),
                _catalog(),
                tmp_path / "awino-home",
                "echo ok",
                file_scope=[],
                project=project,
                contract=approved_copy,
            )

    def test_open_floor_approved_contract_writes_prompt_with_ref(
        self, tmp_path: Path
    ) -> None:
        project = _project(tmp_path)
        _, contract = _approved_contract_in_project(project)
        ledger = _ledger(tmp_path)
        from awino.dispatch import open_floor

        state = open_floor(
            ledger,
            _open_run(ledger),
            _request(),
            _catalog(),
            tmp_path / "awino-home",
            "echo ok",
            file_scope=[],
            project=project,
            contract=contract,
        )
        prompt = Path(state.prompt_path).read_text(encoding="utf-8")
        assert "c-1" in prompt
        assert f"r{contract.contract_revision}" in prompt

    def test_dispatch_reloads_the_stored_contract(self, tmp_path: Path) -> None:
        # A tampered passed object cannot inject content: run_dispatch
        # builds the assignment from the stored file, not the argument, so
        # the worker sees the approved objective, not the tampered one.
        project = _project(tmp_path)
        _, contract = _approved_contract_in_project(project)
        tampered = copy.deepcopy(contract)
        tampered.objective = "Do something else entirely"
        calls: list[Assignment] = []

        def execute(assignment: Assignment, awino_home: Path, proj: Path, runner: Runner):
            calls.append(assignment)
            return SpawnResult(
                assignment.agent_id, "CLAIMED", 0, 1, "", claimed_complete=True
            )

        def verify_fn(spawned: SpawnResult, assignment: Assignment, proj: Path):
            spawned.verified = True
            return spawned

        ledger = _ledger(tmp_path)
        result = run_dispatch(
            ledger,
            _open_run(ledger),
            _request(),
            _catalog(),
            _paths(tmp_path),
            tmp_path / "awino-home",
            project,
            Runner.NONE,
            "echo ok",
            file_scope=[],
            confirmed_budget=True,
            max_floors=1,
            execute=execute,
            verify_fn=verify_fn,
            health_check=_ok_health,
            contract=tampered,
        )
        assert result.outcome == DispatchOutcome.COMPLETE
        assert len(calls) == 1
        assert calls[0].objective == contract.objective
        assert "Do something else" not in calls[0].objective


# ── contract block rendering ────────────────────────────────────────────


class TestContractBlock:
    def test_block_names_revision_brief_and_grant(self, controller: PlanController) -> None:
        c = approve(controller, make_contract())
        block = render_contract_block(c)
        assert "task-brief/v1" in block
        assert f"r{c.contract_revision}" in block
        assert "luke" in block
        assert "single source" in block

    def test_block_without_approval_says_stop(self) -> None:
        block = render_contract_block(make_contract())
        assert "do not execute" in block
