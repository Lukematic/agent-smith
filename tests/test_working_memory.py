"""Working memory: the mind to the ledger's court record.

Unit coverage for the four pieces (checklist, facts, decisions, user model)
plus the audit/fix hooks buddy runs over them and the session-end hooks that
feed them. The user model reads ~/.awino/profile.yaml, so every test that
touches it runs with an isolated HOME.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from awino import loops, playbook, session_log, session_markers, session_state, working_memory
from awino.cli import buddy, loopctl
from awino.enforce import Ledger, LoopEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"


@pytest.fixture()
def state_root(tmp_path: Path) -> Path:
    return tmp_path / ".awino"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate ~/.awino so user-model tests never touch the real profile."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


def _loop_id() -> str:
    return f"rpi-test-{datetime.now(UTC).isoformat()}"


# ── checklist ────────────────────────────────────────────────────────────────


def test_checklist_transitions_across_boundaries(state_root: Path) -> None:
    checklist = working_memory.Checklist(state_root)
    loop_id = _loop_id()

    checklist.note_loop_created(loop_id, "rpi", "refactor the parser", "research")
    item = checklist.focus()
    assert item is not None
    assert item["loop_id"] == loop_id
    assert item["status"] == "doing"
    assert item["phase"] == "research"
    assert item["history"][0]["from"] == "open"

    checklist.note_phase(loop_id, "research", "pair-plan")
    assert checklist.focus()["phase"] == "pair-plan"
    assert checklist.focus()["status"] == "doing"

    checklist.note_blocked(loop_id, "3/3 failures")
    blocked = checklist.blocked_items()
    assert len(blocked) == 1
    assert blocked[0]["blocker"] == "3/3 failures"

    checklist.note_unblocked(loop_id, "human re-entered research")
    item = checklist.focus()
    assert item["status"] == "doing"
    assert item["blocker"] is None

    checklist.note_done(loop_id, "verified end to end")
    done_item = next(i for i in checklist.items() if i["loop_id"] == loop_id)
    assert done_item["status"] == "done"
    assert checklist.focus() is None  # done clears the focus

    # Every hop is in the history with a note, oldest first.
    notes = [hop["note"] for hop in done_item["history"]]
    assert notes[0] == "loop created at phase 'research'"
    assert "advanced: research -> pair-plan" in notes
    assert "blocked: 3/3 failures" in notes
    assert notes[-1] == "verified end to end"


def test_checklist_brief_is_compact_not_a_dump(state_root: Path) -> None:
    checklist = working_memory.Checklist(state_root)
    loop_id = _loop_id()
    checklist.note_loop_created(loop_id, "rpi", "refactor the parser", "research")
    checklist.note_blocked(loop_id, "waiting on a human decision")

    lines = checklist.brief_lines()
    assert lines[0].startswith("CHECKLIST")
    assert loop_id in lines[0]
    assert any(line.startswith("BLOCKED") and loop_id in line for line in lines)
    # Compact: a few short lines, never the raw item JSON.
    assert len(lines) <= 4
    for line in lines:
        assert len(line) < 200
        assert '"loop_id"' not in line


def test_moves_summary_reports_only_what_moved_since_cutoff(
    state_root: Path,
) -> None:
    checklist = working_memory.Checklist(state_root)
    loop_id = _loop_id()
    checklist.note_loop_created(loop_id, "rpi", "refactor the parser", "research")
    cutoff = working_memory._now_iso()
    checklist.note_phase(loop_id, "research", "pair-plan")

    moves = checklist.moves_since(cutoff)
    assert len(moves) == 1
    assert moves[0]["from"] == "doing"
    assert moves[0]["to"] == "doing"
    assert "advanced: research -> pair-plan" in moves[0]["note"]

    lines = checklist.moves_summary_lines(cutoff)
    assert lines[0].startswith("CHECKLIST")
    assert any("advanced: research -> pair-plan" in line for line in lines)

    everything = checklist.moves_summary_lines(None)
    assert any("loop created at phase 'research'" in line for line in everything)


def test_note_verdict_synthesizes_placeholder_for_unknown_loop(
    state_root: Path,
) -> None:
    # A verdict on a loop the checklist never saw still closes honestly: the
    # item records what is known (the verdict), not an invented title.
    checklist = working_memory.Checklist(state_root)
    checklist.note_verdict("rpi-ghost", "yes", "tests all pass")
    item = next(i for i in checklist.items() if i["loop_id"] == "rpi-ghost")
    assert item["status"] == "done"
    assert item["title"] == ""
    assert item["history"][-1]["note"] == "outcome verdict: yes -- tests all pass"


# ── facts ────────────────────────────────────────────────────────────────────


def test_facts_append_and_correct_preserves_history(state_root: Path) -> None:
    facts = working_memory.Facts(state_root)
    old_id = facts.append("deploys run on docker compose v2")
    assert old_id == "F-0001"
    new_id = facts.correct(old_id, "deploys run on docker compose v2.24+", note="version pinned")
    assert new_id == "F-0002"

    raw = facts.path.read_text(encoding="utf-8")
    # The old entry is marked, never rewritten: dated pointer + intact body.
    assert "SUPERSEDED by F-0002 on" in raw
    assert "deploys run on docker compose v2\n" in raw or "deploys run on docker compose v2" in raw
    # The new entry points back and carries the correction note.
    assert "(supersedes F-0001)" in raw
    assert "Correction note: version pinned" in raw

    entries = facts.entries()
    old = facts.get(old_id)
    assert old is not None and old.superseded_by == "F-0002"
    new = facts.get(new_id)
    assert new is not None and new.supersedes == "F-0001"
    assert len(entries) == 2


def test_facts_correct_refuses_to_bury_the_latest(state_root: Path) -> None:
    facts = working_memory.Facts(state_root)
    old_id = facts.append("first claim")
    facts.correct(old_id, "second claim")
    with pytest.raises(ValueError, match="already superseded"):
        facts.correct(old_id, "third claim")


# ── decisions ────────────────────────────────────────────────────────────────


def test_decisions_record_why_and_flag_missing_why(state_root: Path) -> None:
    decisions = working_memory.Decisions(state_root)
    kept = decisions.record(
        decision="use postgres for the queue",
        why="sqlite locked under concurrent writers",
        source="pair-planning in loop rpi-1 (by human)",
        key="rpi-1:q1",
    )
    entry = decisions.by_key("rpi-1:q1")
    assert entry is not None
    assert entry.id == kept
    assert entry.why_recorded

    decisions.record(
        decision="ship the beta friday",
        why="",
        source="loop approve --by lead for loop rpi-1",
        key="rpi-1:approval",
    )
    why_less = decisions.why_less()
    assert [e.key for e in why_less] == ["rpi-1:approval"]
    assert not why_less[0].why_recorded


def test_decisions_rerecord_supersedes_not_rewrites(state_root: Path) -> None:
    decisions = working_memory.Decisions(state_root)
    first = decisions.record("scope: honda", "as asked", "pair", key="rpi-1:scope")
    second = decisions.record("scope: big", "customer needs more", "pair", key="rpi-1:scope")

    current = decisions.by_key("rpi-1:scope")
    assert current is not None and current.id == second
    old = decisions.get(first)
    assert old is not None and old.superseded_by == second
    assert "honda" in decisions.path.read_text(encoding="utf-8")  # old text kept


# ── user model ───────────────────────────────────────────────────────────────


def test_user_model_defaults_are_unknown_not_assumed(home: Path) -> None:
    model = working_memory.UserModel.load()
    assert model["wants_challenges"] is None
    assert model["preferred_stance"] is None
    assert model["narration_verbosity"] == "normal"
    assert model["recommendation_scope"] == "honda-first"
    assert working_memory.UserModel.calibration_line(model) is None


def test_verdict_learning_rules_are_deterministic(home: Path) -> None:
    model = working_memory.UserModel.load()

    changed = working_memory.UserModel.apply_verdict(
        model, "partial", "the summary was too verbose, cut it in half"
    )
    assert changed
    assert model["narration_verbosity"] == "terse"
    assert working_memory.UserModel.narration(model) == "terse"
    rule = model["learned"][-1]["rule"]
    assert rule == "RULE-VERBOSITY-TERSE"

    # Two Honda overrides flip the recommendation scope.
    working_memory.UserModel.apply_verdict(model, "yes", "I overrode the default scope")
    working_memory.UserModel.apply_verdict(model, "yes", "overriding the Honda again")
    assert model["honda_overrides"] == 2
    assert model["recommendation_scope"] == "big-first"
    assert working_memory.UserModel.recommendation_scope(model) == "big-first"


def test_learning_rules_never_touch_explicit_fields(home: Path) -> None:
    model = working_memory.UserModel.load()
    model["narration_verbosity"] = "verbose"
    model["explicit"] = ["narration_verbosity"]
    working_memory.UserModel.save(model)

    reloaded = working_memory.UserModel.load()
    changed = working_memory.UserModel.apply_verdict(
        reloaded, "no", "way too verbose, summarize"
    )
    # The rule fired but the explicit field stands; the skip is audited.
    assert not changed
    assert reloaded["narration_verbosity"] == "verbose"
    skipped = reloaded["learned"][-1]
    assert skipped["skipped"] == "explicit"
    assert skipped["rule"] == "RULE-VERBOSITY-TERSE"


def test_corrections_learn_explicitly_not_by_vibes(home: Path) -> None:
    model = working_memory.UserModel.load()
    changed = working_memory.UserModel.apply_corrections(
        model,
        [
            "good challenge, be blunt next time",
            "too many options, give me fewer options",
        ],
    )
    assert changed
    assert model["wants_challenges"] is True
    assert model["options_style"] == "few"
    rules = [entry["rule"] for entry in model["learned"]]
    assert "RULE-CHALLENGE-WANT" in rules
    assert "RULE-OPTIONS-FEW" in rules

    # Text that matches no rule changes nothing.
    unchanged = dict(model)
    assert not working_memory.UserModel.apply_corrections(model, ["thanks, looks fine"])
    assert model["learned"] == unchanged["learned"]


def test_terse_narration_calibration(home: Path) -> None:
    assert not loopctl._terse_narration()  # unlearned -> normal
    model = working_memory.UserModel.load()
    model["narration_verbosity"] = "terse"
    model["explicit"] = ["narration_verbosity"]
    working_memory.UserModel.save(model)
    assert loopctl._terse_narration()


# ── driver hooks: phase boundaries update the checklist, answers feed decisions ─


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    return project


def _driver(
    project: Path, tmp_path: Path, state_root: Path
) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
        state_root=state_root,
    )


def test_driver_new_advance_lock_update_checklist(tmp_path: Path, state_root: Path) -> None:
    driver = _driver(_project(tmp_path), tmp_path, state_root)
    state = driver.new("refactor the parser")
    checklist = working_memory.Checklist(state_root)

    item = checklist.focus()
    assert item is not None
    assert item["loop_id"] == state.id
    assert item["status"] == "doing"
    assert item["phase"] == "research"

    driver.record_failure(state, ["missing artifact"])
    driver.record_failure(state, ["missing artifact"])
    driver.record_failure(state, ["missing artifact"])
    assert state.locked
    blocked = checklist.blocked_items()
    assert len(blocked) == 1
    assert blocked[0]["loop_id"] == state.id
    assert "3/3" in blocked[0]["blocker"]


def test_driver_approve_feeds_decisions_with_why(tmp_path: Path, state_root: Path) -> None:
    driver = _driver(_project(tmp_path), tmp_path, state_root)
    state = driver.new("refactor the parser")
    driver.record_thinking_run(state, "premortem", by="human", memory_id="D-0001")
    # "Execute when comfortable and understanding": approval requires the
    # comprehension check first, even in this decisions-memory test.
    driver.record_explanation(state, "Refactor the parser in small steps.", by="human")
    driver.approve_plan(state, by="human", reason="small and safe")
    decisions = working_memory.Decisions(state_root)
    entry = decisions.by_key(f"{state.id}:approval")
    assert entry is not None
    assert entry.why_recorded
    assert "small and safe" in entry.why

    # An empty reason stays flagged as missing -- buddy prompts, never invents.
    state2 = driver.new("another task")
    driver.record_thinking_run(state2, "premortem", by="human", memory_id="D-0002")
    driver.record_explanation(state2, "Do the other task carefully.", by="human")
    driver.approve_plan(state2, by="human", reason="")
    entry2 = decisions.by_key(f"{state2.id}:approval")
    assert entry2 is not None
    assert not entry2.why_recorded
    assert entry2.why == working_memory.WHY_MISSING


def test_driver_without_state_root_stays_file_free(tmp_path: Path) -> None:
    driver = loops.RpiDriver(
        project_root=_project(tmp_path),
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
    )
    state = driver.new("refactor the parser")
    driver.record_thinking_run(state, "premortem", by="human", memory_id="D-0003")
    # Approval requires comprehension first ("execute when comfortable and
    # understanding"); no plan artifact exists, so the explanation alone is
    # the whole bar.
    driver.record_explanation(state, "Refactor the parser in small steps.", by="human")
    driver.approve_plan(state, by="human", reason="fine")
    assert not (tmp_path / ".awino" / "checklist.json").exists()


# ── buddy audit + fix ────────────────────────────────────────────────────────


def _loop_event(loop_id: str, kind: str, detail: str) -> LoopEvent:
    return LoopEvent(
        loop_id=loop_id,
        loop_kind="rpi",
        phase="pair-plan",
        kind=kind,
        at=datetime.now(UTC).isoformat(),
        detail=detail,
    )


def test_buddy_finds_and_backfills_unrecorded_decisions(state_root: Path) -> None:
    ledger = Ledger(state_root)
    ledger.record_loop_event(
        _loop_event(
            "rpi-1",
            "human_answered",
            "question=q1 kind=answer by=human: use postgres",
        )
    )
    ledger.record_loop_event(
        _loop_event("rpi-1", "approval_granted", "by=human reason=small and safe")
    )
    ledger.record_loop_event(
        _loop_event("rpi-2", "approval_granted", "by=human")
    )

    decisions = working_memory.Decisions(state_root)
    events = ledger.loop_events()
    unrecorded = buddy._unrecorded_decisions(events, decisions)
    assert len(unrecorded) == 3

    fixed, prompts = buddy._backfill_unrecorded_decisions(ledger, decisions)
    assert len(fixed) == 3
    # The why-less approval prompts the human; the fix never invents one.
    assert len(prompts) == 1
    assert "rpi-2:approval" in prompts[0]

    pair = decisions.by_key("rpi-1:q1")
    assert pair is not None and pair.why_recorded
    assert "use postgres" in pair.decision
    approval = decisions.by_key("rpi-1:approval")
    assert approval is not None and approval.why_recorded
    empty = decisions.by_key("rpi-2:approval")
    assert empty is not None and not empty.why_recorded

    # Idempotent: a second --fix finds nothing new.
    fixed_again, prompts_again = buddy._backfill_unrecorded_decisions(ledger, decisions)
    assert fixed_again == []
    assert prompts_again == []


def test_buddy_flags_stale_checklist_and_stale_fact_refs(state_root: Path) -> None:
    checklist = working_memory.Checklist(state_root)
    checklist.note_loop_created("rpi-old", "rpi", "old work", "research")
    # Backdate every hop so the checklist reads as untouched for 10 days.
    import json

    data = json.loads(checklist.path.read_text(encoding="utf-8"))
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    for item in data["items"]:
        for hop in item["history"]:
            hop["at"] = old
        item["updated_at"] = old
    data["updated_at"] = old
    checklist.path.write_text(json.dumps(data), encoding="utf-8")

    prompt = buddy._stale_checklist_prompt(checklist)
    assert prompt is not None and "10 days" in prompt

    facts = working_memory.Facts(state_root)
    old_id = facts.append("first claim")
    new_id = facts.correct(old_id, "corrected claim")
    decisions = working_memory.Decisions(state_root)
    decisions.record(
        decision="ship it", why=f"because {old_id} said so", source="test"
    )
    refs = buddy._stale_fact_refs(state_root, facts)
    assert refs == [(old_id, new_id, "decisions.md")]


# ── session end: facts promoted, corrections learned ─────────────────────────


def test_session_end_writes_memory_deltas(
    tmp_path: Path, state_root: Path, home: Path
) -> None:
    project = _project(tmp_path)
    session_state.start(state_root, "sess-1")
    session_log.append(state_root, "sess-1", "fact", "deploys run on docker compose v2")
    session_log.append(state_root, "sess-1", "correction", "be blunt, push back more")
    checklist = working_memory.Checklist(state_root)
    checklist.note_loop_created("rpi-1", "rpi", "refactor the parser", "research")
    since = session_markers.last_session_end_time(state_root)

    lines = playbook.run_event(
        "session-end", state_root, project, ledger=Ledger(state_root), open_seeds=[]
    )
    assert any("memory-write" in line for line in lines)
    assert any("fact recorded: F-0001" in line for line in lines)
    assert any("user model" in line and "RULE-CHALLENGE-WANT" in line for line in lines)

    facts = working_memory.Facts(state_root)
    assert facts.get("F-0001") is not None
    assert "docker compose v2" in facts.get("F-0001").text

    model = working_memory.UserModel.load()
    assert model["wants_challenges"] is True

    # Re-running session-end is idempotent: no double promotion.
    again = playbook.run_event(
        "session-end", state_root, project, ledger=Ledger(state_root), open_seeds=[]
    )
    assert not any("fact recorded" in line for line in again)
    assert len(facts.entries()) == 1

    # The checklist summary sees what moved since the marker.
    summary = checklist.moves_summary_lines(since)
    assert any("loop created at phase 'research'" in line for line in summary)


def test_session_end_without_session_skips_memory_deltas(
    tmp_path: Path, state_root: Path
) -> None:
    lines = playbook.run_event(
        "session-end",
        state_root,
        _project(tmp_path),
        ledger=Ledger(state_root),
        open_seeds=[],
    )
    assert any("no active session: memory deltas skipped" in line for line in lines)
