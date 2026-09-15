"""The exam is itself examined: probes have the shape they claim, the fixture is
disposable, and render reports honestly."""

from __future__ import annotations

from pathlib import Path

from awino.exam import PROBES, ProbeResult, render


def test_every_probe_names_a_real_cli_entry_and_an_expectation() -> None:
    for probe in PROBES:
        assert probe.argv and probe.expect
        assert probe.name and "." in probe.name


def test_probe_names_are_unique() -> None:
    names = [p.name for p in PROBES]
    assert len(names) == len(set(names))


def test_render_counts_fired_honestly() -> None:
    results = [ProbeResult("a.b", True, "x"), ProbeResult("c.d", False, "y")]
    lines = render(results)
    assert lines[0].startswith("FIRES")
    assert lines[1].startswith("SILENT")
    assert lines[-1] == "EXAM  1/2 capabilities fire"


def test_the_exam_covers_the_capabilities_the_human_asked_about() -> None:
    names = " ".join(p.name for p in PROBES)
    for must in (
        "heilmeier",
        "stance",
        "elevator",
        "recall",
        "verify",
        "floor",
        "hook",
        "auto",
        "graph",
    ):
        assert must in names, must


# ── Adversarial exam tests ──────────────────────────────────────────────────
# A failing subprocess can never pass through expected text alone, and a probe
# only counts when its command is a real executable that ran clean.

from awino.exam import Probe, _run, launcher_resolves, probe_fired  # noqa: E402


def test_probe_fires_only_on_clean_exit_with_evidence() -> None:
    probe = Probe("x.y", ("best",), "CARRYING")
    assert probe_fired(probe, 0, "something CARRYING on") is True


def test_failing_subprocess_cannot_pass_on_expected_text() -> None:
    probe = Probe("x.y", ("best",), "CARRYING")
    # The expected text is right there in the output — but the command crashed.
    assert probe_fired(probe, 1, "something CARRYING on") is False
    assert probe_fired(probe, 2, "CARRYING") is False
    assert probe_fired(probe, -9, "CARRYING") is False


def test_clean_exit_without_evidence_is_not_a_pass() -> None:
    probe = Probe("x.y", ("best",), "CARRYING")
    assert probe_fired(probe, 0, "nothing relevant here") is False
    assert probe_fired(probe, 0, "") is False


def test_launcher_resolves_for_the_real_interpreter() -> None:
    assert launcher_resolves() is True


def test_launcher_rejects_a_missing_interpreter() -> None:
    assert launcher_resolves("/nonexistent/python-xyz-123") is False


def test_run_captures_nonzero_return_codes(tmp_path: Path) -> None:
    # `gate open` with no args is a usage error: the CLI really runs and
    # really fails. _run must report that instead of swallowing it.
    code, _ = _run(("gate", "open"), tmp_path, "")
    assert code != 0
