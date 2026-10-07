"""Routing realistic requests: the skill, the stance, and the loop.

Word overlap with skill descriptions alone routed 13 of 36 of these requests;
most came back "ambiguous" and stopped the human with a "which one?" question.
The intent phrases in awino.skill_catalog fix that. HELD_OUT was written
after the phrases were, as a check they generalize rather than memorize.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from awino import ladder, provision, stance
from awino.dispatch import decide
from awino.skill_catalog import SkillCatalog

ROOT = Path(__file__).parents[1]

ROUTES = [
    ("my tests are failing with a KeyError in parser.py", "awino-debug"),
    ("fix the bug where login fails on Safari", "awino-debug"),
    ("the build is broken after I upgraded numpy", "awino-debug"),
    ("the agent keeps ignoring my instructions and editing the wrong files", "awino-triage"),
    ("why does my agent hallucinate file paths", "awino-triage"),
    ("what is a harness and why does it matter", "awino-consult"),
    ("how should I manage context for a long running agent", "awino-consult"),
    ("which multi-agent pattern fits a code review bot", "awino-consult"),
    ("I have an idea for an app that helps nurses schedule shifts", "awino-discover"),
    ("this repo is empty, help me figure out what we are building", "awino-discover"),
    ("refactor the auth module across all services", "awino-rpi"),
    ("migrate the api from flask to fastapi", "awino-rpi"),
    ("add pagination to the orders endpoint and update the client", "awino-rpi"),
    ("build me an agent that reviews pull requests", "awino-author-agent"),
    ("create a subagent that writes release notes", "awino-author-agent"),
    ("I need a tool that checks for broken links", "awino-author-tool"),
    ("should this be a hook or a skill", "awino-author-tool"),
    ("remember that we use uv not pip", "awino-memory"),
    ("what did we decide about the database last week", "awino-memory"),
    ("update yourself", "awino-self-update"),
    ("refresh your knowledge base", "awino-self-update"),
    ("make a diagram of the system architecture", "awino-visualize"),
    ("chart the monthly sales numbers", "awino-visualize"),
    ("review my pyproject and CI config for problems", "awino-config-review"),
    ("audit the justfile and github workflows", "awino-config-review"),
    ("set up this project with a venv, tests and a task runner", "awino-bootstrap"),
    ("find evidence on whether RAG improves accuracy, with citations", "awino-evidence"),
    ("summarize the literature on protein folding models with sources", "awino-evidence"),
    ("make this data pipeline reproducible with run ids and snapshots", "awino-reproducibility"),
    ("keep iterating on the migration until every test passes", "awino-ralph"),
    ("split this across parallel agents: frontend, backend and docs", "awino-delegate"),
    ("brainstorm what we can offer this sponsor", "awino-brain"),
    ("help me write a proposal for the hospital's scheduling problem", "awino-brain"),
    (
        "I'm new to this domain, help me understand the problem and where I could help",
        "awino-brain",
    ),
    ("prepare slides for the sponsor meeting", "awino-brain"),
    ("make a slide deck for the release", "awino-visualize"),
]

HELD_OUT = [
    ("pytest says AttributeError: NoneType has no attribute id", "awino-debug"),
    ("the login page crashes when I click submit", "awino-debug"),
    ("fix the chart rendering bug in the dashboard", "awino-debug"),
    ("the CI workflow is failing on windows", "awino-debug"),
    ("claude keeps deleting my tests instead of fixing them", "awino-triage"),
    ("our coding agent is stuck in a loop rewriting the same file", "awino-triage"),
    ("what's the difference between a skill and a subagent", "awino-consult"),
    ("how do I keep the context window from filling up", "awino-consult"),
    ("I want to build something for small farms but don't know what yet", "awino-discover"),
    ("move the database layer from sqlite to postgres", "awino-rpi"),
    ("restructure the frontend into feature folders", "awino-rpi"),
    ("implement export to csv for the reports page", "awino-rpi"),
    ("design an agent that triages support tickets", "awino-author-agent"),
    ("write me a script that renames photos by date", "awino-author-tool"),
    ("remember: deploys happen on fridays only", "awino-memory"),
    ("why did we pick redis for the cache", "awino-memory"),
    ("is awino on the latest version, update the knowledge", "awino-self-update"),
    ("draw a flowchart of the checkout process", "awino-visualize"),
    ("plot latency over the last week", "awino-visualize"),
    ("sanity-check our pre-commit and dependency settings", "awino-config-review"),
    ("scaffold a new python project with uv and pytest", "awino-bootstrap"),
    ("what does the research say about code review bots, cite papers", "awino-evidence"),
    ("add provenance and snapshots to the training run", "awino-reproducibility"),
    ("retry the flaky integration fix until it works", "awino-ralph"),
    ("run the api, ui and docs changes in parallel", "awino-delegate"),
    ("a sponsor wants help with grid outages, what could we propose", "awino-brain"),
    ("draft a white paper for the funding committee", "awino-brain"),
    ("I'm new to the field of hydrology, where could I help their team", "awino-brain"),
]

STANCES = [
    ("I think we should use Postgres for everything", "steel-man"),
    ("we should rewrite it in Rust", "steel-man"),
    ("so that means we can skip the tests", "assumption-audit"),
    ("what am I missing here", "assumption-audit"),
    ("teach me how the gate ledger works", "teach-back"),
    ("I don't understand why the run refused", "teach-back"),
    ("break this down from first principles", "first-principles"),
    ("research what is known about agent memory", "research-intake"),
    ("honestly, how would you approach this", "expert"),
    ("add a retry to the http client", None),
]


@pytest.fixture(scope="module")
def catalog() -> SkillCatalog:
    return SkillCatalog(
        project_root=Path("/nonexistent-project-root"),
        global_root=Path("/nonexistent-global-root"),
        bundled_root=ROOT / "skills",
    )


@pytest.mark.parametrize(("request_text", "skill"), ROUTES + HELD_OUT)
def test_realistic_requests_route_to_the_right_skill(
    catalog: SkillCatalog, request_text: str, skill: str
) -> None:
    decision = decide(request_text, catalog)
    assert decision.confidence == "high", decision.question
    assert decision.skill is not None and decision.skill.name == skill


@pytest.mark.parametrize("request_text", ["hello", "thanks!", "ok continue"])
def test_small_talk_routes_nowhere(catalog: SkillCatalog, request_text: str) -> None:
    assert decide(request_text, catalog).confidence == "none"


@pytest.mark.parametrize(("request_text", "expected"), STANCES)
def test_stances_follow_the_humans_words(request_text: str, expected: str | None) -> None:
    detected = stance.detect(request_text)
    assert (detected.name if detected else None) == expected


def test_a_discovered_test_command_keeps_a_small_fix_to_one_attempt(tmp_path: Path) -> None:
    (tmp_path / "test_stats.py").write_text("def test_x():\n    assert True\n")
    found = provision.discover_verification(tmp_path)
    assert found == ("python -m pytest -q", "Python test files")
    choice = ladder.choose("fix the ZeroDivisionError", "awino-debug", found[0], [])
    assert choice.loop == "floor"


def test_npm_test_script_counts_as_verification(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    assert provision.discover_verification(tmp_path) == ("npm test", "package.json scripts.test")
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'
    )
    assert provision.discover_verification(tmp_path) is None
