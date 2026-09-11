"""Tests for mission alignment: the driver flags (never blocks) when an
artifact ignores the project's stated mission.

Mission sources: `.awino/MISSION.md` (headings and list items) or
`.awino/project.yaml` (`goals:` list). The keyword heuristic is documented:
lowercase alphanumeric words, minimum five characters, an explicit stopword
set, case-insensitive substring matching.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from awino import loops
from awino.enforce import Ledger

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "awino-rpi" / "SKILL.md"

RESEARCH_OK = """# Research: auth migration

## Metadata
- date 2026-09-11, branch challenge/tested-fixes, commit abc123
- scope: src/auth.py examined in full

## Where it lives
| Concern | File | Lines |
|---|---|---|
| auth | src/auth.py | 1-200 |

## How it works
The authenticator validates tokens; see src/auth.py:42.

## Flow
login -> validate -> session.

## Existing conventions to imitate
Use the existing session store.

## Open questions
None.
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    return project


@pytest.fixture()
def driver(project: Path, tmp_path: Path) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
    )


@pytest.fixture()
def event_driver(
    project: Path, tmp_path: Path, loop_ledger: Ledger
) -> loops.RpiDriver:
    return loops.RpiDriver(
        project_root=project,
        loops_dir=tmp_path / "loops",
        skill_md=SKILL_MD,
        ledger=loop_ledger,
    )


@pytest.fixture()
def loop_ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / ".awino")


def _write_research(driver: loops.RpiDriver, state: loops.LoopState, text: str) -> None:
    path = driver.project_root / state.research_artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_mission_md(project: Path, text: str) -> None:
    state_dir = project / ".awino"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "MISSION.md").write_text(text, encoding="utf-8")


class TestMissionSources:
    def test_no_mission_file_means_no_check(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        state = event_driver.new("migrate auth")
        _write_research(event_driver, state, RESEARCH_OK)
        assert event_driver.check(state) == []
        assert event_driver.last_drift is None
        kinds = [e.kind for e in loop_ledger.loop_events(state.id)]
        assert "mission_drift_flagged" not in kinds

    def test_mission_md_headings_are_goals(self, project: Path) -> None:
        _write_mission_md(
            project,
            "# Mission\n\n## Authenticate users securely\n\n## Ship quickly\n",
        )
        goals = loops._mission_goal_texts(project)
        assert "Authenticate users securely" in goals
        assert "Ship quickly" in goals

    def test_mission_md_list_items_are_goals(self, project: Path) -> None:
        _write_mission_md(
            project,
            "# Mission\n\n- Authenticate users securely\n- Ship quickly\n",
        )
        goals = loops._mission_goal_texts(project)
        assert "Authenticate users securely" in goals

    def test_project_yaml_goals_are_goals(self, project: Path) -> None:
        state_dir = project / ".awino"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "project.yaml").write_text(
            "goals:\n  - Authenticate users securely\n  - title: Ship quickly\n",
            encoding="utf-8",
        )
        goals = loops._mission_goal_texts(project)
        assert "Authenticate users securely" in goals
        assert "Ship quickly" in goals

    def test_mission_md_takes_precedence_over_yaml(self, project: Path) -> None:
        _write_mission_md(project, "# Mission\n\n## From markdown\n")
        state_dir = project / ".awino"
        (state_dir / "project.yaml").write_text(
            "goals:\n  - From yaml\n", encoding="utf-8"
        )
        goals = loops._mission_goal_texts(project)
        assert goals == ["From markdown"]


class TestKeywordHeuristic:
    def test_keywords_are_long_lowercase_alphanumeric(self) -> None:
        keywords = loops._mission_keywords(["Authenticate users securely!"])
        assert "authenticate" in keywords
        assert "securely" in keywords
        assert "users" in keywords  # 5 chars meets the minimum
        assert all(len(k) >= 5 for k in keywords)
        assert all(k == k.lower() for k in keywords)
        # Short tokens are dropped.
        assert loops._mission_keywords(["a an the it"]) == set()

    def test_stopwords_are_filtered(self) -> None:
        keywords = loops._mission_keywords(["the quick brown fox jumps"])
        # 'quick', 'brown', 'jumps' are 5+ chars; stopwords like 'the' are out.
        assert "the" not in keywords
        assert all(k not in loops.MISSION_STOPWORDS for k in keywords)

    def test_matching_is_case_insensitive_substring(self) -> None:
        assert loops._goal_hit(
            "Authenticate users securely", "we must AUTHENTICATE everyone".lower()
        )
        # Substring: 'securely' appears inside 'insecurely'? No -- but the
        # heuristic is a plain substring check.
        assert loops._goal_hit("Ship quickly", "we ship quickly now".lower())


class TestDriftFlaggedNotBlocking:
    def test_drift_flagged_when_artifact_ignores_mission(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        _write_mission_md(
            event_driver.project_root,
            "# Mission\n\n## Authenticate users securely\n",
        )
        state = event_driver.new("migrate auth")
        # RESEARCH_OK mentions auth.py but not 'authenticate' or 'securely'.
        _write_research(event_driver, state, RESEARCH_OK)
        assert event_driver.check(state) == []  # validation still passes
        assert event_driver.last_drift == ["Authenticate users securely"]
        events = loop_ledger.loop_events(state.id)
        flagged = [e for e in events if e.kind == "mission_drift_flagged"]
        assert len(flagged) == 1
        assert "Authenticate users securely" in flagged[0].detail

    def test_no_drift_when_artifact_mentions_mission(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        _write_mission_md(
            event_driver.project_root,
            "# Mission\n\n## Authenticate users securely\n",
        )
        state = event_driver.new("migrate auth")
        _write_research(
            event_driver, state,
            RESEARCH_OK + "\nThis authenticates users securely per the mission.\n",
        )
        assert event_driver.check(state) == []
        assert event_driver.last_drift is None
        kinds = [e.kind for e in loop_ledger.loop_events(state.id)]
        assert "mission_drift_flagged" not in kinds

    def test_drift_does_not_block_advance(
        self, event_driver: loops.RpiDriver
    ) -> None:
        _write_mission_md(
            event_driver.project_root,
            "# Mission\n\n## Authenticate users securely\n",
        )
        state = event_driver.new("migrate auth")
        _write_research(event_driver, state, RESEARCH_OK)
        assert event_driver.check(state) == []
        # Drift is flagged, but the loop advances anyway.
        assert event_driver.advance(state) == "plan"

    def test_drift_only_emitted_once_per_validation(
        self, event_driver: loops.RpiDriver, loop_ledger: Ledger
    ) -> None:
        _write_mission_md(
            event_driver.project_root,
            "# Mission\n\n## Authenticate users securely\n",
        )
        state = event_driver.new("migrate auth")
        _write_research(event_driver, state, RESEARCH_OK)
        assert event_driver.check(state) == []
        assert event_driver.check(state) == []  # re-check: already validated
        flagged = [
            e for e in loop_ledger.loop_events(state.id)
            if e.kind == "mission_drift_flagged"
        ]
        assert len(flagged) == 1
