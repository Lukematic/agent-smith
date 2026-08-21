"""The validator must block broken artifacts and pass correct ones.

A validator verified only against passing input is untested. Half of these tests
plant defects deliberately.
"""

from __future__ import annotations

from pathlib import Path

from smith.validate import (
    BROKEN_SELFTEST,
    Kind,
    Status,
    duplicated_headings,
    infer_kind,
    split_frontmatter,
    validate_text,
)

GOOD_SKILL = """---
name: example-skill
description: Does one clear thing for a specific situation
---

# Example Skill

Body text long enough to be substantive, describing a real procedure that an
agent can follow step by step without guessing at the intent behind it. This
padding exists so the body-length gate has something real to measure against.

## Failure Modes

| Mode | Definition |
| --- | --- |
| `SOMETHING_BAD` | the thing that goes wrong |

## Completion

Done when the command runs and its output is pasted.

Grounding: chapters/6-harnesses/5-harness-engineering.md
"""


def check(text: str, name: str) -> Status:
    report = validate_text(Path("skills/example-skill/SKILL.md"), text)
    return next(c.status for c in report.checks if c.name == name)


class TestGoodArtifact:
    def test_valid_skill_passes(self) -> None:
        report = validate_text(Path("skills/example-skill/SKILL.md"), GOOD_SKILL)
        assert report.ok(), list(report.failures)

    def test_kind_inferred_from_filename(self) -> None:
        assert infer_kind(Path("skills/x/SKILL.md"), "") is Kind.SKILL

    def test_kind_inferred_from_agents_dir(self) -> None:
        assert infer_kind(Path("agents/thing.md"), "") is Kind.AGENT


class TestSilentKiller:
    def test_colon_in_description_fails(self) -> None:
        # This breaks discovery with no error message at all, so it must block.
        bad = GOOD_SKILL.replace(
            "description: Does one clear thing for a specific situation",
            "description: Does one thing. Expects: SPEC",
        )
        assert check(bad, "no_colon_in_description") is Status.FAIL

    def test_clean_description_passes(self) -> None:
        assert check(GOOD_SKILL, "no_colon_in_description") is Status.PASS


class TestRequiredSections:
    def test_missing_failure_modes_fails(self) -> None:
        bad = GOOD_SKILL.replace("## Failure Modes", "## Something Else")
        assert check(bad, "section_failure_modes") is Status.FAIL

    def test_missing_completion_fails(self) -> None:
        bad = GOOD_SKILL.replace("## Completion", "## Wrap Up")
        assert check(bad, "section_completion") is Status.FAIL

    def test_missing_citation_fails(self) -> None:
        bad = GOOD_SKILL.replace("chapters/6-harnesses/5-harness-engineering.md", "somewhere")
        assert check(bad, "cites_book") is Status.FAIL


class TestNoFalsePositives:
    """A validator that flags correct work gets ignored, which is worse than none."""

    def test_no_tool_list_skips_rather_than_fails(self) -> None:
        assert check(GOOD_SKILL, "declares_tools") is Status.SKIP

    def test_orchestrator_check_skips_without_a_tool_list(self) -> None:
        assert check(GOOD_SKILL, "orchestrator_unarmed") is Status.SKIP

    def test_authoring_orchestrators_may_hold_write(self) -> None:
        # "Writes about orchestrators" is not "is an orchestrator".
        text = GOOD_SKILL.replace(
            "description: Does one clear thing for a specific situation",
            "description: Authors orchestrator definitions for review",
        ).replace("---\n\n# Example", "tools: Read, Write\n---\n\n# Example")
        assert check(text, "orchestrator_unarmed") is Status.SKIP

    def test_real_orchestrator_with_write_fails(self) -> None:
        text = GOOD_SKILL.replace(
            "description: Does one clear thing for a specific situation",
            "description: Coordinator that routes work to specialists",
        ).replace("---\n\n# Example", "tools: Read, Write, Bash\n---\n\n# Example")
        assert check(text, "orchestrator_unarmed") is Status.FAIL

    def test_two_ands_is_advisory_not_blocking(self) -> None:
        text = GOOD_SKILL.replace(
            "description: Does one clear thing for a specific situation",
            "description: Reads files and reports findings",
        )
        assert check(text, "single_responsibility") is Status.PASS

    def test_three_ands_warns_without_blocking(self) -> None:
        text = GOOD_SKILL.replace(
            "description: Does one clear thing for a specific situation",
            "description: Plans and builds and tests and ships",
        )
        report = validate_text(Path("skills/example-skill/SKILL.md"), text)
        assert any(
            c.name == "single_responsibility" and c.status is Status.WARN for c in report.checks
        )
        assert report.ok()  # warnings advise, they do not block


class TestDuplication:
    def test_repeated_heading_is_detected(self) -> None:
        assert duplicated_headings("## Setup\n## Other\n## Setup\n") == ["setup"]

    def test_unique_headings_are_clean(self) -> None:
        assert duplicated_headings("## One\n## Two\n") == []


class TestFrontmatter:
    def test_missing_frontmatter_reports_problem(self) -> None:
        fields, _, problems = split_frontmatter("# Just a heading\n")
        assert not fields
        assert problems

    def test_unterminated_frontmatter_reports_problem(self) -> None:
        _, _, problems = split_frontmatter("---\nname: x\nno closing marker\n")
        assert problems

    def test_fields_are_parsed(self) -> None:
        fields, body, problems = split_frontmatter("---\nname: x\ntools: Read\n---\nbody here\n")
        assert not problems
        assert fields == {"name": "x", "tools": "Read"}
        assert "body here" in body


class TestSelftest:
    def test_planted_artifact_is_caught_on_every_axis(self) -> None:
        report = validate_text(Path("selftest/bad-orchestrator.md"), BROKEN_SELFTEST)
        caught = {c.name for c in report.failures}
        for expected in (
            "no_colon_in_description",
            "orchestrator_unarmed",
            "section_failure_modes",
            "section_completion",
            "cites_book",
            "completion_has_evidence",
            "body_not_empty",
        ):
            assert expected in caught, f"validator missed {expected}"
        assert not report.ok()

    def test_selftest_name_matches_its_own_path(self) -> None:
        # The fixture is named to match its location on purpose: the planted
        # defects are the seven above, and a spurious eighth would make the
        # selftest assert something untrue.
        report = validate_text(Path("selftest/bad-orchestrator.md"), BROKEN_SELFTEST)
        status = next(c.status for c in report.checks if c.name == "name_matches_location")
        assert status is Status.PASS
