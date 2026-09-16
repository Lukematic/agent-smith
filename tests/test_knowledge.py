"""Routing must find the right chapters without fetching, and never crash.

Every test here traces to a bug that a real command surfaced. YAML parsing 6.1 as
a float crashed routing after it had already produced the correct answer, which is
the worst kind of bug: right result, failed delivery.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from awino.knowledge import (
    BudgetExceeded,
    KnowledgeReceiptRequired,
    KnowledgeStore,
    Manifest,
    knowledge_receipt,
    record_knowledge_receipt,
    require_knowledge_receipt,
)
from awino.paths import AwinoPaths


@pytest.fixture
def store() -> KnowledgeStore:
    return KnowledgeStore(AwinoPaths.discover())


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
        paths = AwinoPaths.discover()
        assert (paths.root / "plugin.json").is_file()
        assert paths.registry.is_file()

    def test_scaffold_is_idempotent(self, tmp_path: Path) -> None:
        paths = AwinoPaths(root=tmp_path)
        first = paths.ensure_scaffold()
        second = paths.ensure_scaffold()
        assert first
        assert second == []


# ── Phase 2: persistent budget accounting ───────────────────────────────
# A fresh store with the same accounting key resumes the consumed budget
# instead of resetting it to zero; without a key nothing is written.


def _tmp_paths(tmp_path: Path) -> AwinoPaths:
    return AwinoPaths(root=tmp_path)


class TestAccountingPersistence:
    def test_fresh_store_resumes_accounting(self, tmp_path: Path) -> None:
        first = KnowledgeStore(_tmp_paths(tmp_path), budget=2, accounting_key="task-1")
        first._charge("book:a.md")
        second = KnowledgeStore(_tmp_paths(tmp_path), budget=2, accounting_key="task-1")
        assert second.opened == 1
        second._charge("book:b.md")
        with pytest.raises(BudgetExceeded):
            second._charge("book:c.md")

    def test_different_keys_are_isolated(self, tmp_path: Path) -> None:
        one = KnowledgeStore(_tmp_paths(tmp_path), budget=1, accounting_key="task-1")
        one._charge("book:a.md")
        two = KnowledgeStore(_tmp_paths(tmp_path), budget=1, accounting_key="task-2")
        assert two.opened == 0
        two._charge("book:a.md")  # same file, different task: charged fresh
        assert two.opened == 1

    def test_reset_budget_drops_persisted_accounting(self, tmp_path: Path) -> None:
        store = KnowledgeStore(_tmp_paths(tmp_path), budget=1, accounting_key="task-1")
        store._charge("book:a.md")
        store.reset_budget()
        fresh = KnowledgeStore(_tmp_paths(tmp_path), budget=1, accounting_key="task-1")
        assert fresh.opened == 0

    def test_no_key_writes_nothing(self, tmp_path: Path) -> None:
        store = KnowledgeStore(_tmp_paths(tmp_path), budget=2)
        store._charge("book:a.md")
        assert not (tmp_path / "knowledge" / "accounting").exists()


# ── Phase 2: knowledge receipts ─────────────────────────────────────────
# Answers cite stored receipts; without a receipt the answer is refused.


class TestKnowledgeReceipts:
    def test_require_without_receipt_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(KnowledgeReceiptRequired):
            require_knowledge_receipt(tmp_path, "what is a harness?")

    def test_record_then_require_round_trip(self, tmp_path: Path) -> None:
        recorded = record_knowledge_receipt(
            tmp_path,
            question="what is a harness?",
            source_id="book",
            path="chapters/6-harnesses/1-what-is-a-harness.md",
            sha="abc123def456",
            by="luke",
        )
        back = require_knowledge_receipt(tmp_path, "what is a harness?")
        assert back.question_hash == recorded.question_hash
        assert back.source_id == "book"
        assert back.sha == "abc123def456"
        assert back.by == "luke"
        assert back.at

    def test_question_whitespace_normalizes_to_one_receipt(self, tmp_path: Path) -> None:
        record_knowledge_receipt(
            tmp_path,
            question="what  is\na harness?",
            source_id="book",
            path="chapters/6-harnesses/1-what-is-a-harness.md",
            sha="abc123def456",
            by="luke",
        )
        assert knowledge_receipt(tmp_path, "what is a harness?") is not None

    def test_receipts_live_in_state_not_the_repo(self, tmp_path: Path) -> None:
        record_knowledge_receipt(
            tmp_path,
            question="what is a harness?",
            source_id="book",
            path="chapters/6-harnesses/1-what-is-a-harness.md",
            sha="abc123def456",
            by="luke",
        )
        assert (tmp_path / "knowledge_receipts").is_dir()
