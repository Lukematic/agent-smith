"""Read-only project configuration audit: findings model and each detector.

Every test builds a small disposable fixture project so detectors are proven
against inline data, not against A.W.I.N.O.'s own repository. The fixture
projects used across this file correspond to acceptance criteria (a)-(e):
uv+Make, Just-only, Poetry, Node/npm, and mixed-config-with-conflict.
"""

from __future__ import annotations

from pathlib import Path

from smith.config_review import (
    Category,
    Finding,
    Severity,
    as_json,
    check_ci,
    check_env_files,
    check_harness_config,
    check_lockfile,
    check_permissions,
    check_pyproject,
    check_readme_drift,
    check_task_runners,
    review,
)

# ── Finding model ──────────────────────────────────────────────────────────────


class TestFindingModel:
    def test_citation_uses_relative_path_and_line(self, tmp_path: Path) -> None:
        path = tmp_path / "Makefile"
        finding = Finding(Severity.WARN, Category.TASK_RUNNER, path, "dup target", line=5)
        assert finding.citation(tmp_path) == "Makefile:5"

    def test_citation_without_line_omits_it(self, tmp_path: Path) -> None:
        path = tmp_path / "pyproject.toml"
        finding = Finding(Severity.INFO, Category.PYPROJECT, path, "no tool sections")
        assert finding.citation(tmp_path) == "pyproject.toml"

    def test_as_dict_is_json_shaped(self, tmp_path: Path) -> None:
        path = tmp_path / ".env"
        finding = Finding(
            Severity.ERROR,
            Category.ENV_FILE,
            path,
            "not gitignored",
            suggested_command="git check-ignore -v .env",
        )
        payload = finding.as_dict(tmp_path)
        assert payload == {
            "severity": "error",
            "category": "env-file",
            "citation": ".env",
            "message": "not gitignored",
            "suggested_command": "git check-ignore -v .env",
        }


# ── pyproject.toml ─────────────────────────────────────────────────────────────


class TestPyprojectDetector:
    def test_missing_file_produces_no_findings(self, tmp_path: Path) -> None:
        assert check_pyproject(tmp_path) == []

    def test_unparseable_toml_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("not = [valid", encoding="utf-8")
        findings = check_pyproject(tmp_path)
        assert len(findings) == 1
        assert findings[0].severity is Severity.ERROR
        assert "does not parse" in findings[0].message

    def test_missing_requires_python_warns(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\n[tool.ruff]\n', encoding="utf-8"
        )
        findings = check_pyproject(tmp_path)
        assert any(f.severity is Severity.WARN and "requires-python" in f.message for f in findings)

    def test_no_tool_sections_is_informational(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nrequires-python = ">=3.12"\n', encoding="utf-8"
        )
        findings = check_pyproject(tmp_path)
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO

    def test_well_formed_pyproject_is_clean(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nrequires-python = ">=3.12"\n[tool.ruff]\n',
            encoding="utf-8",
        )
        assert check_pyproject(tmp_path) == []


# ── uv.lock staleness ──────────────────────────────────────────────────────────


class TestLockfileDetector:
    def test_uv_declared_without_lock_warns(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.uv]\n", encoding="utf-8")
        findings = check_lockfile(tmp_path)
        assert len(findings) == 1
        assert "no uv.lock" in findings[0].message

    def test_lock_older_than_pyproject_warns(self, tmp_path: Path) -> None:
        import os
        import time

        (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        time.sleep(0.05)
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.uv]\n", encoding="utf-8")
        # Force an unambiguous ordering regardless of filesystem mtime resolution.
        lock_stat = (tmp_path / "uv.lock").stat()
        os.utime(pyproject, (lock_stat.st_mtime + 5, lock_stat.st_mtime + 5))
        findings = check_lockfile(tmp_path)
        assert len(findings) == 1
        assert "older than pyproject.toml" in findings[0].message

    def test_fresh_lock_is_clean(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.uv]\n", encoding="utf-8")
        (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        assert check_lockfile(tmp_path) == []

    def test_no_pyproject_produces_no_findings(self, tmp_path: Path) -> None:
        assert check_lockfile(tmp_path) == []


# ── task runners ───────────────────────────────────────────────────────────────


class TestTaskRunnerDetector:
    def test_duplicate_target_in_one_makefile_warns(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text(
            "test:\n\tpytest\n\ntest:\n\tpytest -v\n", encoding="utf-8"
        )
        findings = check_task_runners(tmp_path)
        assert len(findings) == 1
        assert findings[0].severity is Severity.WARN
        assert "duplicate target" in findings[0].message

    def test_identical_redefinition_is_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("test:\n\tpytest\n\ntest:\n\tpytest\n", encoding="utf-8")
        assert check_task_runners(tmp_path) == []

    def test_makefile_and_justfile_conflicting_task_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("test:\n\tpytest -q\n", encoding="utf-8")
        (tmp_path / "justfile").write_text("test:\n    pytest -v\n", encoding="utf-8")
        findings = check_task_runners(tmp_path)
        conflicts = [f for f in findings if f.severity is Severity.ERROR]
        assert len(conflicts) == 1
        assert "defined differently" in conflicts[0].message

    def test_makefile_and_justfile_agreeing_task_is_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("test:\n\tpytest -q\n", encoding="utf-8")
        (tmp_path / "justfile").write_text("test:\n    pytest -q\n", encoding="utf-8")
        assert check_task_runners(tmp_path) == []

    def test_just_only_project_is_never_told_to_use_make(self, tmp_path: Path) -> None:
        (tmp_path / "justfile").write_text(
            "test:\n    pytest -q\n\nlint:\n    ruff check .\n", encoding="utf-8"
        )
        findings = check_task_runners(tmp_path)
        assert findings == []
        assert not any("make" in f.message.lower() for f in findings)


# ── CI ──────────────────────────────────────────────────────────────────────────


class TestCiDetector:
    def _workflows(self, tmp_path: Path) -> Path:
        directory = tmp_path / ".github" / "workflows"
        directory.mkdir(parents=True)
        return directory

    def test_missing_workflows_dir_produces_no_findings(self, tmp_path: Path) -> None:
        assert check_ci(tmp_path) == []

    def test_missing_test_step_warns(self, tmp_path: Path) -> None:
        workflows = self._workflows(tmp_path)
        (workflows / "ci.yml").write_text(
            "name: CI\non: [push]\njobs:\n  build:\n    steps:\n      - run: ruff check .\n",
            encoding="utf-8",
        )
        findings = check_ci(tmp_path)
        assert any(f.severity is Severity.WARN and "test step" in f.message for f in findings)

    def test_missing_lint_step_is_informational(self, tmp_path: Path) -> None:
        workflows = self._workflows(tmp_path)
        (workflows / "ci.yml").write_text(
            "name: CI\non: [push]\njobs:\n  build:\n    steps:\n      - run: pytest -q\n",
            encoding="utf-8",
        )
        findings = check_ci(tmp_path)
        assert any(f.severity is Severity.INFO and "lint step" in f.message for f in findings)

    def test_hardcoded_secret_pattern_is_an_error_with_line_number(self, tmp_path: Path) -> None:
        workflows = self._workflows(tmp_path)
        (workflows / "ci.yml").write_text(
            "name: CI\non: [push]\njobs:\n  build:\n    steps:\n"
            "      - run: pytest -q\n"
            '      - env:\n          API_KEY: "sk-abcdef1234567890"\n',
            encoding="utf-8",
        )
        findings = check_ci(tmp_path)
        secret_findings = [f for f in findings if f.severity is Severity.ERROR]
        assert len(secret_findings) == 1
        assert secret_findings[0].line == 8

    def test_templated_secret_reference_is_not_flagged(self, tmp_path: Path) -> None:
        workflows = self._workflows(tmp_path)
        (workflows / "ci.yml").write_text(
            "name: CI\non: [push]\njobs:\n  build:\n    steps:\n"
            "      - run: pytest -q\n"
            '      - env:\n          API_KEY: "${{ secrets.API_KEY }}"\n',
            encoding="utf-8",
        )
        findings = check_ci(tmp_path)
        assert not any(f.severity is Severity.ERROR for f in findings)

    def test_clean_workflow_with_lint_and_test_is_not_flagged(self, tmp_path: Path) -> None:
        workflows = self._workflows(tmp_path)
        (workflows / "ci.yml").write_text(
            "name: CI\non: [push]\njobs:\n  build:\n    steps:\n"
            "      - run: ruff check .\n"
            "      - run: pytest -q\n",
            encoding="utf-8",
        )
        assert check_ci(tmp_path) == []


# ── harness config ─────────────────────────────────────────────────────────────


class TestHarnessConfigDetector:
    def test_differing_agents_and_claude_md_warns(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("Be helpful.\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("Be terse.\n", encoding="utf-8")
        findings = check_harness_config(tmp_path)
        assert any("AGENTS.md and CLAUDE.md" in f.message for f in findings)

    def test_identical_agents_and_claude_md_is_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("Be helpful.\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("Be helpful.\n", encoding="utf-8")
        assert check_harness_config(tmp_path) == []

    def test_duplicate_persona_name_across_dirs_warns(self, tmp_path: Path) -> None:
        kilo_agent = tmp_path / ".kilo" / "agent"
        kilo_modes = tmp_path / ".kilo" / "modes"
        kilo_agent.mkdir(parents=True)
        kilo_modes.mkdir(parents=True)
        (kilo_agent / "reviewer.md").write_text(
            "---\nname: reviewer\n---\nBody A\n", encoding="utf-8"
        )
        (kilo_modes / "reviewer.md").write_text(
            "---\nname: reviewer\n---\nBody B\n", encoding="utf-8"
        )
        findings = check_harness_config(tmp_path)
        assert any("reviewer" in f.message for f in findings)

    def test_no_harness_files_produces_no_findings(self, tmp_path: Path) -> None:
        assert check_harness_config(tmp_path) == []


# ── .env files ──────────────────────────────────────────────────────────────────


class TestEnvFileDetector:
    def test_no_env_file_produces_no_findings(self, tmp_path: Path) -> None:
        assert check_env_files(tmp_path) == []

    def test_env_not_gitignored_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("SECRET=x\n", encoding="utf-8")
        findings = check_env_files(tmp_path)
        errors = [f for f in findings if f.severity is Severity.ERROR]
        assert len(errors) == 1
        assert "not listed in .gitignore" in errors[0].message

    def test_env_values_are_never_included_in_any_finding(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("SUPER_SECRET_VALUE=do-not-leak-this\n", encoding="utf-8")
        (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
        findings = check_env_files(tmp_path)
        for finding in findings:
            assert "do-not-leak-this" not in finding.message
            assert "SUPER_SECRET_VALUE" not in finding.message

    def test_env_gitignored_with_example_is_clean_besides_presence_note(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".env").write_text("KEY=x\n", encoding="utf-8")
        (tmp_path / ".env.example").write_text("KEY=\n", encoding="utf-8")
        (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
        findings = check_env_files(tmp_path)
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO

    def test_missing_env_example_warns(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("KEY=x\n", encoding="utf-8")
        (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
        findings = check_env_files(tmp_path)
        assert any(f.severity is Severity.WARN and ".env.example" in f.message for f in findings)


# ── README drift ────────────────────────────────────────────────────────────────


class TestReadmeDriftDetector:
    def test_undocumented_task_is_informational(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text(
            "test:\n\tpytest\n\nrelease:\n\techo hi\n", encoding="utf-8"
        )
        (tmp_path / "README.md").write_text("Run `make test` to run tests.\n", encoding="utf-8")
        findings = check_readme_drift(tmp_path)
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO
        assert "release" in findings[0].message

    def test_all_documented_tasks_are_clean(self, tmp_path: Path) -> None:
        (tmp_path / "justfile").write_text("test:\n    pytest\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("Run `just test`.\n", encoding="utf-8")
        assert check_readme_drift(tmp_path) == []

    def test_no_readme_produces_no_findings(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
        assert check_readme_drift(tmp_path) == []


# ── kilo.json permissions ───────────────────────────────────────────────────────


class TestPermissionsDetector:
    def test_broad_wildcard_allow_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "kilo.json").write_text(
            '{\n  "permissions": {\n    "*": "allow"\n  }\n}\n', encoding="utf-8"
        )
        findings = check_permissions(tmp_path)
        assert len(findings) == 1
        assert findings[0].severity is Severity.ERROR
        assert findings[0].line == 3

    def test_scoped_permissions_are_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "kilo.json").write_text(
            '{\n  "permissions": {\n    "read": "allow",\n    "write": "ask"\n  }\n}\n',
            encoding="utf-8",
        )
        assert check_permissions(tmp_path) == []

    def test_unparseable_kilo_json_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "kilo.json").write_text("{not valid json", encoding="utf-8")
        findings = check_permissions(tmp_path)
        assert len(findings) == 1
        assert "does not parse" in findings[0].message

    def test_no_kilo_json_produces_no_findings(self, tmp_path: Path) -> None:
        assert check_permissions(tmp_path) == []


# ── disposable fixture projects (acceptance criteria a-e) ────────────────────


class TestFixtureProjects:
    """Five disposable fixtures matching the acceptance criteria exactly."""

    def test_a_uv_and_make_project_is_clean(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "a"\nrequires-python = ">=3.12"\n[tool.uv]\n[tool.ruff]\n',
            encoding="utf-8",
        )
        (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        (tmp_path / "Makefile").write_text(
            "test:\n\tuv run pytest -q\n\nlint:\n\tuv run ruff check .\n", encoding="utf-8"
        )
        (tmp_path / "README.md").write_text("Run `make test` and `make lint`.\n", encoding="utf-8")
        findings = review(tmp_path)
        assert findings == []

    def test_b_just_only_project_is_clean_and_never_suggests_make(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "b"\nrequires-python = ">=3.12"\n[tool.ruff]\n',
            encoding="utf-8",
        )
        (tmp_path / "justfile").write_text(
            "test:\n    pytest -q\n\nlint:\n    ruff check .\n", encoding="utf-8"
        )
        (tmp_path / "README.md").write_text("Run `just test` and `just lint`.\n", encoding="utf-8")
        findings = review(tmp_path)
        assert findings == []
        assert not any("make" in f.message.lower() for f in findings)

    def test_c_poetry_project_is_not_told_to_use_uv(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "c"\nversion = "0.1.0"\n[project]\nrequires-python = ">=3.11"\n',
            encoding="utf-8",
        )
        (tmp_path / "poetry.lock").write_text("", encoding="utf-8")
        findings = review(tmp_path)
        assert not any("uv" in f.message.lower() for f in findings)

    def test_d_node_npm_project_has_no_python_findings(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            '{"name": "d", "scripts": {"test": "jest"}}\n', encoding="utf-8"
        )
        findings = review(tmp_path)
        assert findings == []

    def test_e_mixed_config_conflict_is_detected(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "e"\nrequires-python = ">=3.12"\n[tool.uv]\n',
            encoding="utf-8",
        )
        (tmp_path / "Makefile").write_text("test:\n\tpytest -q\n", encoding="utf-8")
        (tmp_path / "justfile").write_text("test:\n    pytest --cov\n", encoding="utf-8")
        findings = review(tmp_path)
        conflicts = [f for f in findings if f.category is Category.TASK_RUNNER]
        assert any(f.severity is Severity.ERROR for f in conflicts)
        errors = [f for f in findings if f.severity is Severity.ERROR]
        assert errors, "conflict detection produced no error-severity finding"


# ── as_json ────────────────────────────────────────────────────────────────────


class TestAsJson:
    def test_summary_counts_match_findings(self, tmp_path: Path) -> None:
        import json

        (tmp_path / "kilo.json").write_text('{"permissions": {"*": "allow"}}\n', encoding="utf-8")
        findings = review(tmp_path)
        payload = json.loads(as_json(tmp_path, findings))
        assert payload["count"] == len(findings)
        assert payload["summary"]["error"] == sum(
            1 for f in findings if f.severity is Severity.ERROR
        )
        assert all("citation" in item for item in payload["findings"])
