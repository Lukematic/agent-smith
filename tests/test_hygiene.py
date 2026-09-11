"""Tests for buddy's state-hygiene check ("clean folders").

Fixtures are synthetic state built in tmp dirs: a stale session, an
orphaned loop state, a partial (unparseable) loop state, unreferenced
clutter, ledger-referenced clutter, and duplicate session-end markers.
The report must flag each; `buddy --fix` must archive the stale session
(never delete it) with a ledger note, tidy the unambiguous clutter, leave
the referenced clutter and the loop states for a human, and afterwards the
ledger must still resolve every artifact it references. No test touches the
real workspace.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awino import cli, hygiene, session_state, skill_catalog
from awino.cli import buddy
from awino.enforce import Ledger, LoopEvent

try:
    import coverage
except ImportError:  # pragma: no cover
    coverage = None

STALE_DAYS = 40


@pytest.fixture()
def cli_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.delenv("AWINO_HOME", raising=False)
    monkeypatch.delenv("SMITH_HOME", raising=False)
    monkeypatch.delenv("AWINO_PROJECT", raising=False)
    monkeypatch.delenv("SMITH_PROJECT", raising=False)
    return CliRunner()


@pytest.fixture()
def messy_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """State dir with one of each hygiene problem."""
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / ".awino"

    # Sessions: one stale, one active.
    session_state.start(state_root, "new-session")
    old_files = [
        state_root / "session" / "old-session.json",
        state_root / "session" / "old-session.jsonl",
    ]
    for path in old_files:
        path.write_text("{}\n", encoding="utf-8")
    ancient = time.time() - STALE_DAYS * 86400
    for path in old_files:
        os.utime(path, (ancient, ancient))

    # Loops: one orphaned (no events), one partial (does not parse).
    loops_dir = state_root / "loops"
    loops_dir.mkdir(parents=True, exist_ok=True)
    (loops_dir / "rpi-20260911-deadbeef.json").write_text(
        json.dumps(
            {"id": "rpi-20260911-deadbeef", "task": "t", "topic": "t", "phase": "implement"}
        ),
        encoding="utf-8",
    )
    (loops_dir / "rpi-20260911-broken.json").write_text(
        '{"id": "rpi-20260911-broken"}', encoding="utf-8"
    )

    # Clutter: one unreferenced, one the ledger references.
    (state_root / "scratch.tmp").write_text("junk\n", encoding="utf-8")
    (state_root / "keepme.tmp").write_text("load-bearing\n", encoding="utf-8")
    run_dir = state_root / "run" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "evidence.jsonl").write_text(
        '{"note": "keepme.tmp is load-bearing"}\n', encoding="utf-8"
    )

    # Duplicate session-end markers.
    (state_root / "session_ends.jsonl").write_text(
        '{"at": "2026-09-10T00:00:00+00:00"}\n'
        '{"at": "2026-09-10T00:00:00+00:00"}\n'
        '{"at": "2026-09-11T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    # A ledger-referenced artifact: the hygiene pass must never delete it.
    ledger = Ledger(state_root)
    ledger.record_loop_event(
        LoopEvent(
            loop_id="rpi-20260911-good",
            loop_kind="rpi",
            phase="research",
            kind="artifact_validated",
            at=datetime.now(UTC).isoformat(),
            detail="research artifact passed validation: docs/report.md",
        )
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "report.md").write_text("# report\n", encoding="utf-8")
    return state_root


def _findings(state_root: Path) -> list[buddy.HygieneFinding]:
    return buddy._hygiene_findings(state_root, Ledger(state_root))


# ── the report flags each problem ────────────────────────────────────────


def test_hygiene_flags_stale_session(messy_state: Path) -> None:
    stale = [f for f in _findings(messy_state) if f.kind == "stale_session"]
    assert [f.target for f in stale] == ["old-session"]
    assert "new-session" not in [f.target for f in stale]  # active: not stale


def test_hygiene_flags_orphaned_and_partial_loops(messy_state: Path) -> None:
    kinds = {f.target: f.kind for f in _findings(messy_state)}
    assert kinds["rpi-20260911-deadbeef"] == "orphaned_loop"
    assert kinds["rpi-20260911-broken"] == "partial_loop"


def test_hygiene_flags_clutter_and_duplicate_markers(messy_state: Path) -> None:
    findings = _findings(messy_state)
    clutter = {f.target: f.detail for f in findings if f.kind == "clutter"}
    assert clutter["scratch.tmp"] == "unreferenced temp/leftover file"
    assert "referenced by ledger" in clutter["keepme.tmp"]
    assert any(f.kind == "duplicate_marker" for f in findings)


def test_buddy_report_has_hygiene_section(messy_state: Path, cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    assert "STATE HYGIENE" in result.output
    for marker in (
        "STALE_SESSION",
        "ORPHANED_LOOP",
        "PARTIAL_LOOP",
        "CLUTTER",
        "DUPLICATE_MARKER",
    ):
        assert marker in result.output, f"missing flag: {marker}"


def test_hygiene_clean_state_reports_clean(
    tmp_path: Path, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, [])
    assert result.exit_code == 0, result.output
    assert "state dir is clean" in result.output


# ── --fix: archive, tidy, prompt ──────────────────────────────────────────


def test_fix_archives_stale_session_with_ledger_note(
    messy_state: Path, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert "FIX archived stale session old-session" in result.output
    archived = messy_state / "archive" / "sessions"
    assert (archived / "old-session.json").is_file()
    assert (archived / "old-session.jsonl").is_file()
    # Not deleted: the files survive under archive/.
    assert not (messy_state / "session" / "old-session.json").exists()
    # The ledger note records what happened and where it went.
    notes = [event for event in Ledger(messy_state).loop_events() if event.kind == "state_archived"]
    assert len(notes) == 1
    assert "old-session" in notes[0].detail
    assert "archive/sessions/" in notes[0].detail
    # The active session is untouched, pointer intact.
    assert (messy_state / "session" / ".active").read_text(encoding="utf-8") == "new-session"


def test_fix_tidies_unambiguous_clutter_but_not_referenced(
    messy_state: Path, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert not (messy_state / "scratch.tmp").exists()
    assert (messy_state / "keepme.tmp").is_file()  # ledger-referenced: kept
    assert "PROMPT" in result.output and "keepme.tmp" in result.output


def test_fix_dedupes_markers_and_prompts_on_loop_state(
    messy_state: Path, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    lines = (messy_state / "session_ends.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # the duplicate is gone, both unique markers kept
    # Orphaned/partial loop state is judgmental: flagged, never moved.
    assert (messy_state / "loops" / "rpi-20260911-deadbeef.json").is_file()
    assert (messy_state / "loops" / "rpi-20260911-broken.json").is_file()
    assert "PROMPT" in result.output and "orphaned_loop" in result.output
    assert "PROMPT" in result.output and "partial_loop" in result.output


def test_fix_second_run_is_quiet(messy_state: Path, cli_runner: CliRunner) -> None:
    first = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert first.exit_code == 0, first.output
    second = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert second.exit_code == 0, second.output
    assert "FIX archived stale session" not in second.output
    # The ledger note is recorded once, not once per run.
    notes = [event for event in Ledger(messy_state).loop_events() if event.kind == "state_archived"]
    assert len(notes) == 1


def test_ledger_still_resolves_every_referenced_artifact_after_fix(
    messy_state: Path, cli_runner: CliRunner, tmp_path: Path
) -> None:
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    ledger = Ledger(messy_state)
    # Every artifact path the trail names still exists on disk.
    for event in ledger.loop_events():
        if event.kind != "artifact_validated":
            continue
        artifact = event.detail.split("passed validation:")[-1].strip()
        assert (tmp_path / artifact).is_file(), f"missing artifact: {artifact}"
    # The trail itself still parses, including the hygiene note.
    assert ledger.loop_events()
    # Run evidence the clutter check read is untouched.
    assert (messy_state / "run" / "r1" / "evidence.jsonl").is_file()


# ── repo hygiene ("one clean"): dead code, docs coverage, docs drift ──────


def test_dead_code_from_ruff_f401(tmp_path: Path) -> None:
    module = tmp_path / "m.py"
    diagnostics = [
        {
            "code": "F401",
            "filename": str(module),
            "location": {"row": 3},
            "message": "`os` imported but unused",
        },
        {
            "code": "E501",
            "filename": str(module),
            "location": {"row": 9},
            "message": "line too long",
        },
    ]
    findings = hygiene.dead_code_from_ruff(diagnostics, tmp_path)
    assert len(findings) == 1
    assert findings[0].target == "m.py:3"
    assert "unused" in findings[0].detail


def test_doc_mentions_finds_commands_in_docs(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "usage.md").write_text(
        "Run `awino ask` to check a question.\n"
        "Then try `awino loop run ralph` for iteration.\n"
        "Undocumented here: doctor.\n",
        encoding="utf-8",
    )
    mentions = hygiene.doc_mentions(docs)
    assert "awino ask" in mentions
    assert "awino loop run ralph" in mentions
    assert "doctor" not in mentions


def test_undocumented_commands_flagged(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "usage.md").write_text("Use `awino ask` to ask questions.\n")
    mentions = hygiene.doc_mentions(docs)
    missing = hygiene.undocumented_commands(["ask", "doctor", "loop run"], mentions)
    assert missing == ["doctor", "loop run"]


def test_dead_doc_refs_flagged() -> None:
    dead = hygiene.dead_doc_refs({"awino ask", "awino time machine"}, ["ask", "doctor"])
    assert dead == ["awino time machine"]
    assert hygiene.dead_doc_refs({"awino ask"}, ["ask", "doctor"]) == []


def test_dead_doc_refs_accepts_real_command_groups() -> None:
    """A mention naming a group (`awino buddy`, `awino gate plan ...`) is alive."""
    commands = ["buddy check", "buddy health", "gate plan approve"]
    mentions = {"awino buddy", "awino buddy --fix", "awino gate plan ..."}
    assert hygiene.dead_doc_refs(mentions, commands) == []


def test_write_commands_reference_marks_draft(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    path = hygiene.write_commands_reference(docs, [("ask", "Check a question"), ("ghost", "")])
    assert path == docs / "commands.md"
    text = path.read_text(encoding="utf-8")
    assert "Do not edit by hand" in text
    assert "regenerated" in text
    assert "`awino ask`" in text and "Check a question" in text
    assert "`awino ghost`" in text and "[DRAFT]" in text


def test_reference_drift_detects_stale_body(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    entries = [("ask", "Check a question")]
    hygiene.write_commands_reference(docs, entries)
    assert hygiene.reference_drift(entries, docs) == []
    drift = hygiene.reference_drift([("ask", "TOTALLY DIFFERENT HELP")], docs)
    assert len(drift) == 1
    assert drift[0].target == "awino ask"
    assert "buddy --fix" in drift[0].detail
    # No reference yet is coverage's finding, not drift's.
    missing_dir = tmp_path / "empty-docs"
    missing_dir.mkdir()
    assert hygiene.reference_drift(entries, missing_dir) == []


@pytest.mark.skipif(coverage is None, reason="coverage not installed")
def test_coverage_deep_flags_unexecuted_function(tmp_path: Path) -> None:
    """Real coverage run: only `live` is executed, `dead` must be flagged."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pkgmod.py").write_text(
        "def live():\n    return 1\n\n\ndef dead():\n    return 2\n",
        encoding="utf-8",
    )
    (proj / "use_live.py").write_text("import pkgmod\nprint(pkgmod.live())\n")
    env = {**os.environ, "COVERAGE_FILE": str(proj / ".coverage")}
    run = subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--source", "pkgmod", "use_live.py"],
        cwd=proj,
        capture_output=True,
        text=True,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    report = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "cov.json"],
        cwd=proj,
        capture_output=True,
        text=True,
        env=env,
    )
    assert report.returncode == 0, report.stderr
    data = json.loads((proj / "cov.json").read_text(encoding="utf-8"))
    findings = hygiene.dead_from_coverage(data, proj)
    targets = [finding.target for finding in findings]
    assert "pkgmod.py:5" in targets
    assert "function 'dead' never executed" in " ".join(f.detail for f in findings)
    assert not any(target == "pkgmod.py:1" for target in targets)


def test_run_coverage_deep_honest_when_coverage_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hygiene, "coverage_available", lambda: False)
    findings, note = hygiene.run_coverage_deep(tmp_path)
    assert findings is None
    assert "coverage" in note


def test_buddy_health_fast_tier_reports_all_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`buddy health` (fast tier) exits 0 and shows the three hygiene lenses."""
    for var in ("AWINO_HOME", "SMITH_HOME", "AWINO_PROJECT", "SMITH_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    result = CliRunner().invoke(cli.app, ["buddy", "health"])
    assert result.exit_code == 0, result.output
    assert "REPO HYGIENE" in result.output
    # Dead code: either the fast-tier header (clean) or per-finding lines.
    assert "DEAD_CODE" in result.output or "DEAD CODE (fast)" in result.output
    # Docs coverage and drift: headers or per-finding lines.
    assert "DOCS_COVERAGE" in result.output or "DOCS_DRIFT" in result.output


def test_every_shipped_skill_has_purpose_and_when_to_use() -> None:
    """The registry parses purpose/when-to-use for all real skills."""
    from awino.cli import _skill_catalog as _real_catalog

    catalog = _real_catalog()
    assert len(catalog.skills) >= 16
    undocumented = [
        skill.name for skill in catalog.skills if not skill_catalog.describe(skill).documented
    ]
    assert undocumented == [], f"skills missing purpose/when-to-use: {undocumented}"
    for skill in catalog.skills:
        doc = skill_catalog.describe(skill)
        assert doc.purpose, skill.name
        assert doc.when_to_use, skill.name


def test_fixture_skill_discovered_dynamically(tmp_path: Path) -> None:
    """A new SKILL.md directory appears in the registry with no hardcoding."""
    skills_dir = tmp_path / "skills"
    (skills_dir / "demo").mkdir(parents=True)
    (skills_dir / "demo" / "SKILL.md").write_text(
        "---\ndescription: Demo skill for registry tests.\n---\n"
        "# demo\n\nUse this skill when testing the registry discovery.\n",
        encoding="utf-8",
    )
    catalog = skill_catalog.SkillCatalog(tmp_path, tmp_path, skills_dir)
    (item,) = catalog.skills
    doc = skill_catalog.describe(item)
    assert doc.name == "demo"
    assert doc.documented
    assert "testing the registry" in doc.when_to_use.lower()


def test_skill_without_doc_sections_is_flagged(tmp_path: Path) -> None:
    """A SKILL.md with no frontmatter description is honestly undocumented."""
    skills_dir = tmp_path / "skills"
    (skills_dir / "vague").mkdir(parents=True)
    (skills_dir / "vague" / "SKILL.md").write_text(
        "# vague\n\nSome rambling text without structure.\n", encoding="utf-8"
    )
    catalog = skill_catalog.SkillCatalog(tmp_path, tmp_path, skills_dir)
    (item,) = catalog.skills
    doc = skill_catalog.describe(item)
    assert not doc.documented


def test_awino_skills_shows_when_to_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """`awino skills` lists every skill with its one-line when-to-use."""
    for var in ("AWINO_HOME", "SMITH_HOME", "AWINO_PROJECT", "SMITH_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    result = CliRunner().invoke(cli.app, ["skills"])
    assert result.exit_code == 0, result.output
    assert "when-to-use" in result.output
    assert "awino-debug" in result.output
    assert "awino-ralph" in result.output
    assert "(no purpose/when-to-use documented in SKILL.md)" not in result.output


def test_fix_regenerates_drifted_command_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    """--fix corrects one-sided drift: stale reference, live --help wins."""
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    hygiene.write_commands_reference(docs, [("ask", "STALE HELP TEXT")])
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert "FIX regenerated" in result.output
    text = (docs / "commands.md").read_text(encoding="utf-8")
    assert "`awino ask`" in text
    assert "STALE HELP TEXT" not in text
    # Second run is quiet: the reference is now in sync.
    again = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert again.exit_code == 0, again.output
    assert "FIX regenerated" not in again.output
    assert "command reference is current" in again.output


def test_fix_leaves_docless_project_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    """No docs/ directory: --fix notes it, creates nothing, counts nothing."""
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(buddy.buddy_app, ["--fix"])
    assert result.exit_code == 0, result.output
    assert "no docs/ directory" in result.output
    assert not (tmp_path / "docs").exists()
