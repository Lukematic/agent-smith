"""Real regression: CI's own test step generates test-results-<os>-py<ver>.xml
at the repo root (see .github/workflows/ci.yml), and 'doctor' must not treat
its own pipeline's expected artifact as clutter that fails the build.
"""

from __future__ import annotations

from pathlib import Path

from smith.paths import SmithPaths
from smith.tidy import Finding, Tidier


def _minimal_home(tmp_path: Path) -> SmithPaths:
    (tmp_path / "README.md").write_text("# test\n", encoding="utf-8")
    return SmithPaths(root=tmp_path)


class TestGeneratedTestReportIsNotStrayClutter:
    def test_ci_matching_junit_xml_is_not_flagged_as_a_stray_root_file(
        self, tmp_path: Path
    ) -> None:
        home = _minimal_home(tmp_path)
        (tmp_path / "test-results-ubuntu-latest-py3.12.xml").write_text(
            "<testsuite/>", encoding="utf-8"
        )
        findings = Tidier(home).scan()
        stray = [f for f in findings if f.kind is Finding.STRAY_ROOT_FILE]
        assert stray == []

    def test_a_different_matrix_cells_report_name_is_also_not_flagged(self, tmp_path: Path) -> None:
        home = _minimal_home(tmp_path)
        (tmp_path / "test-results-windows-latest-py3.13.xml").write_text(
            "<testsuite/>", encoding="utf-8"
        )
        findings = Tidier(home).scan()
        stray = [f for f in findings if f.kind is Finding.STRAY_ROOT_FILE]
        assert stray == []

    def test_an_unrelated_xml_file_is_still_correctly_flagged(self, tmp_path: Path) -> None:
        # The allowance must be specific to the generated-report naming
        # pattern, not "any .xml file at root" - otherwise real stray
        # clutter with an .xml extension would silently go undetected.
        home = _minimal_home(tmp_path)
        (tmp_path / "notes.xml").write_text("<note/>", encoding="utf-8")
        findings = Tidier(home).scan()
        stray = [f for f in findings if f.kind is Finding.STRAY_ROOT_FILE]
        assert len(stray) == 1
        assert stray[0].path.name == "notes.xml"

    def test_the_exact_reproduced_bug_no_longer_fails_the_structure_gate(
        self, tmp_path: Path
    ) -> None:
        from smith.health import Health, check_structure

        home = _minimal_home(tmp_path)
        (tmp_path / "test-results-ubuntu-latest-py3.12.xml").write_text(
            "<testsuite/>", encoding="utf-8"
        )
        result = check_structure(home)
        assert result.health is not Health.FAIL
