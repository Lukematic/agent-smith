"""Deep Plan: define the problem, a grill the engine keeps honest (and that
must challenge), prioritized steps with checks that have teeth where plans
usually lie, a compiled plan the harness binds the run to, and the steps in the
Seeds tracker. The samples below are a worked example (making a tiny notes
service per-user) and double as the passing fixtures."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import cli, deepplan, ladder
from awino.dispatch import decide
from awino.enforce import Gate, Ledger, LedgerError
from awino.loops import PLAN_SECTIONS
from awino.skill_catalog import SkillCatalog

ROOT = Path(__file__).parents[1]

APP = '''"""A tiny notes service."""

from notes.store import Store

store = Store()


def add_note(text: str) -> int:
    return store.add(text)


def list_notes() -> list[str]:
    return store.all()
'''

STORE = """class Store:
    def __init__(self) -> None:
        self._notes: list[str] = []

    def add(self, text: str) -> int:
        self._notes.append(text)
        return len(self._notes)

    def all(self) -> list[str]:
        return list(self._notes)
"""

FRAME = """# Per-user notes

## Problem
Two teams share one install and each can read the other's notes today, so nobody
writes anything sensitive in it and the pilot starts Monday.
Evidence: notes/app.py:5 keeps one Store for everyone; the team lead's words, "we can see HR's notes".

## Goal
Each signed-in user sees and edits only their own notes.

## Done when
- C1: `add_note` and `list_notes` take a user id, and a user lists only their own notes.
- C2: A test shows user B cannot list user A's notes.

## Break it down
- Who is asking: every call has to know the user.
- Where notes live: one user's notes must be unreachable from another's.
- What exists: today's callers and tests assume one shared list.

## What's needed
- A user id on every call (the pilot's login already provides one).
- Sign-off from the team lead that old notes can be dropped.

## Constraints
- No new dependencies; the store stays in memory for v1.

## Out of scope
- Sharing notes between users.
"""

DIRECTIONS = """# Directions

## Variations
- Owner field on every note, filter on read. Lens: the 10x simpler version
- One store per user, created on first use. Lens: remove a constraint
- Encrypt each user's notes with their key. Lens: what an expert would find obvious
- Notes public by default, private on request. Lens: the opposite
- Teams own notes and users inherit access. Lens: another audience
- Reuse the session's user id as the partition key. Lens: combine with something nearby

## Directions
### A: owner field and filtering
Value: smallest change; every caller passes the user.
Hardest part: a caller that forgets the user id leaks everything.
Assumption: callers always know the user id.

### B: one store per user
Value: isolation by construction; a bug cannot cross users.
Hardest part: the shared notes that exist today.
Assumption: no note needs to be visible to two users.

## Recommendation
B, because isolation by construction beats remembering to filter. A wins if sharing is coming soon.
"""

RECON = """# Recon

## Code that matters
- notes/store.py:1 the in-memory store every note goes through.
- notes/app.py:5 the single shared store instance: this is the leak.
- notes/app.py:8 add_note, the entry point that must take a user.

## Reuse
- Store already adds and lists; keep it and hold one per user.

## How we verify
`python -m pytest -q`

## Risks
- Callers outside this repo call add_note without a user id.
"""

SLICES = """# Slices

## Slices
### 1. One store per user
Priority: P0
Files: notes/app.py, tests/test_isolation.py (new)
Depends on: none
Verify: `python -m pytest -q tests/test_isolation.py`
Done when: the new test shows user B cannot list user A's notes.
Covers: C1, C2

### 2. Keep the old callers working
Priority: P0
Files: notes/app.py, tests/test_app.py
Depends on: 1
Verify: `python -m pytest -q`
Done when: the existing tests pass with an explicit user.
Covers: C1

### 3. Count notes per user for the admin page
Priority: P2
Files: notes/app.py
Depends on: 1
Verify: `python -m pytest -q tests/test_app.py`
Done when: the count matches the notes each user added.
Covers: C1

## Critical path
1 -> 2

## Will change
- notes/app.py, tests/

## Will not change
- notes/store.py
"""

REDTEAM = """# Red team

## Premortem
### A caller forgets the user id
Warning sign: notes appear under the wrong user.
Mitigation: the user id is a required argument, with no default.

### Memory grows per user
Warning sign: process memory climbs with active users.
Mitigation: the pilot is two teams; a follow-up tracks a real store.

### Existing notes vanish
Warning sign: users report empty lists after the deploy.
Mitigation: notes are in memory and reset on every deploy today; the release note says so.

## Objections
- Why not a database? Answer: out of scope for v1; the Store interface stays, so swapping it is one slice.
- Per-user stores make sharing harder later. Answer: sharing is out of scope, and direction A stays the fallback.

## Rollback
Revert the slice's commit; the store module is untouched.
"""


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    return fake


@pytest.fixture()
def project(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("AWINO_HOME", "SMITH_HOME", "AWINO_PROJECT", "SMITH_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    proj = tmp_path / "proj"
    (proj / "notes").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / "notes" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "notes" / "app.py").write_text(APP, encoding="utf-8")
    (proj / "notes" / "store.py").write_text(STORE, encoding="utf-8")
    (proj / "tests" / "test_app.py").write_text(
        "from notes.app import add_note\n\n\ndef test_add() -> None:\n    assert add_note('x')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture()
def session(project: Path) -> deepplan.Session:
    return deepplan.start(project / ".awino", project, "Per-user notes")


def _write_and_record(session: deepplan.Session, stage: str, text: str) -> list[str]:
    (session.path / f"{stage}.md").write_text(text, encoding="utf-8")
    return deepplan.record(session, stage, text)


def _grill(session: deepplan.Session) -> None:
    deepplan.ask(session, "Private by default?", "Yes, private", "decides the data model")
    deepplan.answer(session, "private, and admins can't read them either")
    deepplan.ask(session, "Is a user id always known?", "Yes, from the session", "API shape")
    deepplan.answer(session, accept=True)
    deepplan.learn(session, "One shared Store instance holds every note", "notes/app.py:5")
    deepplan.ask(
        session,
        "You said 'private'. Does that hold if a user leaves the team, or do their notes go too?",
        "They go: notes are personal, not team records",
        "if notes are team records, per-user stores are the wrong design",
        challenge=True,
    )
    deepplan.answer(session, "they go with the person")
    assert deepplan.finish_grill(session) == []


def _through_slices(session: deepplan.Session) -> None:
    assert _write_and_record(session, "frame", FRAME) == []
    _grill(session)
    assert _write_and_record(session, "directions", DIRECTIONS) == []
    deepplan.confirm(session, "directions", "B, one store per user")
    assert _write_and_record(session, "recon", RECON) == []
    assert _write_and_record(session, "slices", SLICES) == []


def _complete(session: deepplan.Session) -> None:
    _through_slices(session)
    assert _write_and_record(session, "redteam", REDTEAM) == []


# ── the samples pass, so every check is achievable ───────────────────────────


def test_a_full_session_records_every_stage(session: deepplan.Session) -> None:
    _complete(session)
    assert session.next_stage() is None
    assert all(session.recorded(name) for name in deepplan.STAGE_NAMES)
    assert session.confirmed("directions")


def test_sessions_live_in_thoughts_plans_with_an_active_pointer(project: Path) -> None:
    session = deepplan.start(project / ".awino", project, "Per-user notes")
    assert session.path.parent == project / "thoughts" / "plans"
    assert (session.path / "brief.md").is_file()
    assert deepplan.active(project / ".awino", project).id == session.id
    second = deepplan.start(project / ".awino", project, "Per-user notes")
    assert second.id == f"{session.id}-2"
    assert deepplan.use(project / ".awino", project, session.id).id == session.id
    assert [s.id for s in deepplan.sessions(project)] == [session.id, second.id]


# ── the grill: one question at a time, the human's words, evidence from code ─


def test_the_grill_needs_a_frame_first(session: deepplan.Session) -> None:
    with pytest.raises(deepplan.PlanError, match="frame first"):
        deepplan.ask(session, "Who is it for?", "The pilot teams", "")


def test_one_question_at_a_time(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    deepplan.ask(session, "Private by default?", "Yes", "data model")
    with pytest.raises(deepplan.PlanError, match="One question at a time"):
        deepplan.ask(session, "And the API shape?", "user id first", "")
    with pytest.raises(deepplan.PlanError, match="their words"):
        deepplan.answer(session, "   ")
    q = deepplan.answer(session, "yes")
    assert q["by"] == "human" and q["answer"] == "yes"
    with pytest.raises(deepplan.PlanError, match="no open question"):
        deepplan.answer(session, "again")


def test_a_question_needs_a_recommendation(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    with pytest.raises(deepplan.PlanError, match="recommended answer"):
        deepplan.ask(session, "Private by default?", " ", "")


def test_accept_records_the_recommendation_as_theirs(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    deepplan.ask(session, "Private by default?", "Yes, private", "")
    q = deepplan.answer(session, accept=True)
    assert q["answer"] == "Yes, private" and q["by"].startswith("human")


def test_defer_needs_a_reason_and_does_not_count_as_an_answer(
    session: deepplan.Session,
) -> None:
    _write_and_record(session, "frame", FRAME)
    deepplan.ask(session, "Export format?", "CSV", "")
    with pytest.raises(deepplan.PlanError, match="why it can wait"):
        deepplan.defer(session, "")
    q = deepplan.defer(session, "export is out of scope for v1")
    assert q["status"] == "deferred"
    assert "answered by the human" in " ".join(deepplan.finish_grill(session))


@pytest.mark.parametrize(
    ("evidence", "why"),
    [
        ("notes/missing.py:1", "does not exist"),
        ("notes/app.py:999", "has 14 lines"),
        ("../outside.py:1", "outside the project"),
        ("notes/app.py", "not path:line"),
    ],
)
def test_learn_needs_a_real_line_of_code(
    session: deepplan.Session, project: Path, evidence: str, why: str
) -> None:
    (project.parent / "outside.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(deepplan.PlanError, match=why):
        deepplan.learn(session, "something true", evidence)


def test_learned_facts_do_not_count_as_human_answers(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    for line in (1, 5, 8):
        deepplan.learn(session, f"fact {line}", f"notes/app.py:{line}")
    problems = deepplan.finish_grill(session)
    assert any("0 question(s) answered by the human" in p for p in problems)
    assert deepplan.finish_grill(session, enough="the code answered everything") == []
    assert session.stage_state("grill")["enough"] == "the code answered everything"


def test_questions_and_code_facts_are_numbered_apart(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    assert deepplan.learn(session, "one shared store", "notes/app.py:5")["id"] == "F1"
    assert deepplan.ask(session, "Private?", "Yes", "")["id"] == "Q1"
    deepplan.answer(session, "yes")
    assert deepplan.learn(session, "add_note takes text", "notes/app.py:8")["id"] == "F2"
    assert deepplan.ask(session, "Keep old notes?", "No", "")["id"] == "Q2"


def test_the_grill_cannot_finish_with_a_question_open(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    deepplan.ask(session, "Private?", "Yes", "")
    problems = deepplan.finish_grill(session, enough="small change")
    assert any("Q1 is still open" in p for p in problems)


def test_the_grill_is_not_a_document(session: deepplan.Session) -> None:
    assert "conversation" in deepplan.record(session, "grill", "# my grill notes")[0]


# ── order and the human's checkpoint ─────────────────────────────────────────


def test_stages_go_in_order(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    problems = _write_and_record(session, "directions", DIRECTIONS)
    assert problems == ["'grill' comes first (awino deepplan grill-done)"]


def test_directions_wait_for_the_human(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    _grill(session)
    _write_and_record(session, "directions", DIRECTIONS)
    problems = _write_and_record(session, "recon", RECON)
    assert "checkpoint" in problems[0] and "confirm directions" in problems[0]
    with pytest.raises(deepplan.PlanError, match="their words"):
        deepplan.confirm(session, "directions", " ")
    with pytest.raises(deepplan.PlanError, match="not a checkpoint"):
        deepplan.confirm(session, "frame", "ok")


def test_re_recording_a_checkpoint_needs_the_human_again(session: deepplan.Session) -> None:
    _through_slices(session)
    _write_and_record(session, "directions", DIRECTIONS + "\nOne more thought.\n")
    assert not session.confirmed("directions")
    assert session.next_stage().name == "directions"


# ── checks with teeth, where plans usually lie ───────────────────────────────


def test_done_criteria_need_ids_and_more_than_one(session: deepplan.Session) -> None:
    bad = FRAME.replace("- C1: `add_note`", "- `add_note`").replace(
        "- C2: A test shows user B cannot list user A's notes.\n", ""
    )
    problems = deepplan.validate(session, "frame", bad)
    assert "1 done criterion; give at least two" in problems
    assert any("has no id" in p for p in problems)


def test_the_problem_is_a_problem_not_a_solution(session: deepplan.Session) -> None:
    solution = FRAME.replace(
        "Two teams share one install and each can read",
        "Add a user id to every call. Two teams share one install and each can read",
    )
    problems = deepplan.validate(session, "frame", solution)
    assert any("reads like a solution ('Add ...')" in p for p in problems)


def test_the_problem_needs_evidence_or_an_honest_inferred(session: deepplan.Session) -> None:
    bare = re.sub(r"Evidence: .*\n", "", FRAME)
    problems = deepplan.validate(session, "frame", bare)
    assert any("'Problem' needs Evidence:" in p for p in problems)
    marked = bare.replace("the pilot starts Monday.", "the pilot starts Monday. [inferred]")
    assert deepplan.validate(session, "frame", marked) == []


def test_the_problem_is_broken_down_and_needs_are_named(session: deepplan.Session) -> None:
    thin = re.sub(r"## Break it down\n(- .*\n)+", "## Break it down\n- one part\n", FRAME)
    thin = re.sub(r"## What's needed\n(- .*\n)+", "", thin)
    problems = deepplan.validate(session, "frame", thin)
    assert "'Break it down' needs at least two parts of the problem" in problems
    assert "missing section: 'What's needed'" in problems


def test_the_grill_has_to_challenge(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    for n in range(3):
        deepplan.ask(session, f"Question {n}?", "yes", "")
        deepplan.answer(session, "yes")
    problems = deepplan.finish_grill(session)
    assert problems and "no challenge yet" in problems[0]
    q = deepplan.ask(session, "Is 'fast' a number?", "under 200 ms", "", challenge=True)
    assert q["challenge"] is True
    deepplan.answer(session, "under 200 ms at p95")
    assert deepplan.finish_grill(session) == []
    assert "| Q4 | (challenge) Is 'fast' a number?" in deepplan.grill_record(session)


def test_a_rambling_goal_is_refused(session: deepplan.Session) -> None:
    bad = FRAME.replace(
        "Each signed-in user sees and edits only their own notes.",
        "Users see their notes. Only theirs. Also edits. And more.",
    )
    assert any("say it in one" in p for p in deepplan.validate(session, "frame", bad))


def test_directions_go_wide_then_narrow(session: deepplan.Session) -> None:
    narrow = re.sub(r"(- .*Lens: .*\n){3}", "", DIRECTIONS, count=1)
    problems = deepplan.validate(session, "directions", narrow)
    assert any("go wider" in p for p in problems)
    no_lens = DIRECTIONS.replace("Lens: the opposite", "")
    assert any("has no 'Lens:'" in p for p in deepplan.validate(session, "directions", no_lens))
    no_bet = DIRECTIONS.replace("Assumption: callers always know the user id.", "")
    assert any("Assumption:" in p for p in deepplan.validate(session, "directions", no_bet))


def test_recon_cites_code_that_exists(session: deepplan.Session) -> None:
    fake = RECON.replace("notes/store.py:1", "notes/auth.py:12")
    problems = deepplan.validate(session, "recon", fake)
    assert "reference notes/auth.py:12: notes/auth.py does not exist" in problems
    assert "2 real path:line reference(s); read and cite at least three" in problems


def test_recon_verification_must_be_a_command_that_runs(session: deepplan.Session) -> None:
    prose = RECON.replace("`python -m pytest -q`", "`tests pass`")
    problems = deepplan.validate(session, "recon", prose)
    assert any("runs on this machine" in p for p in problems)


def test_every_criterion_needs_a_slice(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    uncovered = SLICES.replace("Covers: C1, C2", "Covers: C1")
    problems = deepplan.validate(session, "slices", uncovered)
    assert "no slice covers C2: every done criterion needs one" in problems


def test_slices_name_real_files_or_mark_them_new(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    unmarked = SLICES.replace("tests/test_isolation.py (new)", "tests/test_isolation.py")
    problems = deepplan.validate(session, "slices", unmarked)
    assert "tests/test_isolation.py does not exist; fix the path or mark it (new)" in problems


def test_a_slice_verify_must_run_here(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    prose = SLICES.replace("`python -m pytest -q tests/test_isolation.py`", "the tests pass")
    problems = deepplan.validate(session, "slices", prose)
    assert any("1. One store per user" in p and "backticks" in p for p in problems)


def test_a_fat_slice_must_be_split(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    fat = SLICES.replace(
        "Files: notes/app.py, tests/test_isolation.py (new)",
        "Files: notes/app.py, a.py (new), b.py (new), c.py (new), d.py (new), e.py (new)",
    )
    problems = deepplan.validate(session, "slices", fat)
    assert any("touches 6 files; split it" in p for p in problems)


def test_one_slice_is_not_a_breakdown(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    one = SLICES.split("### 2.")[0] + "## Critical path\nx\n## Will change\nx\n"
    one += "## Will not change\nx\n"
    assert "1 slice(s); use two to twelve" in deepplan.validate(session, "slices", one)


def test_every_step_has_a_priority_and_p0_comes_first(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    missing = SLICES.replace("Priority: P2\n", "", 1)
    problems = deepplan.validate(session, "slices", missing)
    assert any("has no 'Priority: P0|P1|P2'" in p for p in problems)
    swapped = SLICES.replace(
        "Priority: P0\nFiles: notes/app.py, tests/test_app.py",
        "Priority: P1\nFiles: notes/app.py, tests/test_app.py",
    )
    swapped = swapped.replace("Priority: P2", "Priority: P0")
    problems = deepplan.validate(session, "slices", swapped)
    assert any("is P0 after a P1 step" in p for p in problems)


def test_done_never_waits_on_a_nice_to_have(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    # C2 is only in step 1; demote it and C2 is covered by nothing that must happen.
    demoted = SLICES.replace("Covers: C1, C2", "Covers: C1").replace(
        "Covers: C1\n\n## Critical", "Covers: C1, C2\n\n## Critical"
    )
    problems = deepplan.validate(session, "slices", demoted)
    assert "C2 only covered by P1/P2 steps: done needs a P0 step for each criterion" in problems
    no_p0 = SLICES.replace("Priority: P0", "Priority: P1")
    assert "no P0 step: done needs at least one must-have" in deepplan.validate(
        session, "slices", no_p0
    )


def test_dependencies_name_earlier_steps(session: deepplan.Session) -> None:
    _write_and_record(session, "frame", FRAME)
    forward = SLICES.replace("Depends on: none", "Depends on: 2", 1)
    problems = deepplan.validate(session, "slices", forward)
    assert any("depends on step 2, which is not an earlier step" in p for p in problems)
    vague = SLICES.replace(
        "Depends on: 1\nVerify: `python -m pytest -q`",
        "Depends on: the first one\nVerify: `python -m pytest -q`",
    )
    problems = deepplan.validate(session, "slices", vague)
    assert any("takes earlier step numbers (1, 2) or none" in p for p in problems)


def test_the_red_team_has_to_attack(session: deepplan.Session) -> None:
    soft = REDTEAM.split("### Memory grows")[0] + REDTEAM[REDTEAM.index("## Objections") :]
    problems = deepplan.validate(session, "redteam", soft)
    assert "1 failure reason(s); find at least three" in problems
    no_answer = REDTEAM.replace("Answer: sharing", "Sharing")
    assert any("has no 'Answer:'" in p for p in deepplan.validate(session, "redteam", no_answer))


# ── the plan the harness executes ────────────────────────────────────────────


def test_compile_waits_for_every_stage(session: deepplan.Session) -> None:
    _through_slices(session)
    with pytest.raises(deepplan.PlanError, match="redteam still to do"):
        deepplan.compile_plan(session)


def test_the_compiled_plan_is_in_the_format_the_harness_checks(
    session: deepplan.Session,
) -> None:
    _complete(session)
    text = deepplan.compile_plan(session).read_text(encoding="utf-8")
    headings = [h.lower() for h in re.findall(r"^#+\s+(.+)$", text, re.M)]
    for section, synonyms in PLAN_SECTIONS.items():
        assert any(any(s in h for s in synonyms) for h in headings), section
    assert "## Phase 1 — One store per user (P0)" in text
    assert "## Phase 3 — Count notes per user for the admin page (P2)" in text
    assert "- [ ] notes/app.py" in text and "- [ ] tests/test_isolation.py (new)" in text
    assert "**Automated success criteria:** `python -m pytest -q tests/test_isolation.py`" in text
    assert "Depends on: phase 1" in text and "Priority: P2 (could, later)" in text
    # Problem first, what is needed, and the priorities, before any step.
    assert text.index("## Problem") < text.index("## Goal") < text.index("## Phase 1")
    assert "Evidence: notes/app.py:5 keeps one Store" in text
    assert "## What's needed" in text and "Sign-off from the team lead" in text
    assert "- P0 (done needs it): phase 1, 2" in text and "- P2 (could, later): phase 3" in text
    # The grill is the decision record: the human's words and what the code showed.
    assert "private, and admins can't read them either | human" in text
    assert "One shared Store instance holds every note (notes/app.py:5) | code" in text
    assert "Direction chosen by the human: B, one store per user" in text
    assert "(challenge) You said 'private'." in text
    assert "C2: A test shows user B" in text


def test_phases_cannot_be_verified_before_approval(session: deepplan.Session) -> None:
    _complete(session)
    deepplan.compile_plan(session)
    with pytest.raises(deepplan.PlanError, match="not approved"):
        deepplan.next_phase_command(session, 1)
    assert [
        (ph.number, ph.title, ph.command, ph.priority, ph.depends)
        for ph in deepplan.phases(session)
    ] == [
        (1, "One store per user", "python -m pytest -q tests/test_isolation.py", 0, ()),
        (2, "Keep the old callers working", "python -m pytest -q", 0, (1,)),
        (
            3,
            "Count notes per user for the admin page",
            "python -m pytest -q tests/test_app.py",
            2,
            (1,),
        ),
    ]


def test_scope_is_the_slices_files_plus_the_session_folder(session: deepplan.Session) -> None:
    _complete(session)
    rel = session.path.relative_to(session.project).as_posix()
    assert deepplan.scope(session) == [
        "notes/app.py",
        "tests/test_isolation.py",
        "tests/test_app.py",
        f"{rel}/",
    ]


def test_the_tracker_is_in_scope_when_the_project_has_one(
    session: deepplan.Session, project: Path
) -> None:
    _complete(session)
    assert ".seeds/" not in deepplan.scope(session)
    (project / ".seeds").mkdir()
    assert deepplan.scope(session)[-1] == ".seeds/"


def test_an_early_seed_record_keyed_by_number_still_resolves(session: deepplan.Session) -> None:
    _complete(session)
    session.data["seeds"] = {"epic": "e-1", "steps": {"1": "s-9"}}
    assert deepplan.seed_for(session, 1) == "s-9"
    assert deepplan.seed_for(session, 2) is None
    assert deepplan.seed_for(session, 7) is None


def test_a_plan_edited_after_compile_must_be_shown_again(session: deepplan.Session) -> None:
    _complete(session)
    with pytest.raises(deepplan.PlanError, match="no plan yet"):
        deepplan.approvable_plan(session)
    plan = deepplan.compile_plan(session)
    assert deepplan.approvable_plan(session) == plan
    plan.write_text(plan.read_text(encoding="utf-8") + "\n- [ ] sneak in a refactor\n")
    with pytest.raises(deepplan.PlanError, match="no longer matches"):
        deepplan.approvable_plan(session)


# ── routing ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def catalog() -> SkillCatalog:
    return SkillCatalog(
        project_root=Path("/nonexistent-project-root"),
        global_root=Path("/nonexistent-global-root"),
        bundled_root=ROOT / "skills",
    )


@pytest.mark.parametrize(
    "request_text",
    [
        "I want to plan the auth rewrite in depth before we build anything",
        "deep plan: per-user notes",
        "grill me on this feature before we start coding",
        "let's brainstorm and break the problem down into steps before executing",
        "planning mode: think this through with me",
        "help me define the problem, break it down, how will we know its done",
        "a thinking and planning session: make steps, break by priority, then create seeds",
        "prioritize the steps for the auth rewrite and put them in seeds",
    ],
)
def test_planning_requests_route_to_deep_plan(catalog: SkillCatalog, request_text: str) -> None:
    decision = decide(request_text, catalog)
    assert decision.skill is not None and decision.skill.name == "awino-deepplan"


def test_sponsor_brainstorms_still_route_to_brain(catalog: SkillCatalog) -> None:
    decision = decide("help me brainstorm what we can offer this sponsor", catalog)
    assert decision.skill is not None and decision.skill.name == "awino-brain"


def test_deep_plan_runs_as_a_direct_loop() -> None:
    choice = ladder.choose("plan this in depth", "awino-deepplan", "pytest", ["a.py"])
    assert choice.loop == "direct"


# ── the CLI, end to end, into a gated run ────────────────────────────────────


def _run(runner: CliRunner, *args: str, code: int = 0) -> str:
    out = runner.invoke(cli.app, ["deepplan", *args])
    assert out.exit_code == code, out.output
    return out.output


def _gate(runner: CliRunner, *args: str, code: int = 0) -> str:
    out = runner.invoke(cli.app, ["gate", *args])
    assert out.exit_code == code, out.output
    return out.output


def _where(project: Path) -> str:
    return "\n".join(deepplan.where(cli._workspace().state_root, project))


def test_cli_walks_a_session_into_a_bound_run(project: Path) -> None:
    runner = CliRunner()
    assert "NO_SESSION" in _run(runner, code=1)
    out = _run(runner, "start", "Per-user notes")
    assert "STARTED" in out and "STAGE  1/6 frame" in out
    session = deepplan.active(cli._workspace().state_root, project)
    assert session is not None
    # A fresh context (new chat, after compaction) is told where the plan stands.
    assert "planning, next stage 'frame'" in _where(project)

    assert "nothing to record" in _run(runner, "record", "frame", code=2)
    (session.path / "frame.md").write_text(
        FRAME.replace("## Problem", "## Background"), encoding="utf-8"
    )
    assert "NOT_YET  frame" in _run(runner, "record", "frame", code=1)
    (session.path / "frame.md").write_text(FRAME, encoding="utf-8")
    assert "RECORDED  frame" in _run(runner, "record", "frame")

    out = _run(runner, "ask", "Private by default?", "--recommend", "Yes", "--why", "data model")
    assert "ASKED  Q1" in out and "STOP" in out
    assert "still open" in _run(runner, "ask", "Next?", "--recommend", "x", code=2)
    assert "OPEN  Q1: Private by default?" in _run(runner)
    assert "Q1 waits for the human's answer: Private by default?" in _where(project)
    assert "ANSWERED  Q1: private" in _run(runner, "answer", "private")
    out = _run(runner, "learn", "one shared store", "--evidence", "notes/app.py:5")
    assert "LEARNED  F1" in out
    assert "NOT_YET  grill" in _run(runner, "grill-done", code=1)
    out = _run(runner, "grill-done", "--enough", "small change, the code answered the rest")
    assert "RECORDED  grill" in out and "| F1 |" in out

    (session.path / "directions.md").write_text(DIRECTIONS, encoding="utf-8")
    assert "CHECKPOINT  stop here" in _run(runner, "record", "directions")
    assert "WAITING for the human" in _run(runner)
    assert "'directions' waits for the human's pick" in _where(project)
    assert "CONFIRMED  directions" in _run(
        runner, "confirm", "directions", "--note", "B, one store per user"
    )
    for stage, text in (("recon", RECON), ("slices", SLICES), ("redteam", REDTEAM)):
        (session.path / f"{stage}.md").write_text(text, encoding="utf-8")
        assert f"RECORDED  {stage}" in _run(runner, "record", stage)
    assert "NEXT  awino deepplan compile" in _run(runner)

    approve = ("go", "--by", "Luke", "--note", "approved, start with slice 1")
    assert "no plan yet" in _run(runner, *approve, code=2)
    out = _run(runner, "compile")
    assert "PLAN" in out and "STOP  show the plan to the human" in out
    assert "waiting for the human's approval" in _where(project)
    assert "not awino gate open" in _where(project)

    # Opening the gate by hand on a Deep Plan would skip the approval and the phases.
    plan = session.path / "plan.md"
    out = _gate(runner, "open", "code-change", "x", "--plan", str(plan), code=2)
    assert "DEEP_PLAN_RUN" in out and "awino deepplan go" in out

    out = _run(runner, *approve)
    assert "PLAN_APPROVED" in out and "loop=rpi" in out and "Never edit plan.md" in out
    assert "next: executing 0/3" in _run(runner, "list")

    # The run is bound to the exact plan the human approved.
    run_id = re.search(r"^RUN (\S+)", out, re.M).group(1)
    ledger = Ledger(cli._workspace().state_root)
    run = ledger.load(run_id)
    assert run.loop == "rpi" and Path(run.plan_path) == plan.resolve()
    assert run.plan_decisions[-1].plan_sha256 == hashlib.sha256(plan.read_bytes()).hexdigest()
    assert "notes/app.py" in run.file_scope
    assert ledger.validate_plan(run_id) == []
    progress = (session.path / "progress.md").read_text()
    assert progress.count("- [ ] Phase") == 3 and "- [ ] Phase 3 (P2):" in progress
    assert "executing, 0/2 P0 phases verified" in _where(project)
    assert "awino deepplan done 1" in _where(project)

    # What a phase depends on comes first, each verified by its own command.
    assert "phase 2 depends on phase 1: verify phase 1 first" in _run(runner, "done", "2", code=2)
    out = _run(runner, "done", "1", code=1)  # the isolation test does not exist yet
    assert "FAIL  tested" in out and "python -m pytest -q tests/test_isolation.py" in out
    assert deepplan.done_phases(session) == set()
    (project / "tests" / "test_isolation.py").write_text(
        "def test_isolated() -> None:\n    assert True\n", encoding="utf-8"
    )
    out = _run(runner, "done", "1")
    assert "PASS  tested" in out and "PHASE_VERIFIED  1" in out and "P0 1/2" in out
    assert "already verified" in _run(runner, "done", "1", code=2)
    assert "the plan has phases 1 to 3" in _run(runner, "done", "4", code=2)
    # Its dependency is met, but the must-haves come before the nice-to-have.
    out = _run(runner, "done", "3", code=2)
    assert "phase 3 is P2; P0 phases come first: verify phase 2 first" in out
    assert "[x] Phase 1 P0" in _run(runner)
    evidence = [e for e in ledger.evidence(run_id) if e.gate == "tested"]
    assert [e.passed for e in evidence] == [False, True]

    # The run cannot close while a phase of the approved plan is unverified.
    out = _gate(runner, "close", code=1)
    assert "DEEP_PLAN_PHASES_UNVERIFIED  phase(s) 2" in out and "deepplan done 2" in out
    out = _run(runner, "done", "2")
    assert "PHASE_VERIFIED  2" in out and "DONE_CRITERIA  every P0 phase is verified" in out
    assert "OPTIONAL  phase(s) 3 (P1/P2)" in out and "gate close" in out
    assert "every P0 phase verified (done); 1 P1/P2 phase(s) open" in _where(project)
    assert "done (P0), 1 optional open" in _run(runner, "list")
    # Done is the P0 phases: the P2 phase is a follow-up, not a blocker.
    out = _gate(runner, "close", code=1)  # the other gates still need their evidence
    assert "DEEP_PLAN_PHASES_UNVERIFIED" not in out and "linted" in out
    assert "FOLLOW_UP  phase 3 (P2) Count notes per user" in out

    # Ticking boxes in plan.md would void the approval; progress.md is where they go.
    plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1), encoding="utf-8")
    with pytest.raises(LedgerError, match="PLAN_INVALID"):
        ledger.record(run_id, Gate.TESTED, "python -c pass")
    assert "PLAN_INVALID" in _gate(runner, "record", "linted", "--cmd", "python -c pass", code=1)


# ── the steps in the Seeds tracker ───────────────────────────────────────────


class _FakeTracker:
    """Records what Deep Plan asks of a tracker; ids are handed out in order."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.created: list[dict] = []
        self.deps: list[tuple[str, str]] = []
        self.plans: list[tuple[str, list[str]]] = []
        self.fail_on = fail_on

    def create(self, title, *, issue_type="task", priority=2, description="", labels=None):
        from awino.seeds import SeedsResult

        if self.fail_on and self.fail_on in title:
            return SeedsResult(False, "create", "tracker is locked")
        issue = f"s-{len(self.created) + 1}"
        self.created.append(
            {
                "id": issue,
                "title": title,
                "type": issue_type,
                "priority": priority,
                "description": description,
                "labels": labels,
            }
        )
        return SeedsResult(True, "create", issue)

    def depend(self, issue, on):
        from awino.seeds import SeedsResult

        self.deps.append((issue, on))
        return SeedsResult(True, "dep add", "")

    def plan_create(self, seed, name):
        from awino.seeds import SeedsResult

        return SeedsResult(True, "plan create", "pl-1")

    def plan_adopt(self, plan, ids):
        from awino.seeds import SeedsResult

        self.plans.append((plan, ids))
        return SeedsResult(True, "plan adopt", "")


def test_seeds_wait_for_a_compiled_plan(session: deepplan.Session) -> None:
    _complete(session)
    with pytest.raises(deepplan.PlanError, match="compile the plan first"):
        deepplan.create_seeds(session, _FakeTracker())


def test_each_step_becomes_a_seed_with_its_priority_and_dependencies(
    session: deepplan.Session,
) -> None:
    _complete(session)
    deepplan.compile_plan(session)
    tracker = _FakeTracker()
    record = deepplan.create_seeds(session, tracker)
    epic, *steps = tracker.created
    assert epic["type"] == "epic" and epic["title"] == "Plan: Per-user notes"
    assert "Problem: Two teams share one install" in epic["description"]
    assert [(s["title"], s["priority"]) for s in steps] == [
        ("P0 One store per user", 0),
        ("P0 Keep the old callers working", 0),
        ("P2 Count notes per user for the admin page", 2),
    ]
    assert steps[0]["labels"] == ["deepplan", "p0"]
    assert "Verify: `python -m pytest -q tests/test_isolation.py`" in steps[0]["description"]
    assert "awino deepplan done 1" in steps[0]["description"]
    # Steps 2 and 3 both need step 1; the plan links the steps in order.
    assert tracker.deps == [("s-3", "s-2"), ("s-4", "s-2")]
    assert tracker.plans == [("pl-1", ["s-2", "s-3", "s-4"])]
    assert record["epic"] == "s-1" and record["plan"] == "pl-1"
    assert record["steps"] == {
        "One store per user": "s-2",
        "Keep the old callers working": "s-3",
        "Count notes per user for the admin page": "s-4",
    }
    assert deepplan.seed_for(session, 2) == "s-3"
    # Running it again creates nothing new.
    again = _FakeTracker()
    deepplan.create_seeds(session, again)
    assert again.created == [] and again.deps == []


def test_a_tracker_failure_halfway_never_duplicates(session: deepplan.Session) -> None:
    _complete(session)
    deepplan.compile_plan(session)
    with pytest.raises(deepplan.PlanError, match="phase 2: tracker is locked"):
        deepplan.create_seeds(session, _FakeTracker(fail_on="Keep the old"))
    assert session.data["seeds"]["steps"] == {"One store per user": "s-2"}
    retry = _FakeTracker()
    deepplan.create_seeds(session, retry)
    assert [c["title"] for c in retry.created] == [
        "P0 Keep the old callers working",
        "P2 Count notes per user for the admin page",
    ]


@pytest.mark.skipif(shutil.which("sd") is None, reason="needs the Seeds CLI (sd)")
def test_seeds_end_to_end_with_the_real_tracker(session: deepplan.Session, project: Path) -> None:
    _complete(session)
    runner = CliRunner()
    assert deepplan.active(cli._workspace().state_root, project).id == session.id
    _run(runner, "compile")
    out = _run(runner, "seeds", code=2)
    assert "no usable Seeds tracker" in out and "--init" in out
    out = _run(runner, "seeds", "--init")
    epic = re.search(r"^EPIC  (\S+)", out, re.M).group(1)
    step = dict(re.findall(r"^SEED  (\S+)  (P\d)", out, re.M))
    assert sorted(step.values()) == ["P0", "P0", "P2"] and "PLAN  pl-" in out

    def show(issue: str) -> dict:
        raw = subprocess.run(
            ["sd", "show", issue, "--json"],
            cwd=project,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        data = json.loads(raw)
        return data.get("issue", data) if isinstance(data, dict) else data[0]

    assert show(epic)["type"] == "epic"
    first = deepplan.seed_for(deepplan.load(project, str(session.path.relative_to(project))), 1)
    assert show(first)["priority"] == 0
    # Verifying a phase closes its seed with the check as the reason.
    _run(runner, "go", "--by", "Luke", "--note", "approved")
    (project / "tests" / "test_isolation.py").write_text(
        "def test_isolated() -> None:\n    assert True\n", encoding="utf-8"
    )
    out = _run(runner, "done", "1")
    assert f"SEED_CLOSED  {first}" in out
    assert show(first)["status"] == "closed"
    assert "EPIC_CLOSED" not in out and show(epic)["status"] != "closed"
    # When every step is verified, the plan's epic closes too.
    _run(runner, "done", "2")
    out = _run(runner, "done", "3")
    assert f"EPIC_CLOSED  {epic}" in out and show(epic)["status"] == "closed"


# ── re-planning mid-run: the human approves the change, old work is re-checked ─


def test_replanning_supersedes_the_run_and_rechecks_unchanged_steps(
    session: deepplan.Session, project: Path
) -> None:
    _complete(session)
    runner = CliRunner()
    _run(runner, "compile")
    approve = ("go", "--by", "Luke", "--note", "approved")
    first_run = re.search(r"^RUN (\S+)", _run(runner, *approve), re.M).group(1)
    (project / "tests" / "test_isolation.py").write_text(
        "def test_isolated() -> None:\n    assert True\n", encoding="utf-8"
    )
    _run(runner, "done", "1")
    assert "already executing this exact plan" in _run(runner, *approve, code=2)

    # Reality changed step 2's check: re-record, and the old approval is gone.
    changed = SLICES.replace(
        "Verify: `python -m pytest -q`\n", "Verify: `python -m pytest -q tests`\n", 1
    )
    (session.path / "slices.md").write_text(changed, encoding="utf-8")
    assert "RECORDED  slices" in _run(runner, "record", "slices")
    assert "not approved yet" in _run(runner, "done", "2", code=2)
    _run(runner, "compile")
    assert "waiting for the human's approval" in _where(project)
    out = _run(runner, *approve)
    second_run = re.search(r"^RUN (\S+)", out, re.M).group(1)
    assert second_run != first_run
    assert f"SUPERSEDED  run {first_run} paused" in out
    # Step 1 did not change: it must pass again on the new run to stay ticked.
    assert "RECHECK  phase 1" in out and "CARRIED  phase 1" in out
    ledger = Ledger(cli._workspace().state_root)
    assert ledger.load(first_run).terminal_state == "paused"
    assert [e.gate for e in ledger.evidence(second_run) if e.gate == "tested"] == ["tested"]
    assert deepplan.done_phases(session) == {1}
    assert "phase 2 depends on phase 1" not in _run(runner, "done", "2")


def test_a_changed_step_is_not_carried_over(session: deepplan.Session, project: Path) -> None:
    _complete(session)
    runner = CliRunner()
    _run(runner, "compile")
    _run(runner, "go", "--by", "Luke", "--note", "approved")
    (project / "tests" / "test_isolation.py").write_text(
        "def test_isolated() -> None:\n    assert True\n", encoding="utf-8"
    )
    _run(runner, "done", "1")
    renamed = SLICES.replace("### 1. One store per user", "### 1. One table per user")
    (session.path / "slices.md").write_text(renamed, encoding="utf-8")
    _run(runner, "record", "slices")
    _run(runner, "compile")
    out = _run(runner, "go", "--by", "Luke", "--note", "approved again")
    assert "RECHECK" not in out and "CARRIED" not in out
    assert deepplan.done_phases(session) == set()
    assert "- [ ] Phase 1 (P0): One table per user" in (session.path / "progress.md").read_text()


def test_reapproval_is_allowed_when_only_the_scope_changed(
    session: deepplan.Session, project: Path
) -> None:
    _complete(session)
    runner = CliRunner()
    _run(runner, "compile")
    _run(runner, "go", "--by", "Luke", "--note", "approved")
    (project / "tests" / "test_isolation.py").write_text(
        "def test_isolated() -> None:\n    assert True\n", encoding="utf-8"
    )
    _run(runner, "done", "1")
    assert "already executing" in _run(runner, "go", "--by", "Luke", "--note", "again", code=2)
    # A tracker now lives here, so the run must be allowed to write it.
    (project / ".seeds").mkdir()
    out = _run(runner, "go", "--by", "Luke", "--note", "approved with the tracker in scope")
    assert "SUPERSEDED" in out and "CARRIED  phase 1" in out
    run_id = re.search(r"^RUN (\S+)", out, re.M).group(1)
    assert ".seeds/" in Ledger(cli._workspace().state_root).load(run_id).file_scope
