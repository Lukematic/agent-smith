"""The Deep Plan stages: what each asks for, in the words the model reads.

Kept apart from the engine in ``awino.deepplan`` so the prompts can be read and
tuned on their own. The grill is not a document stage: it is a question log the
engine keeps, one question at a time (see ``awino.deepplan.ask``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    name: str
    title: str
    purpose: str
    prompt: str
    checkpoint: bool = False

    @property
    def filename(self) -> str:
        return f"{self.name}.md"


GRILL_GUIDE = """\
Grill the human until you share one picture of the work. The rules:

Grilling is a challenge, not a survey: you are both trying to find what is
wrong with the plan before the code does.

1. Read before you ask. If the code, the docs, or git history can answer a
   question, answer it yourself and record it:
     awino deepplan learn "<what is true>" --evidence path/to/file.py:42
2. One question at a time, always with your recommended answer and why it
   matters. Offer two or three concrete options when that helps:
     awino deepplan ask "<question>" --recommend "<your answer>" --why "<what it changes>"
   Then stop and wait. Record exactly what they said:
     awino deepplan answer "<their words>"      (or --accept if they take your recommendation)
3. Ask the question that unblocks the most other decisions first, then walk
   down the tree. Do not ask anything whose answer would not change the plan.
4. Cover what bites later: who it is for and why now; what is in v1 and what
   waits; data and state; edge cases and failure modes; security and privacy;
   performance; compatibility and migration of what exists; how we will prove
   it works; how it rolls out and rolls back.
5. Challenge at least once, and mark it so the record shows it:
     awino deepplan ask "<pushback>" --challenge --recommend "<your answer>" --why "<risk>"
   Push back when an answer is vague ("fast", "better": what number, what would
   we see?), when a solution is posing as the problem (what is wrong today that
   this fixes?), when scope creeps (what can wait for P1?), and on the riskiest
   assumption (how would we know it is wrong, and how soon?).
6. Never answer your own question. If the human wants to move on, record it:
     awino deepplan defer --reason "<why it can wait>"
7. Finish when no blocking question is open:
     awino deepplan grill-done
"""

STAGES: tuple[Stage, ...] = (
    Stage(
        "frame",
        "Define the problem",
        "what is wrong, how we will know it is fixed, its parts, and what we need",
        (
            "Read the brief and skim the code it touches before writing. Challenge the\n"
            "ask: a request often arrives as a solution; find the problem under it.\n"
            "\n"
            "## Problem\n"
            "What is wrong today, for whom, and what it costs. Describe the problem, not\n"
            "the fix. Include `Evidence:` (a number, a file:line, an error, the user's\n"
            "own words), or mark it `[inferred]` and plan to check it in the grill.\n"
            "\n"
            "## Goal\n"
            "One sentence, in plain words: the change that makes the problem go away.\n"
            "\n"
            "## Done when\n"
            "How we will know. Observable criteria, one bullet each, numbered `C1:`,\n"
            "`C2:`... A criterion is something a person or a command can check, never\n"
            "a feeling.\n"
            "\n"
            "## Break it down\n"
            "The parts of the problem, one bullet each (at least two). For each: what\n"
            "has to be true for this part to be solved?\n"
            "\n"
            "## What's needed\n"
            "People, access, decisions, data, tools, money: anything we need before or\n"
            "during the work, and who has it. Write `- nothing beyond the repo` only\n"
            "after checking.\n"
            "\n"
            "## Constraints\n"
            "Time, tech, people, things that must not break.\n"
            "\n"
            "## Out of scope\n"
            "At least one bullet. Saying no early is what keeps the plan small."
        ),
    ),
    Stage(
        "grill",
        "Get grilled",
        "one question at a time, each with a recommendation, until nothing blocking is open",
        GRILL_GUIDE,
    ),
    Stage(
        "directions",
        "Brainstorm, then choose",
        "go wide with variations, narrow to two or three real directions, recommend one",
        (
            "## Variations\n"
            "At least five, one bullet each, each tagged with the lens that produced\n"
            "it (`Lens:` the opposite, remove a constraint, the 10x simpler version,\n"
            "the 10x scale version, another audience, combine with something nearby,\n"
            "what an expert would find obvious). Ground them in the code you read.\n"
            "\n"
            "## Directions\n"
            "Two or three genuinely different directions, one `###` heading each, with:\n"
            "`Value:` who benefits and how much. `Hardest part:` the thing most likely\n"
            "to go wrong. `Assumption:` what we are betting is true but have not checked.\n"
            "\n"
            "## Recommendation\n"
            "The direction you would pick, and what would change your mind.\n"
            "\n"
            "Then STOP. Ask the human which direction to take."
        ),
        checkpoint=True,
    ),
    Stage(
        "recon",
        "Read the code that matters",
        "where it lives, what to reuse, how we verify today, where it is risky",
        (
            "Read before you design. Every claim here points at the code.\n"
            "\n"
            "## Code that matters\n"
            "At least three `path/to/file:line` references that exist, each with what it\n"
            "does and why it matters for this change.\n"
            "\n"
            "## Reuse\n"
            "Existing functions, helpers, patterns to build on instead of writing new ones.\n"
            "\n"
            "## How we verify\n"
            "The real command that runs the tests today, in backticks (`python -m pytest -q`,\n"
            "`npm test`, `just test`). It must run on this machine.\n"
            "\n"
            "## Risks\n"
            "Fragile areas, shared state, things with no tests, places users depend on."
        ),
    ),
    Stage(
        "slices",
        "Steps, by priority",
        "what needs to get done: small steps, prioritized, each proven by a real command",
        (
            "## Slices\n"
            "Two to twelve steps, one `###` heading each, numbered `### 1. <title>`. A step\n"
            "is a thin vertical piece that works end to end and can be shown working,\n"
            "about half an hour of focused work. For each:\n"
            "`Priority:` P0 (done needs it), P1 (should, right after), P2 (could, later).\n"
            "  List them by priority: every P0 first, then P1, then P2. Every done\n"
            "  criterion needs a P0 step: done must never wait on a nice-to-have.\n"
            "`Files:` exact paths it touches (mark new ones `(new)`; five at most, or split).\n"
            "`Depends on:` the numbers of earlier steps it truly needs (`1, 2`), or `none`.\n"
            "`Verify:` the command that proves it, in backticks, runnable here.\n"
            "`Done when:` what that command or a person sees.\n"
            "`Covers:` the done criteria it serves (`C1, C3`). A step that covers none is\n"
            "not justified.\n"
            "\n"
            "## Critical path\n"
            "The longest chain of dependent slices.\n"
            "\n"
            "## Will change\n"
            "## Will not change\n"
            "The scope boundary, as files or areas. Anything outside it found later goes\n"
            "to a follow-up list, never into this work."
        ),
    ),
    Stage(
        "redteam",
        "Attack the plan",
        "how it fails, the strongest objections, and the way back",
        (
            "Switch sides. If you can, hand this stage to a separate reviewer (a\n"
            "subagent with no stake in the plan); the author grades its own plan too kindly.\n"
            "\n"
            "## Premortem\n"
            "It is a month from now and this failed. At least three reasons, one `###`\n"
            "each, with `Warning sign:` (what we would see first) and `Mitigation:`\n"
            "(what the plan does about it now).\n"
            "\n"
            "## Objections\n"
            "What a sharp reviewer would push back on. At least two, each with `Answer:`\n"
            "(why the plan stands, or what it changes).\n"
            "\n"
            "## Rollback\n"
            "How we undo each risky slice if it goes wrong."
        ),
    ),
)

STAGE_NAMES = tuple(s.name for s in STAGES)


def by_name(name: str) -> Stage:
    for stage in STAGES:
        if stage.name == name:
            return stage
    raise ValueError(f"unknown stage {name!r}: one of {', '.join(STAGE_NAMES)}")
