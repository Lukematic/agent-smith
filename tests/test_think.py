"""The nine thinking modes: registry, validation, teaching side, critic
integration, CLI, and the plan-approval thinking gate.

The thinking modes are the executable form of "challenge assumptions":
each mode has a prompt template (the structure of the thinking), required
output sections enforced by a structural validator, and a working-memory
destination -- thinking that doesn't land in memory didn't happen.
"""

import pytest

from awino import think

# ── compliant fixtures: one per mode ─────────────────────────────────────

FEYNMAN_OK = """# Feynman: caching

## Simple explanation
A cache keeps a copy of an expensive answer so the next identical question
is answered from the copy instead of recomputed.

## ELI5
Imagine you do a hard math problem and write the answer on a sticky note.
Next time someone asks the same problem, you read the sticky note instead
of solving it again. The sticky note is the cache. It only helps when the
same question gets asked twice.

## Diagram
```mermaid
graph LR
    Question --> Cache
    Cache -->|hit| Answer[Sticky note answer]
    Cache -->|miss| Compute[Solve it]
    Compute --> Cache
    Compute --> Answer
```

## Where it breaks
The simple version stops working when the answer changes over time: the
sticky note goes stale, and reading it gives the wrong answer. That is
where cache invalidation lives, and it is the thinnest part of this
understanding.

## Self-check
What would expose a fake understanding? Ask: when is a cache slower than
no cache? Honest answer: when nothing repeats -- every lookup misses and
you paid for storage you never use.
"""

FEYNMAN_NO_ELI5 = """# Feynman: caching

## Simple explanation
A cache keeps a copy of an expensive answer.

## Diagram
Question --> Cache --> Answer

## Where it breaks
Stale answers.

## Self-check
When is a cache slower than no cache?
"""

FEYNMAN_NO_DIAGRAM = """# Feynman: caching

## Simple explanation
A cache keeps a copy of an expensive answer so the next identical question
is answered from the copy instead of recomputed.

## ELI5
Imagine you do a hard math problem and write the answer on a sticky note.
Next time someone asks the same problem, you read the sticky note instead
of solving it again.

## Where it breaks
The simple version stops working when the answer changes over time.

## Self-check
When is a cache slower than no cache?
"""

FEYNMAN_PROSE_DIAGRAM = """# Feynman: caching

## Simple explanation
A cache keeps a copy of an expensive answer so the next identical question
is answered from the copy instead of recomputed.

## ELI5
Imagine you do a hard math problem and write the answer on a sticky note.
Next time someone asks the same problem, you read the sticky note instead
of solving it again.

## Diagram
The diagram would show the question, the cache, and the answer, and how
the answer flows back into the cache when it is computed fresh. The sticky
note holds the answer between requests.

## Where it breaks
The simple version stops working when the answer changes over time.

## Self-check
When is a cache slower than no cache?
"""

BLINDSPOT_OK = """# Blindspot: the migration plan

## Assumptions checked
- The migration can run without downtime.
- The old auth tokens remain valid during the cutover.

## Blind spots
- Token expiry during cutover: invisible because the plan assumes the
  old tokens live forever; missed because no one checked the TTL.
- Rollback needs the old code path: why this was invisible is that the
  plan never names what "revert" concretely runs.
"""

DEVIL_OK = """# Devil: strangler vs big-bang

## The opposing case
The opposing case for the strangler approach: a big-bang rewrite is
simpler to reason about, has one cutover instead of months of dual
running, and the team is small enough to coordinate it.

## What to take seriously
The dual-running cost is real: take seriously the operational burden of
two auth paths, and time-box the strangler phases. My own view -- strangler
is safer -- stands, but only with that time-box.
"""

PREMORTEM_OK = """# Premortem: the launch

## Failure reasons
1. The database migration locked the users table for nine minutes.
   Warning signs: migration dry-runs on production-sized data were never
   done; `migrate --dry-run` timing was unknown.
2. The feature flag stayed on for 10% of traffic after rollback.
   Warning signs: no flag-state dashboard; the rollback runbook never
   mentions flags.
3. Support was not told the error messages changed.
   Warning signs: the support macros still quote the old wording; no
   comms ticket exists.
"""

UNCOMFORTABLE_OK = """# Uncomfortable: the deadline

## The avoided question
Are we shipping this date because the work is ready, or because saying
otherwise would be an uncomfortable conversation with leadership?

## The answer
The date was set before the scope was known, and every estimate since
has been fitted to it rather than the other way around. The uncomfortable
truth is that the scope needs two more weeks, and the conversation has
been avoided for a month.
"""

THOUGHT_EXPERIMENT_OK = """# Thought experiment: free deploys

## Scenario
Deploys become instant and free: zero build time, zero risk, infinite
rollbacks.

## Push to the extreme
If every keystroke deployed to production instantly, code review would
have to happen after the fact, and "done" would mean "observed in prod".

## What it reveals
It reveals that most of our process is a tax on deploy cost, not on
quality: with free deploys we would still want tests, but the release
train, the freeze calendar, and the sign-off chain would evaporate.
"""

FIRST_PRINCIPLES_OK = """# First principles: the queue

## Facts
- Messages arrive at 400/sec at peak.
- One worker processes 120/sec.
- The queue is in memory only.

## Assumptions
- Peak lasts minutes, not hours (unverified).
- Workers never crash mid-batch (false on deploys).

## Rebuild from facts
400/sec in, 120/sec out per worker: at least 4 workers for peak, and the
queue must survive a worker crash -- so it cannot stay in memory only.
"""

ASSUMPTION_DESTROYER_OK = """# Assumption destroyer: the rewrite

## Assumptions
1. Users want more features. Inversion: users want fewer, better
   features -- the opposite of the roadmap. Reframing: think of the
   product as a tool that should disappear, not a platform that grows.
2. The rewrite must preserve every behavior. Inversion: the rewrite
   should deliberately drop behaviors -- the opposite of compatibility.
   Reframing: a migration, not a port; carry users, not code.
3. Performance is the top complaint. Inversion: nobody measures it --
   the opposite of "top complaint" is "unexamined assumption". Reframing:
   latency as a feature to sell, not a bug to fix.
4. The team must grow to ship faster. Inversion: a smaller team ships
   faster -- the opposite of the hiring plan. Reframing: throughput per
   person, not headcount.
5. The deadline is fixed. Inversion: the deadline is the variable and
   scope is fixed -- the opposite of the current plan. Reframing: ship
   dates as commitments to scope cuts, not to heroics.
"""

SIMPLIFY_OK = """# Simplify: the slow checkout

## Minimal variables
- Cart value: why it earns its place -- revenue is the outcome; thrown
  away: button color, page weight, font choices.
- Steps to pay: why it earns its place -- every step loses people; thrown
  away: the exact wording of each step, the progress bar style.

## ELI5
Buying should be like handing money to a shopkeeper: you say what you
want, you pay, you leave. Every extra counter you have to visit is a
chance to walk out of the shop instead.

## Diagram
Cart value --> Steps to pay --> Walkouts
Steps to pay --> Cart value

## Solution using only these
Using only these variables: cut steps to pay to one, and show the cart
value early so it anchors the decision. Nothing else changes.
"""

SIMPLIFY_NO_DIAGRAM = """# Simplify: the slow checkout

## Minimal variables
- Cart value: the outcome.
- Steps to pay: every step loses people.

## ELI5
Buying should be like handing money to a shopkeeper.

## Solution using only these
Using only these variables: cut steps to pay to one.
"""


# ── registry ─────────────────────────────────────────────────────────────

class TestModeRegistry:
    def test_nine_modes(self) -> None:
        assert think.MODE_NAMES == (
            "feynman",
            "blindspot",
            "devil",
            "premortem",
            "uncomfortable",
            "thought-experiment",
            "first-principles",
            "assumption-destroyer",
            "simplify",
        )

    def test_stance_mappings(self) -> None:
        """Modes map onto stances where they overlap; the rest are new."""
        assert think.by_name("feynman").stance == "teach-back"
        assert think.by_name("blindspot").stance == "assumption-audit"
        assert think.by_name("devil").stance == "steel-man"
        assert think.by_name("first-principles").stance == "first-principles"
        for new in (
            "premortem",
            "uncomfortable",
            "thought-experiment",
            "assumption-destroyer",
            "simplify",
        ):
            assert think.by_name(new).stance is None, new

    def test_memory_destinations(self) -> None:
        """Decisions hold judgments; facts hold learnings."""
        for name in (
            "blindspot",
            "devil",
            "premortem",
            "uncomfortable",
            "thought-experiment",
        ):
            assert think.by_name(name).memory == "decisions", name
        for name in ("feynman", "first-principles", "assumption-destroyer", "simplify"):
            assert think.by_name(name).memory == "facts", name

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown thinking mode"):
            think.by_name("nope")
        with pytest.raises(ValueError, match="unknown thinking mode"):
            think.validate("nope", "text")


# ── validation: compliant outputs pass ───────────────────────────────────

class TestCompliantOutputsPass:
    @pytest.mark.parametrize(
        "mode_name, text",
        [
            ("feynman", FEYNMAN_OK),
            ("blindspot", BLINDSPOT_OK),
            ("devil", DEVIL_OK),
            ("premortem", PREMORTEM_OK),
            ("uncomfortable", UNCOMFORTABLE_OK),
            ("thought-experiment", THOUGHT_EXPERIMENT_OK),
            ("first-principles", FIRST_PRINCIPLES_OK),
            ("assumption-destroyer", ASSUMPTION_DESTROYER_OK),
            ("simplify", SIMPLIFY_OK),
        ],
    )
    def test_compliant_output_passes(self, mode_name: str, text: str) -> None:
        assert think.validate(mode_name, text) == []


# ── validation: failures name the missing part ───────────────────────────

class TestFailuresNameTheMissingPart:
    def test_missing_section_named(self) -> None:
        missing = think.validate("premortem", "# Premortem\n\nNo sections.\n")
        assert any("failure reasons" in item for item in missing)

    def test_premortem_counts_reasons_and_warning_signs(self) -> None:
        missing = think.validate(
            "premortem", "# P\n\n## Failure reasons\n1. It broke.\n2. It broke again.\n"
        )
        assert any("at least 3 required" in item for item in missing)
        assert any("no warning signs" in item for item in missing)

    def test_assumption_destroyer_counts_and_names_parts(self) -> None:
        missing = think.validate(
            "assumption-destroyer", "# A\n\n## Assumptions\n1. Users want speed.\n"
        )
        assert any("at least 5 required" in item for item in missing)
        assert any("no inversion" in item for item in missing)
        assert any("no reframing" in item for item in missing)

    def test_blindspot_names_invisibility(self) -> None:
        missing = think.validate(
            "blindspot",
            "# B\n\n## Assumptions checked\n- x\n\n## Blind spots\n- We forgot the TTL.\n",
        )
        assert any("why it was invisible" in item for item in missing)

    def test_devil_requires_opposing_case_first(self) -> None:
        missing = think.validate(
            "devil",
            "# D\n\n## What to take seriously\nMy view: strangler wins, no contest.\n\n"
            "## The opposing case\nThe opposing case: big-bang is simpler.\n",
        )
        assert any("opposing case must come before" in item for item in missing)

    def test_simplify_requires_only_these_solution(self) -> None:
        text = SIMPLIFY_OK.replace("Using only these variables:", "Solution:")
        text = text.replace("Nothing else changes.", "That is the whole fix.")
        missing = think.validate("simplify", text)
        assert any("uses only the named variables" in item for item in missing)

    def test_uncomfortable_requires_a_question_and_an_answer(self) -> None:
        missing = think.validate(
            "uncomfortable",
            "# U\n\n## The avoided question\nThe deadline.\n\n## The answer\nToo short.\n",
        )
        assert any("asks no question" in item for item in missing)
        assert any("too short to be an answer" in item for item in missing)


# ── the teaching side: ELI5 + diagram ─────────────────────────────────────
# Both feynman and simplify must carry an ELI5 section (a smart
# twelve-year-old follows it) and a diagram section that actually draws the
# variables and their relationships -- boxes and arrows, not prose.

class TestTeachingSide:
    def test_feynman_teaching_sections_pass(self) -> None:
        assert think.validate("feynman", FEYNMAN_OK) == []

    def test_simplify_teaching_sections_pass(self) -> None:
        assert think.validate("simplify", SIMPLIFY_OK) == []

    def test_feynman_missing_eli5_names_it(self) -> None:
        missing = think.validate("feynman", FEYNMAN_NO_ELI5)
        assert any("eli5" in item for item in missing)

    def test_simplify_missing_eli5_names_it(self) -> None:
        text = SIMPLIFY_OK.replace("## ELI5\n", "## Plain\n")
        missing = think.validate("simplify", text)
        assert any("eli5" in item for item in missing)

    def test_feynman_missing_diagram_names_it(self) -> None:
        missing = think.validate("feynman", FEYNMAN_NO_DIAGRAM)
        assert any("diagram" in item for item in missing)

    def test_simplify_missing_diagram_names_it(self) -> None:
        missing = think.validate("simplify", SIMPLIFY_NO_DIAGRAM)
        assert any("diagram" in item for item in missing)

    def test_prose_is_not_a_diagram(self) -> None:
        """A diagram section that merely describes the variables -- no
        boxes, no arrows, no mermaid -- fails with the diagram named."""
        missing = think.validate("feynman", FEYNMAN_PROSE_DIAGRAM)
        assert any("draws no diagram" in item for item in missing)

    def test_mermaid_counts_as_a_diagram(self) -> None:
        assert think.validate("feynman", FEYNMAN_OK) == []

    def test_ascii_arrows_count_as_a_diagram(self) -> None:
        text = FEYNMAN_OK.replace(
            "```mermaid\ngraph LR\n    Question --> Cache",
            "Question --> Cache",
        ).replace("```\n\n## Where", "\n\n## Where")
        assert think.validate("feynman", text) == []


# ── critic integration ───────────────────────────────────────────────────

class TestCriticIntegration:
    def test_critic_verifies_thinking_modes(self) -> None:
        from awino import stance_verify

        assert stance_verify.verify("premortem", PREMORTEM_OK) == []
        assert stance_verify.verify("simplify", SIMPLIFY_OK) == []

    def test_critic_names_thinking_mode_failures(self) -> None:
        from awino import stance_verify

        missing = stance_verify.verify("simplify", SIMPLIFY_NO_DIAGRAM)
        assert any("diagram" in item for item in missing)

    def test_critic_rejects_unknown_names(self) -> None:
        from awino import stance_verify

        with pytest.raises(ValueError):
            stance_verify.verify("nope", "text")


# ── CLI ──────────────────────────────────────────────────────────────────

class TestThinkCli:
    def test_think_lists_all_nine_modes(self) -> None:
        from typer.testing import CliRunner

        from awino.cli import app

        result = CliRunner().invoke(app, ["think"])
        assert result.exit_code == 0, result.output
        assert "THINK_MODES" in result.output
        for name in think.MODE_NAMES:
            assert name in result.output

    def test_think_mode_prints_template_and_stance_note(self) -> None:
        from typer.testing import CliRunner

        from awino.cli import app

        result = CliRunner().invoke(app, ["think", "devil"])
        assert result.exit_code == 0, result.output
        assert "the opposing case" in result.output.lower()
        assert "steel-man" in result.output

    def test_think_unknown_mode_refused(self) -> None:
        from typer.testing import CliRunner

        from awino.cli import app

        result = CliRunner().invoke(app, ["think", "nope"])
        assert result.exit_code == 2
        assert "unknown thinking mode" in result.output
