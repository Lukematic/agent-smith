"""Routing must find the right chapters without fetching, and never crash.

Every test here traces to a bug that a real command surfaced. YAML parsing 6.1 as
a float crashed routing after it had already produced the correct answer, which is
the worst kind of bug: right result, failed delivery.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smith.knowledge import BudgetExceeded, KnowledgeStore, Manifest
from smith.paths import SmithPaths


@pytest.fixture
def store() -> KnowledgeStore:
    return KnowledgeStore(SmithPaths.discover())


class TestRegistryIntegrity:
    def test_registry_parses(self, store: KnowledgeStore) -> None:
        # A registry that cannot be parsed makes every other feature dead.
        assert store.registry(), "REGISTRY.yaml failed to parse"

    def test_registry_indexes_chapters(self, store: KnowledgeStore) -> None:
        assert len(store.registry_paths()) > 50

    def test_every_indexed_path_is_a_chapter_markdown(self, store: KnowledgeStore) -> None:
        for path in store.registry_paths():
            assert path.startswith("chapters/")
            assert path.endswith(".md")

    def test_every_route_key_resolves_to_a_path(self, store: KnowledgeStore) -> None:
        # A router that points at a missing key is a router that lies.
        data = store.registry()
        unresolved: list[str] = []
        for keys in (data.get("routes") or {}).values():
            for key in keys:
                if store.path_for_key(str(key)) is None:
                    unresolved.append(str(key))
        assert not unresolved, f"routes reference unknown keys: {sorted(set(unresolved))}"


class TestRouting:
    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("what is a harness", "chapters/6-harnesses/1-what-is-a-harness.md"),
            (
                "my agent keeps making the same mistake",
                "chapters/6-harnesses/5-harness-engineering.md",
            ),
            (
                "how do I restrict what an agent can do",
                "chapters/5-tool-use/3-tool-restrictions.md",
            ),
            ("context window blowing up", "chapters/4-context/2-context-strategies.md"),
            ("debugging an agent", "chapters/8-practices/1-debugging-agents.md"),
        ],
    )
    def test_question_routes_to_expected_chapter(
        self, store: KnowledgeStore, question: str, expected: str
    ) -> None:
        keys = store.route(question)
        paths = [store.path_for_key(k) for k in keys[:3]]
        assert expected in paths, f"{question!r} routed to {paths}"

    def test_route_keys_are_strings_not_floats(self, store: KnowledgeStore) -> None:
        # YAML turns 6.1 into a float, which crashed the join in the CLI.
        keys = store.route("what is a harness")
        assert keys
        assert all(isinstance(k, str) for k in keys)
        assert ", ".join(keys)  # would raise TypeError on floats

    def test_unmatched_question_returns_empty(self, store: KnowledgeStore) -> None:
        assert store.route("zzzz nonexistent topic qqqq") == []

    def test_routing_reads_no_chapter_bodies(self, store: KnowledgeStore) -> None:
        # Routing is free. Only fetching costs budget.
        store.route("what is a harness")
        assert store.opened == 0


class TestBudget:
    def test_fourth_distinct_file_is_blocked(self, store: KnowledgeStore) -> None:
        for name in ("a.md", "b.md", "c.md"):
            store._charge(f"book:{name}")
        with pytest.raises(BudgetExceeded):
            store._charge("book:d.md")

    def test_reopening_the_same_file_is_free(self, store: KnowledgeStore) -> None:
        for _ in range(5):
            store._charge("book:same.md")
        assert store.opened == 1

    def test_budget_message_names_the_remedy(self, store: KnowledgeStore) -> None:
        for name in ("a.md", "b.md", "c.md"):
            store._charge(f"book:{name}")
        with pytest.raises(BudgetExceeded, match="under-decomposed"):
            store._charge("book:d.md")

    def test_reset_clears_the_budget(self, store: KnowledgeStore) -> None:
        store._charge("book:a.md")
        store.reset_budget()
        assert store.opened == 0


class TestManifest:
    def test_missing_manifest_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert Manifest(tmp_path / "nope.json").entries == []

    def test_corrupt_manifest_degrades_gracefully(self, tmp_path: Path) -> None:
        # A broken cache ledger must not take the whole tool down.
        bad = tmp_path / "MANIFEST.json"
        bad.write_text("{ not json", encoding="utf-8")
        assert Manifest(bad).entries == []

    def test_newest_age_is_none_when_cold(self, tmp_path: Path) -> None:
        assert Manifest(tmp_path / "m.json").newest_age_days is None


class TestPaths:
    def test_discovery_finds_the_smith_root(self) -> None:
        paths = SmithPaths.discover()
        assert (paths.root / "plugin.json").is_file()
        assert paths.registry.is_file()

    def test_scaffold_is_idempotent(self, tmp_path: Path) -> None:
        paths = SmithPaths(root=tmp_path)
        first = paths.ensure_scaffold()
        second = paths.ensure_scaffold()
        assert first
        assert second == []
