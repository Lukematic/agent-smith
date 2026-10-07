"""Brain mode: seven stages, two human checkpoints, a private profile, and a
report a non-technical reader can follow. The sample documents below are a
worked example (a water utility that wants to predict pipe breaks, brought by
an AI expert who does not know water) and double as the passing fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import brain, cli, ladder
from awino.dispatch import decide
from awino.skill_catalog import SkillCatalog

ROOT = Path(__file__).parents[1]

PROBLEM = """# Riverside Water: which pipes break next

## The problem in one sentence
Riverside fixes water mains after they burst, and wants to know which ones will break next so crews can replace them first.

## What we know vs what we are assuming
- Fact: 212 main breaks last year, each costing about $18,000 (sponsor email).
- Fact: they keep a spreadsheet of breaks since 2009 (brief).
- Assumption: the spreadsheet records pipe material and age for most breaks.
- Assumption: the crews would change their schedule if given a ranked list.

## The assumption to test first
That the break records name the pipe segment. Ask for ten rows and check this week.

## In scope
- A ranked list of pipe segments by risk, refreshed monthly.

## Out of scope
- Sensors in the ground, and anything that controls valves.

## Questions for the sponsor
- Who decides the replacement schedule today?
"""

PROFILE = """# Me

## Background
Ten years building prediction tools for retail and logistics.

## Domains I know well
- Demand forecasting

## Strengths
- Turning messy spreadsheets into reliable predictions
- Explaining numbers to managers

## Interests
- Public infrastructure and climate resilience
- Teaching non-technical teams to trust data

## What I want to get better at
- Scoping work before I build

## How I like to work

## Growth log
"""

YOU = """## What you bring
- Forecasting experience maps straight onto ranking pipes by break risk.

## What is new to you
- How utilities plan capital work: ask the asset manager for last year's plan.

## Blindspots
- Regulation. Why it matters: replacement budgets are approved by the state. Cover it by: reading the last rate case.
- The crews. Why it matters: a list they don't trust gets ignored. Cover it by: riding along for a day.

## Where your interests fit
- This is infrastructure resilience, which you want to grow in.
"""

OPTIONS = """## What others have done
- DC Water ranks mains by condition (Source: their 2021 capital plan).
- Many utilities use simple age-and-material rules [inferred].

## Glaring holes
- Nobody has said who acts on the list; close it by naming an owner at kickoff.

## Options
### A. Simple scoring rules
How it works: points for age, material, and past breaks.
Fits you: fast to explain.
Catch: misses soil and pressure effects.
Proof: does last year's top 50 contain this year's breaks?

### B. Learn from the break history
How it works: a model learns the pattern from 15 years of breaks.
Fits you: your core strength.
Catch: needs clean records.
Proof: beat option A on held-out years.

## Recommendation
Start with A in week one, then B if the records are clean. The crews trust A sooner.
"""

PLAN = """## The chain
### 1. Get ten rows of break records
Unlocks: knowing whether option B is possible.
Done when: the sponsor sends the rows and we confirm segment IDs exist.
Uses: messy spreadsheets.

### 2. Build the simple scoring list
Unlocks: a first list the crews can react to.
Done when: last year's top 50 caught at least 20 of this year's breaks.
Uses: explaining numbers to managers.

### 3. Ride along with a crew and review the list together
Done when: the crew lead marks which ranked pipes they agree with.
Uses: teaching non-technical teams to trust data.

## First move this week
Email the sponsor for ten rows of records.

## What we need from the sponsor
- The break spreadsheet, and one hour with the asset manager.
"""

REPORT = """# Knowing which pipes will break next

## Summary
Riverside fixes water mains after they burst. We propose a monthly ranked list of the pipes most likely to break next, so crews can replace them before they fail. It starts simple and gets smarter as we learn from your records. We need your break spreadsheet and an hour with your asset manager.

## The problem as we understand it
Last year 212 mains broke, each costing about $18,000 in repairs, overtime, and lost water. Crews respond well, but they are always responding. The goal is to move some of that work from emergency to planned.

## Where things stand today
Replacement is planned mostly by pipe age. That is a sensible start, but age alone misses pipes that break young because of soil, pressure, or material. Nobody has yet checked how well the current plan predicted last year's breaks.

## What we propose
First, a simple scoring list: points for age, material, and past breaks on the same street. Second, if your records are clean enough, a system that learns from fifteen years of breaks which combinations matter most.

## How it works, in plain words
Think of it like a doctor's checkup list. A doctor does not scan every patient for everything; they look at age, history, and a few warning signs, and call in the riskiest patients first. Our list does the same for pipes: it reads each pipe's history and puts the riskiest ones at the top. Later, the learning step is like a doctor who has seen thousands of patients and noticed which warning signs actually mattered.

## How we will know it worked
- Last year's top 50 pipes on our list include at least 20 of this year's actual breaks.
- Crew leads agree with most of the top 20 after a ride-along review.
- Emergency repairs drop over two years, measured against the 212 baseline.

## Scope
In: a monthly ranked list and a short guide for the planning team. Out: sensors in the ground and anything that controls valves.

## Risks and how we will handle them
The records may not say which pipe broke. We check ten rows in week one before promising the learning step. The crews may not trust a list from outside; we build it with them on a ride-along, not for them.

## What we need from you
- Your break spreadsheet from 2009 onward, by the end of next week.
- One hour with the asset manager to learn how the plan is made today.
- A crew lead willing to review the first list with us.
- A decision by month two on whether to fund the learning step.
"""

NOTES = """## Your 30-second version
Riverside is fixing pipes after they burst. We will give them a list each month of the pipes most likely to burst next, like a doctor's list of patients to call in first.

## Slide outline
### 1. 212 breaks last year
Say: each one is a night call, an angry street, and $18,000.
Tie-in: opens with their pain, not our method.

### 2. Today: replace by age
Say: age is a fair guess, but young pipes break too.
Tie-in: the gap the list fills.

### 3. The checkup list
Say: think of a doctor calling the riskiest patients first.
Tie-in: the analogy everyone will repeat.

### 4. What we need
Say: the spreadsheet, an hour, and a crew lead.
Tie-in: ends on the ask.

## Analogies to keep in your pocket
- The doctor's checkup list: use it when they ask how it decides.
- A weather forecast: use it when they ask why it is not always right.

## Where they will get lost
- The learning step. If you see: blank looks at "model". Try: "it notices which warning signs mattered in the past".
- Accuracy numbers. If you see: someone asking "so is it 40% right?". Try: "of the 50 pipes we flag, about 20 really break; age alone catches about 8".

## Questions they will ask
- How much will it cost? About six weeks of work for the simple list.
- Will it replace our engineers? No; it gives them a better first draft.

## Check your own understanding
- Why might a young pipe break?
- What would make us skip the learning step?
"""

GROW = """## What you learned
Utilities plan replacement years ahead, through rate cases.

## Blindspot to work on next
Regulation: read one rate case summary this month.

## Add to your profile
- Domain: water utility capital planning (beginner)
- Strength shown: turning a forecasting problem into a crew-facing list
"""

DOCS = {
    "problem": PROBLEM,
    "you": YOU,
    "options": OPTIONS,
    "plan": PLAN,
    "report": REPORT,
    "notes": NOTES,
    "grow": GROW,
}


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    return fake


@pytest.fixture()
def state_root(tmp_path: Path) -> Path:
    return tmp_path / ".awino"


def _with_profile() -> None:
    path = brain.init_profile()
    path.write_text(PROFILE, encoding="utf-8")


# ── the samples pass, so the checks are achievable ───────────────────────────


@pytest.mark.parametrize("stage", brain.STAGE_NAMES)
def test_the_worked_example_passes_every_stage(stage: str) -> None:
    assert brain.validate(stage, DOCS[stage]) == []


# ── the session: order, checkpoints, profile ────────────────────────────────


def test_full_session_with_checkpoints_and_growth(home: Path, state_root: Path) -> None:
    session = brain.start(state_root, "Riverside Water pipe breaks", "Sponsor email...")
    assert (state_root / "brain" / ".gitignore").read_text() == "*\n"
    assert brain.active(state_root).id == session.id

    assert brain.record(session, "you", YOU) == ["record 'problem' first"]
    assert brain.record(session, "problem", PROBLEM) == []
    blocked = brain.record(session, "you", YOU)
    assert "checkpoint" in blocked[0]
    assert session.next_stage().name == "problem"  # waiting for the human

    brain.confirm(session, "problem", "Yes, and add the valve question later")
    blocked = brain.record(session, "you", YOU)
    assert "profile is missing" in blocked[0]

    _with_profile()
    assert brain.record(session, "you", YOU) == []
    assert brain.record(session, "options", OPTIONS) == []
    assert "checkpoint" in brain.record(session, "plan", PLAN)[0]
    brain.confirm(session, "options", "Go with A first")
    for stage in ("plan", "report", "notes", "grow"):
        assert brain.record(session, stage, DOCS[stage]) == [], stage
    assert session.next_stage() is None
    assert (session.path / "report.md").read_text() == REPORT
    assert (session.path / "speaker-notes.md").read_text() == NOTES

    profile = brain.profile_text()
    assert "water utility capital planning" in profile
    assert profile.index("## Growth log") < profile.index("water utility")


def test_changing_a_confirmed_checkpoint_needs_a_fresh_yes(home: Path, state_root: Path) -> None:
    session = brain.start(state_root, "t")
    brain.record(session, "problem", PROBLEM)
    brain.confirm(session, "problem", "yes")
    brain.record(session, "problem", PROBLEM.replace("212", "230"))
    assert not session.confirmed("problem")


def test_confirm_needs_a_checkpoint_a_record_and_the_humans_words(
    home: Path, state_root: Path
) -> None:
    session = brain.start(state_root, "t")
    with pytest.raises(brain.BrainError, match="record"):
        brain.confirm(session, "problem", "yes")
    brain.record(session, "problem", PROBLEM)
    with pytest.raises(brain.BrainError, match="their words"):
        brain.confirm(session, "problem", "  ")
    with pytest.raises(brain.BrainError, match="not a checkpoint"):
        brain.confirm(session, "plan", "yes")


def test_sessions_get_unique_folders(home: Path, state_root: Path) -> None:
    a = brain.start(state_root, "Same title")
    b = brain.start(state_root, "Same title")
    assert a.id != b.id
    assert [s.id for s in brain.sessions(state_root)] == sorted([a.id, b.id])
    assert brain.use(state_root, a.id).id == a.id


# ── the checks catch what matters ────────────────────────────────────────────


def test_problem_must_be_one_plain_sentence() -> None:
    jargon = PROBLEM.replace(
        "Riverside fixes water mains after they burst",
        "Riverside wants an LLM pipeline to leverage break data",
    )
    problems = brain.validate("problem", jargon)
    assert any("jargon" in p and "llm" in p and "leverage" in p for p in problems)
    long = PROBLEM.replace(
        "so crews can replace them first.", "so crews can replace them. It is hard. Very hard."
    )
    assert any("sentences" in p for p in brain.validate("problem", long))


def test_problem_needs_facts_and_assumptions() -> None:
    no_facts = PROBLEM.replace("- Fact:", "- Note:")
    assert any("Fact:" in p for p in brain.validate("problem", no_facts))


def test_a_document_title_does_not_swallow_a_section() -> None:
    # The H1 contains "problem"-ish words; the real "## ..." section must win.
    text = "# The problem with in scope pipes\n\n" + PROBLEM.split("\n", 1)[1]
    assert brain.validate("problem", text) == []


def test_blindspots_need_why_and_how() -> None:
    bad = YOU.replace("Cover it by: riding along for a day.", "")
    assert any("Cover it by" in p for p in brain.validate("you", bad))


def test_precedents_need_a_source_or_an_inferred_mark() -> None:
    bad = OPTIONS.replace(" [inferred]", "")
    assert any("no link" in p for p in brain.validate("options", bad))


def test_options_need_a_catch_and_a_proof() -> None:
    bad = OPTIONS.replace("Catch: misses soil and pressure effects.\n", "")
    assert any("Catch" in p for p in brain.validate("options", bad))


def test_plan_steps_need_a_verifiable_done_and_last_step_may_skip_unlocks() -> None:
    assert brain.validate("plan", PLAN) == []  # step 3 has no Unlocks
    bad = PLAN.replace(
        "Done when: the sponsor sends the rows and we confirm segment IDs exist.\n", ""
    )
    assert any("Done when" in p for p in brain.validate("plan", bad))


def test_a_middle_step_must_say_what_it_unlocks() -> None:
    bad = PLAN.replace("Unlocks: knowing whether option B is possible.\n", "")
    assert any("Unlocks" in p and "Get ten rows" in p for p in brain.validate("plan", bad))


def test_report_defines_every_technical_term() -> None:
    bad = REPORT.replace("a system that learns", "a machine learning classifier that learns")
    problems = brain.validate("report", bad)
    assert any("glossary" in p and "classifier" in p for p in problems)
    fixed = (
        bad
        + "\n## Plain-language glossary\n- Classifier: a program that sorts things into groups.\n"
    )
    assert brain.validate("report", fixed) == []


def test_report_needs_an_analogy_and_a_real_length() -> None:
    flat = REPORT.replace("Think of it like a doctor's checkup list.", "").replace(
        "is like a doctor", "is a doctor"
    )
    assert any("analogy" in p for p in brain.validate("report", flat))
    assert any("words" in p for p in brain.validate("report", "## Summary\nShort.\n"))


def test_notes_plan_for_confusion() -> None:
    bad = NOTES.replace('Try: "it notices which warning signs mattered in the past".', "")
    assert any("Try" in p for p in brain.validate("notes", bad))
    no_say = NOTES.replace("Say: age is a fair guess, but young pipes break too.\n", "")
    assert any("Say" in p for p in brain.validate("notes", no_say))


def test_jargon_matches_words_not_fragments() -> None:
    assert brain.jargon_in("a ragged apiary of tokens") == ["token"]


# ── interest fit (the typed-scorer seam) ─────────────────────────────────────


def test_interest_fit_scores_each_step() -> None:
    likes = ["Teaching non-technical teams to trust data"]
    fit = dict(brain.interest_fit(PLAN, likes))
    assert len(fit) == 3
    ride_along = next(v for k, v in fit.items() if k.startswith("3."))
    assert ride_along > 0
    assert max(fit.values()) == ride_along


def test_a_custom_scorer_plugs_in() -> None:
    class Always:
        def score(self, work: str, interests: list[str]) -> float:
            return 0.9

    assert {s for _, s in brain.interest_fit(PLAN, ["x"], Always())} == {0.9}


# ── routing: brain words reach Brain, slides still reach visualize ───────────


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
        "help me brainstorm what we can offer this sponsor",
        "brain mode: new project space, help me find the gaps",
        "draft a whitepaper proposal for the steering committee",
        "prepare a sponsor presentation on their problem",
    ],
)
def test_brain_requests_route_to_brain(catalog: SkillCatalog, request_text: str) -> None:
    decision = decide(request_text, catalog)
    assert decision.skill is not None and decision.skill.name == "awino-brain"


def test_plain_slide_requests_still_route_to_visualize(catalog: SkillCatalog) -> None:
    decision = decide("make a slide deck for the release", catalog)
    assert decision.skill is not None and decision.skill.name == "awino-visualize"


def test_brain_runs_as_a_direct_loop() -> None:
    choice = ladder.choose("brainstorm the sponsor problem", "awino-brain", None, [])
    assert choice.loop == "direct"


# ── the CLI ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def project(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("AWINO_HOME", "SMITH_HOME", "AWINO_PROJECT", "SMITH_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return proj


def test_cli_walks_a_session(project: Path) -> None:
    runner = CliRunner()
    out = runner.invoke(cli.app, ["brain"])
    assert out.exit_code == 1 and "NO_SESSION" in out.output

    out = runner.invoke(cli.app, ["brain", "start", "Riverside Water"])
    assert out.exit_code == 0, out.output
    assert "STAGE  1/7 problem" in out.output
    session = brain.active(cli._workspace().state_root)
    assert session is not None

    (session.path / "problem.md").write_text(PROBLEM.replace("- Fact:", "- Note:"))
    out = runner.invoke(cli.app, ["brain", "record", "problem"])
    assert out.exit_code == 1 and "NOT_YET" in out.output and "Fact:" in out.output

    (session.path / "problem.md").write_text(PROBLEM)
    out = runner.invoke(cli.app, ["brain", "record", "problem"])
    assert out.exit_code == 0 and "CHECKPOINT" in out.output

    out = runner.invoke(cli.app, ["brain"])
    assert "WAITING for the human" in out.output

    out = runner.invoke(cli.app, ["brain", "confirm", "problem", "--note", "looks right"])
    assert out.exit_code == 0 and "NEXT  awino brain stage you" in out.output

    out = runner.invoke(cli.app, ["brain", "me"])
    assert out.exit_code == 1 and "MISSING" in out.output
    out = runner.invoke(cli.app, ["brain", "me", "--init"])
    assert out.exit_code == 0 and "GAPS  Background, Strengths, Interests" in out.output
