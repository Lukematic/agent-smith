"""The seven Brain stages: what each asks for, in the words the model reads.

Kept apart from the engine in ``awino.brain`` so the prompts can be read and
tuned on their own. Each prompt is the structure the thinking goes into; the
matching check in ``awino.brain`` proves the required parts exist.
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
    output: str = ""  # file name in the session folder; defaults to <name>.md

    @property
    def filename(self) -> str:
        return self.output or f"{self.name}.md"


STAGES: tuple[Stage, ...] = (
    Stage(
        "problem",
        "Get the problem right",
        "first principles: what is actually being asked, what we know, what is in and out",
        (
            "Read the brief (everything the human gave you) before writing a word.\n"
            "Talk like a person, not a report generator.\n"
            "\n"
            "## The problem in one sentence\n"
            "What is wrong or wanted, and for whom. If a smart friend outside the\n"
            "field would stumble on a word, swap it for a plain one.\n"
            "\n"
            "## What we know vs what we are assuming\n"
            "One bullet each, starting `Fact:` or `Assumption:`. A fact says where\n"
            "it came from (the brief, a document, the sponsor).\n"
            "\n"
            "## The assumption to test first\n"
            "The one that sinks everything if it is wrong, and the cheapest way to\n"
            "check it.\n"
            "\n"
            "## In scope\n"
            "## Out of scope\n"
            "At least one bullet each. Out of scope is what keeps a proposal honest.\n"
            "\n"
            "## Questions for the sponsor\n"
            "The few things only they can answer.\n"
            "\n"
            "Then STOP. Ask the human: did I get the problem and the scope right?"
        ),
        checkpoint=True,
    ),
    Stage(
        "you",
        "Map what you bring",
        "your strengths, what is new to you, your blindspots, where your interests fit",
        (
            "Read the profile (`awino brain me`) and the problem. This stage is about\n"
            "the human, so be specific to them, and kind but straight.\n"
            "\n"
            "## What you bring\n"
            "Strengths from the profile that apply here, each tied to a specific\n"
            "part of the problem.\n"
            "\n"
            "## What is new to you\n"
            "The parts of their world the human does not know yet, and the fastest\n"
            "way in: a person to ask, a document to read, a site visit.\n"
            "\n"
            "## Blindspots\n"
            "At least two things the human is likely to miss. For each:\n"
            "`Why it matters:` and `Cover it by:`. Look at the domain (rules,\n"
            "workflows, regulation), the people (who uses it, who pays, who can say\n"
            "no), the data (does it exist, is it clean, are we allowed), and habits\n"
            "(an AI expert's reflex is to reach for a model before fixing the process).\n"
            "\n"
            "## Where your interests fit\n"
            "The part of this the human would most enjoy and grow from."
        ),
    ),
    Stage(
        "options",
        "Look around, then choose",
        "what others have done, glaring holes, two or three real options, one recommendation",
        (
            "Be a realist: name the holes, say how to close them, and keep moving.\n"
            "\n"
            "## What others have done\n"
            "At least two real precedents (companies, papers, tools, earlier\n"
            "attempts inside the sponsor): what worked and what did not. Give a\n"
            "link or `Source:`; anything from memory is marked `[inferred]`.\n"
            "\n"
            "## Glaring holes\n"
            "Gaps in the ask or in our understanding that would sink a proposal,\n"
            "and how to close each one.\n"
            "\n"
            "## Options\n"
            "Two or three genuinely different approaches, one `###` heading each, with:\n"
            "`How it works:` one plain line. `Fits you:` which strengths it uses.\n"
            "`Catch:` the cost or the risk. `Proof:` the small result in about two\n"
            "weeks that would show it works.\n"
            "\n"
            "## Recommendation\n"
            "The one you would pick and why, in two or three sentences.\n"
            "\n"
            "Then STOP. Ask the human which option to go with."
        ),
        checkpoint=True,
    ),
    Stage(
        "plan",
        "Break it into a chain",
        "if we do A we can do B: three to six steps, each with a check someone else can verify",
        (
            "## The chain\n"
            "Three to six steps, in order, one `###` heading each. Each step says\n"
            "what we do, then:\n"
            "`Unlocks:` what it makes possible next (the last step may skip this).\n"
            "`Done when:` a check someone else could verify: a demo, a number, a\n"
            "signed-off document. Never 'feels done'.\n"
            "`Uses:` which of the human's strengths or interests it draws on.\n"
            "\n"
            "## First move this week\n"
            "One small, concrete action.\n"
            "\n"
            "## What we need from the sponsor\n"
            "Data, people, access, decisions, dates."
        ),
    ),
    Stage(
        "report",
        "Write the report",
        "two to four pages a non-technical decision maker can read and say yes to",
        (
            "Write for a smart reader who does not work in AI. Conversational, like\n"
            "explaining it over coffee. Tell it as a story: where they are, what is\n"
            "in the way, what changes. Every technical idea gets an everyday analogy.\n"
            "About 400 to 2,500 words.\n"
            "\n"
            "## Summary\n"
            "Three or four sentences: their problem, what we propose, what it gets\n"
            "them, what we need from them.\n"
            "\n"
            "## The problem as we understand it\n"
            "## Where things stand today\n"
            "The gaps, plainly.\n"
            "## What we propose\n"
            "## How it works, in plain words\n"
            "At least one analogy ('think of it like...').\n"
            "## How we will know it worked\n"
            "Specific checks, with numbers where you can.\n"
            "## Scope\n"
            "What is in, what is out.\n"
            "## Risks and how we will handle them\n"
            "## What we need from you\n"
            "The clear ask: decisions, data, people, dates.\n"
            "## Plain-language glossary\n"
            "Needed only if you used a technical term. One line each, in everyday words."
        ),
        output="report.md",
    ),
    Stage(
        "notes",
        "Prepare yourself",
        "your private prep: the 30-second version, slides with what to say, where they will get lost",
        (
            "Private prep for the human. Never sent to the sponsor. Warm and practical.\n"
            "\n"
            "## Your 30-second version\n"
            "How you would say the whole thing out loud.\n"
            "\n"
            "## Slide outline\n"
            "Four to ten slides, one `###` heading each, with `Say:` (what you say,\n"
            "in your own voice) and `Tie-in:` (how it connects to the slide before,\n"
            "or to their world).\n"
            "\n"
            "## Analogies to keep in your pocket\n"
            "At least two, each with when to use it.\n"
            "\n"
            "## Where they will get lost\n"
            "At least two spots, each with `If you see:` (the signal: a frown, a\n"
            "silence, a question) and `Try:` (another way in). Confusion is a signal\n"
            "to change the explanation, never the listener's fault.\n"
            "\n"
            "## Questions they will ask\n"
            "Short honest answers, including 'I don't know yet; here is how we will\n"
            "find out.'\n"
            "\n"
            "## Check your own understanding\n"
            "Questions you should answer without notes. Any you cannot answer is the\n"
            "part to study before the meeting."
        ),
        output="speaker-notes.md",
    ),
    Stage(
        "grow",
        "Grow",
        "what this taught you, one blindspot to work on, what to add to your profile",
        (
            "## What you learned\n"
            "## Blindspot to work on next\n"
            "One, with a small way to practise it.\n"
            "## Add to your profile\n"
            "Bullets to append to your profile: domain knowledge you now have,\n"
            "strengths you showed, interests you found."
        ),
    ),
)

STAGE_NAMES = tuple(s.name for s in STAGES)


def by_name(name: str) -> Stage:
    for stage in STAGES:
        if stage.name == name:
            return stage
    raise ValueError(f"unknown stage {name!r}: one of {', '.join(STAGE_NAMES)}")
