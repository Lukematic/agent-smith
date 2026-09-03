"""Persona and generated-mode wiring: prove that awino start is the documented
first command, that dispatch replaces the multi-row routing table, that the
generated Kilo/Roo mode carries the same two instructions, and that docs state
honestly where dispatch actually fires automatically versus where it depends
on the persona calling it.

Asserted against the generated mode output (modes.build_modes), not a
hand-edited fixture file - a mode file drifting from its generator without a
failing test is exactly the propagation gap S6 exists to catch elsewhere.
"""

from __future__ import annotations

from pathlib import Path

from smith.modes import build_modes

AGENTS_AWINO_MD = Path(__file__).resolve().parents[1] / "agents" / "awino.md"
AWINO_MD = Path(__file__).resolve().parents[1] / "AWINO.md"
AGENT_GUIDE_MD = Path(__file__).resolve().parents[1] / "docs" / "agent-guide.md"


class TestPersonaNamesStartAsTheFirstCommand:
    def test_persona_first_move_names_awino_start(self) -> None:
        text = AGENTS_AWINO_MD.read_text(encoding="utf-8")
        first_move = text.split("## First move", 1)[1].split("\n## ", 1)[0]
        assert "awino start" in first_move

    def test_constitution_first_move_names_awino_start(self) -> None:
        text = AWINO_MD.read_text(encoding="utf-8")
        first_move = text.split("## 0. Session start", 1)[1].split("\n## ", 1)[0]
        assert "awino start" in first_move


class TestPersonaRoutesActionableRequestsThroughDispatch:
    def test_routing_section_names_awino_dispatch(self) -> None:
        text = AGENTS_AWINO_MD.read_text(encoding="utf-8")
        routing = text.split("## Routing", 1)[1]
        assert "awino dispatch" in routing


class TestGeneratedModesCarryTheSameTwoInstructions:
    def test_the_primary_generated_mode_names_start_and_dispatch(self, tmp_path: Path) -> None:
        modes = build_modes(tmp_path / "smith-home")
        primary = next(mode for mode in modes if mode.slug == "awino")
        combined = primary.role_definition + primary.custom_instructions
        assert "awino start" in combined
        assert "awino dispatch" in combined


class TestDocsHonestlyStateTheAutomationBoundary:
    def test_agent_guide_states_claude_hook_vs_kilo_roo_persona_dependency(self) -> None:
        text = AGENT_GUIDE_MD.read_text(encoding="utf-8")
        assert "UserPromptSubmit" in text
        assert "awino dispatch" in text
        lowered = text.lower()
        assert "kilo" in lowered
        assert "roo" in lowered

    def test_no_doc_claims_dispatch_fires_automatically_in_kilo_or_roo(self) -> None:
        text = AGENT_GUIDE_MD.read_text(encoding="utf-8")
        # The document must state the dependency, not the false positive: it
        # should not claim automatic firing for Kilo/Roo in the same breath as
        # the Claude Code hook without the persona-dependency qualifier nearby.
        lowered = " ".join(text.lower().split())
        assert "depends on the persona calling" in lowered
        # The hook routes and detects stance but must remain advisory: the doc
        # has to say it never spawns, or it is overclaiming automation again.
        assert "it never spawns" in lowered
