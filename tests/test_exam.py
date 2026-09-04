"""The exam is itself examined: probes have the shape they claim, the fixture is
disposable, and render reports honestly."""

from __future__ import annotations

from smith.exam import PROBES, ProbeResult, render


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
