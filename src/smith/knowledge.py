"""Knowledge fetching, caching, and drift detection.

The registry is the index and is always loadable. Chapter bodies are fetched on
demand, capped by a per-task budget, and stamped with provenance so a citation
can always be traced to a sha and a date.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from smith.paths import SmithPaths

DEFAULT_STALE_DAYS = 14
DEFAULT_BUDGET = 3

RAW_BASES: dict[str, str] = {
    "book": "https://raw.githubusercontent.com/jayminwest/agentic-engineering-book/main/",
    "overstory": "https://raw.githubusercontent.com/jayminwest/overstory/main/",
    "warren": "https://raw.githubusercontent.com/jayminwest/warren/main/",
    "seeds": "https://raw.githubusercontent.com/jayminwest/seeds/main/",
}

TREE_API = (
    "https://api.github.com/repos/jayminwest/agentic-engineering-book/git/trees/main?recursive=1"
)


class BudgetExceeded(RuntimeError):
    """Raised when a task tries to open more knowledge files than allowed.

    This is the structural form of the context-bloat guard. A prompt asking the
    model to "be mindful of context" is a wish; this is an error.
    """


class FetchError(RuntimeError):
    """A knowledge file could not be fetched, without exposing an HTTP traceback."""

    def __init__(self, source_id: str, path: str, status_code: int | None = None):
        detail = f"HTTP {status_code}" if status_code is not None else "network error"
        super().__init__(f"{source_id}:{path} ({detail})")
        self.source_id = source_id
        self.path = path
        self.status_code = status_code


@dataclass(frozen=True)
class CacheEntry:
    path: str
    source_id: str
    sha: str
    fetched_at: str
    bytes: int

    @property
    def age_days(self) -> int:
        fetched = datetime.fromisoformat(self.fetched_at)
        return (datetime.now(UTC) - fetched).days

    def is_stale(self, stale_days: int = DEFAULT_STALE_DAYS) -> bool:
        return self.age_days >= stale_days


class Manifest:
    """Provenance ledger for the knowledge cache."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[tuple[str, str], CacheEntry] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for item in raw:
            entry = CacheEntry(**item)
            self._entries[(entry.source_id, entry.path)] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(e) for e in self._entries.values()]
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get(self, source_id: str, path: str) -> CacheEntry | None:
        return self._entries.get((source_id, path))

    def put(self, entry: CacheEntry) -> None:
        self._entries[(entry.source_id, entry.path)] = entry

    def drop(self, source_id: str, path: str) -> None:
        self._entries.pop((source_id, path), None)

    @property
    def entries(self) -> list[CacheEntry]:
        return list(self._entries.values())

    @property
    def newest_age_days(self) -> int | None:
        if not self._entries:
            return None
        return min(e.age_days for e in self._entries.values())


class KnowledgeStore:
    """Fetches and caches knowledge files, enforcing the per-task budget."""

    def __init__(
        self,
        paths: SmithPaths,
        budget: int = DEFAULT_BUDGET,
        stale_days: int = DEFAULT_STALE_DAYS,
        client: httpx.Client | None = None,
    ) -> None:
        self.paths = paths
        self.budget = budget
        self.stale_days = stale_days
        self.manifest = Manifest(paths.manifest)
        self._opened: set[str] = set()
        self._client = client

    # ── budget ───────────────────────────────────────────────────────────────
    @property
    def opened(self) -> int:
        return len(self._opened)

    def reset_budget(self) -> None:
        self._opened.clear()

    def _charge(self, key: str) -> None:
        if key in self._opened:
            return
        if len(self._opened) >= self.budget:
            raise BudgetExceeded(
                f"budget {self.budget} exhausted (already opened: {sorted(self._opened)}). "
                "The task is under-decomposed. Split it."
            )
        self._opened.add(key)

    # ── fetching ─────────────────────────────────────────────────────────────
    def cache_file(self, source_id: str, path: str) -> Path:
        flat = path.replace("/", "__").replace("\\", "__")
        return self.paths.cache / f"{source_id}__{flat}"

    def fetch(
        self, path: str, source_id: str = "book", *, force: bool = False, charge: bool = True
    ) -> tuple[Path, str]:
        """Return the cache path and a status of ``CACHE_HIT``, ``FETCHED``, or ``REFRESHED``."""
        if source_id not in RAW_BASES:
            raise ValueError(f"unknown source {source_id!r}; known: {sorted(RAW_BASES)}")

        key = f"{source_id}:{path}"
        if charge:
            self._charge(key)

        target = self.cache_file(source_id, path)
        existing = self.manifest.get(source_id, path)

        if not force and existing and target.is_file() and not existing.is_stale(self.stale_days):
            return target, "CACHE_HIT"

        status = "REFRESHED" if existing else "FETCHED"
        url = RAW_BASES[source_id] + path
        client = self._client or httpx.Client(timeout=30.0, follow_redirects=True)
        try:
            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise FetchError(source_id, path, exc.response.status_code) from None
            except httpx.RequestError:
                raise FetchError(source_id, path) from None
            body = response.text
        finally:
            if self._client is None:
                client.close()

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

        self.manifest.put(
            CacheEntry(
                path=path,
                source_id=source_id,
                sha=hashlib.sha256(body.encode("utf-8")).hexdigest()[:12],
                fetched_at=datetime.now(UTC).isoformat(),
                bytes=len(body.encode("utf-8")),
            )
        )
        self.manifest.save()
        return target, status

    # ── registry ─────────────────────────────────────────────────────────────
    def registry(self) -> dict[str, Any]:
        if not self.paths.registry.is_file():
            return {}
        return yaml.safe_load(self.paths.registry.read_text(encoding="utf-8")) or {}

    def registry_paths(self) -> set[str]:
        """Every chapter path the registry indexes."""
        data = self.registry()
        found: set[str] = set()
        for chapter in (data.get("chapters") or {}).values():
            for entry in chapter.get("files") or []:
                path = entry.get("path")
                if path and path.startswith("chapters/"):
                    found.add(path)
        return found

    def route(self, question: str) -> list[str]:
        """Map a question to chapter keys using the registry's route table."""
        data = self.registry()
        routes: dict[str, list[str]] = data.get("routes") or {}
        lowered = question.lower()
        scored: list[tuple[int, list[str]]] = []
        for phrase, keys in routes.items():
            words = [w for w in phrase.lower().split() if len(w) > 3]
            if not words:
                continue
            hits = sum(1 for w in words if w in lowered)
            if hits:
                scored.append((hits, keys))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        ordered: list[str] = []
        for _, keys in scored:
            for key in keys:
                # YAML reads 6.1 as a float, so normalise every key to text.
                text = str(key)
                if text not in ordered:
                    ordered.append(text)
        return ordered

    def path_for_key(self, key: str) -> str | None:
        """Resolve a registry key to a path.

        Keys live in three places: chapter files, meta docs, and reference
        configs. Searching only chapters made valid routes look broken.
        """
        data = self.registry()
        wanted = str(key)
        for chapter in (data.get("chapters") or {}).values():
            for entry in chapter.get("files") or []:
                if str(entry.get("key")) == wanted:
                    return entry.get("path")
        for group in ("meta_docs", "reference_configs"):
            for entry in data.get(group) or []:
                if str(entry.get("key")) == wanted:
                    return entry.get("path")
        return None

    # ── drift ────────────────────────────────────────────────────────────────
    def upstream_paths(self, client: httpx.Client | None = None) -> tuple[set[str], set[str], str]:
        """Return chapter paths, appendix example dirs, and the tree sha."""
        owned = client or self._client or httpx.Client(timeout=30.0, follow_redirects=True)
        try:
            response = owned.get(TREE_API, headers={"User-Agent": "agent-smith"})
            response.raise_for_status()
            tree = response.json()
        finally:
            if client is None and self._client is None:
                owned.close()

        chapters: set[str] = set()
        appendix_dirs: set[str] = set()
        for node in tree.get("tree", []):
            if node.get("type") != "blob":
                continue
            path = node["path"]
            if path.startswith("chapters/") and path.endswith(".md"):
                chapters.add(path)
            elif path.startswith("appendices/examples/"):
                parts = path.split("/")
                if len(parts) > 3:
                    appendix_dirs.add("/".join(parts[:3]))
        return chapters, appendix_dirs, tree.get("sha", "unknown")

    def drift(self, client: httpx.Client | None = None) -> dict[str, Any]:
        upstream, appendix_dirs, sha = self.upstream_paths(client)
        known = self.registry_paths()
        return {
            "tree_sha": sha,
            "upstream_count": len(upstream),
            "registry_count": len(known),
            "added": sorted(upstream - known),
            "removed": sorted(known - upstream),
            "appendix_dirs": sorted(appendix_dirs),
        }

    def write_drift_report(self, result: dict[str, Any]) -> Path:
        lines = [
            "# Registry Drift Report",
            "",
            f"generated_at: {datetime.now(UTC).isoformat()}",
            f"tree_sha: {result['tree_sha']}",
            f"upstream_chapters: {result['upstream_count']}",
            f"registry_chapters: {result['registry_count']}",
            f"appendix_example_dirs: {len(result['appendix_dirs'])}",
            "",
            f"## ADDED upstream, not in REGISTRY.yaml ({len(result['added'])})",
        ]
        lines += [f"- {p}" for p in result["added"]] or ["- none"]
        lines += ["", f"## REMOVED upstream, still in REGISTRY.yaml ({len(result['removed'])})"]
        lines += [f"- {p}" for p in result["removed"]] or ["- none"]
        lines += [
            "",
            "## Next",
            "- For each ADDED path: fetch it, read the frontmatter, add a registry entry with tags and use_when.",
            "- For each REMOVED path: delete its entry and grep memory/ for orphaned citations.",
        ]
        self.paths.drift_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.paths.drift_report

    # ── hygiene ──────────────────────────────────────────────────────────────
    def orphaned_cache(self) -> list[Path]:
        """Cache files with no manifest entry. Disposable by definition."""
        tracked = {self.cache_file(e.source_id, e.path) for e in self.manifest.entries}
        return sorted(
            f
            for f in self.paths.cache.glob("*")
            if f.is_file() and f.name != ".gitignore" and f not in tracked
        )
