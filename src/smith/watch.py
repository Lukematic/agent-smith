"""Multi-source knowledge watching: detect upstream changes, never auto-merge.

``awino update`` (in cli/maintain.py / knowledge.py) checks drift against exactly one
hardcoded repository — the book. That is honestly labelled version tracking, not
research. This module extends the same *mechanism* (poll a git tree, diff
against what is known) to every source declared in ``knowledge/SOURCES.yaml``,
including sources with no chapter/registry structure at all (skills repos, tool
repos, arbitrary reference repos the user points A.W.I.N.O. at).

What this still is: change **detection**, not autonomous learning. Every finding
becomes a *proposed* review item (a seed, if seeds is available; a report line
otherwise). Nothing is fetched into the registry, no skill is authored, and no
lesson is written without a human looking at the diff first. That boundary is
deliberate — see docs/loop-engineering-honesty.md for why crossing it silently
would be a false claim about what this system does.

Grounded in the same harness-engineering principle as everywhere else in A.W.I.N.O.:
a recurring manual chore (checking N repos by hand) gets a structural mechanism
(one command that checks all N and tells you exactly what changed), and the
judgement call (is this worth integrating) stays with a human.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from smith.paths import SmithPaths

WATCHLIST_FILE = "watchlist.yaml"
SNAPSHOT_FILE = "watch_snapshot.json"


@dataclass(frozen=True)
class WatchedRepo:
    """One arbitrary GitHub repo A.W.I.N.O. should poll for changes.

    Distinct from ``SOURCES.yaml`` entries: sources are curated, registry-backed
    knowledge origins with a role and a license. A watchlist entry is just "check
    this repo for new commits/files and tell me" — the on-ramp before something
    earns a place in SOURCES.yaml.
    """

    id: str
    owner: str
    repo: str
    ref: str = "main"
    note: str = ""
    added_by: str = "user"
    added_at: str = ""

    @property
    def tree_api(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{self.ref}?recursive=1"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


def watchlist_path(paths: SmithPaths) -> Path:
    return paths.knowledge / WATCHLIST_FILE


def snapshot_path(paths: SmithPaths) -> Path:
    return paths.knowledge / SNAPSHOT_FILE


def load_watchlist(paths: SmithPaths) -> list[WatchedRepo]:
    path = watchlist_path(paths)
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [WatchedRepo(**entry) for entry in data.get("repos", [])]


def save_watchlist(paths: SmithPaths, repos: list[WatchedRepo]) -> Path:
    path = watchlist_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "// note": "Repos A.W.I.N.O. should poll for changes. Add with 'awino watch add'.",
        "repos": [{k: v for k, v in vars(r).items() if v} for r in repos],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def add_watched_repo(
    paths: SmithPaths, owner: str, repo: str, ref: str = "main", note: str = ""
) -> WatchedRepo:
    """Add a repo to the watchlist. This is the 'check out this repo' entry point.

    Adding to the watchlist does not fetch anything and does not touch the
    registry. It only means the repo will be included in future `awino watch`
    scans.
    """
    repos = load_watchlist(paths)
    repo_id = f"{owner}/{repo}"
    if any(r.id == repo_id for r in repos):
        raise ValueError(f"{repo_id} is already on the watchlist")
    entry = WatchedRepo(
        id=repo_id,
        owner=owner,
        repo=repo,
        ref=ref,
        note=note,
        added_at=datetime.now(UTC).isoformat(),
    )
    repos.append(entry)
    save_watchlist(paths, repos)
    return entry


def remove_watched_repo(paths: SmithPaths, repo_id: str) -> bool:
    repos = load_watchlist(paths)
    remaining = [r for r in repos if r.id != repo_id]
    if len(remaining) == len(repos):
        return False
    save_watchlist(paths, remaining)
    return True


# ── snapshot: what tree sha we last saw for each source ──────────────────────


def _load_snapshot(paths: SmithPaths) -> dict[str, str]:
    path = snapshot_path(paths)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_snapshot(paths: SmithPaths, snapshot: dict[str, str]) -> None:
    path = snapshot_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")


@dataclass
class ChangeFinding:
    """One repo's change status since the last snapshot."""

    source_id: str
    label: str
    html_url: str
    previous_sha: str | None
    current_sha: str
    changed: bool
    changed_paths: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def summary(self) -> str:
        if self.error:
            return f"{self.label}: could not check ({self.error})"
        if not self.changed:
            return f"{self.label}: no change since last check"
        if self.previous_sha is None:
            return f"{self.label}: first check, baseline recorded"
        return f"{self.label}: changed ({len(self.changed_paths)} path(s) touched or new)"


def _fetch_tree(client: httpx.Client, tree_api: str) -> tuple[str, list[str]]:
    response = client.get(tree_api, headers={"User-Agent": "agent-smith"})
    response.raise_for_status()
    data = response.json()
    paths = [node["path"] for node in data.get("tree", []) if node.get("type") == "blob"]
    return data.get("sha", "unknown"), paths


def check_source_for_changes(
    source_id: str,
    label: str,
    tree_api: str,
    html_url: str,
    previous_sha: str | None,
    client: httpx.Client | None = None,
) -> ChangeFinding:
    """Poll one repo's tree. A changed sha is reported; content is never fetched.

    Fetching bodies happens later, deliberately, one file at a time, through the
    existing budgeted KnowledgeStore.fetch() — this function only answers "did
    anything change", which is cheap and safe to run on every source every time.
    """
    owned = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        sha, paths = _fetch_tree(owned, tree_api)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return ChangeFinding(
            source_id, label, html_url, previous_sha, previous_sha or "", False, error=str(exc)
        )
    finally:
        if client is None:
            owned.close()

    changed = previous_sha is not None and sha != previous_sha
    is_first_check = previous_sha is None
    return ChangeFinding(
        source_id=source_id,
        label=label,
        html_url=html_url,
        previous_sha=previous_sha,
        current_sha=sha,
        changed=changed or is_first_check,
        changed_paths=paths if (changed or is_first_check) else [],
    )


def scan_all(paths: SmithPaths, client: httpx.Client | None = None) -> list[ChangeFinding]:
    """Check every configured source and every watchlisted repo for changes.

    This is the single entry point both `awino watch` (local, on demand) and the
    scheduled GitHub Actions workflow (remote, on a cron) call. Same code path,
    so "the user ran it" and "the schedule ran it" produce identical, comparable
    output.
    """
    findings: list[ChangeFinding] = []
    snapshot = _load_snapshot(paths)
    owned = client or httpx.Client(timeout=30.0, follow_redirects=True)

    try:
        sources_data = (
            yaml.safe_load(paths.sources.read_text(encoding="utf-8"))
            if paths.sources.is_file()
            else {}
        )
        for source in (sources_data or {}).get("sources", []):
            if "tree_api" not in source:
                continue  # some sources (e.g. seeds) have no tree to diff
            source_id = source["id"]
            finding = check_source_for_changes(
                source_id,
                source.get("name", source_id),
                source["tree_api"],
                f"https://github.com/{source.get('owner', '')}/{source.get('repo', '')}",
                snapshot.get(source_id),
                owned,
            )
            findings.append(finding)
            snapshot[source_id] = finding.current_sha

        for repo in load_watchlist(paths):
            finding = check_source_for_changes(
                repo.id, repo.id, repo.tree_api, repo.html_url, snapshot.get(repo.id), owned
            )
            findings.append(finding)
            snapshot[repo.id] = finding.current_sha
    finally:
        if client is None:
            owned.close()

    _save_snapshot(paths, snapshot)
    return findings


def as_report(findings: list[ChangeFinding]) -> str:
    changed = [f for f in findings if f.changed and not f.error]
    errored = [f for f in findings if f.error]
    lines = [
        "# Watch Report",
        "",
        f"generated_at: {datetime.now(UTC).isoformat()}",
        f"sources_checked: {len(findings)}",
        f"changed: {len(changed)}",
        f"errors: {len(errored)}",
        "",
    ]
    for finding in findings:
        lines.append(f"- {finding.summary}  ({finding.html_url})")
    lines.append("")
    lines.append(
        "Nothing was fetched or added automatically. Review each changed source with "
        "`awino fetch <path> --source <id>` or open the repo directly, then curate any "
        "addition into knowledge/REGISTRY.yaml by hand — see awino-self-update."
    )
    return "\n".join(lines)


def as_json(findings: list[ChangeFinding]) -> str:
    return json.dumps(
        [
            {
                "source_id": f.source_id,
                "label": f.label,
                "html_url": f.html_url,
                "changed": f.changed,
                "changed_path_count": len(f.changed_paths),
                "error": f.error,
            }
            for f in findings
        ],
        indent=2,
    )


def seed_titles_for_changes(findings: list[ChangeFinding]) -> list[dict[str, Any]]:
    """Shape change findings as seed-creation payloads, for the CLI to hand to sd.

    A finding becomes a *proposal* for a tracked review task — never an
    automatic knowledge update. The human (or a human-approved workflow) still
    decides whether anything gets curated in.
    """
    return [
        {
            "title": f"Review upstream changes in {f.label}",
            "description": (
                f"{f.html_url} changed since last check ({len(f.changed_paths)} paths in tree). "
                "Fetch and review manually; do not bulk-import. Curate into "
                "knowledge/REGISTRY.yaml only what is actually useful, with tags and use_when."
            ),
            "labels": ["knowledge-watch", "verify"],
        }
        for f in findings
        if f.changed and not f.error
    ]
