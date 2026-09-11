# Tidy and Exam Hygiene v0.7.2

## Problem

1. `Tidier` in `src/awino/tidy.py` flagged the valid FAIR directory `session` as a stray root directory because `"session"` was omitted from `ROOT_ALLOWED_DIRS`.
2. `_fixture(root)` in `src/awino/exam.py` omitted `README.md` and `src/`, causing two probes (`best.session-order` and `elevator.remembers`) to fail with `REFUSED 1 health gate(s) failing: structure`.
3. The canonical status header across `AWINO.md`, `agents/awino.md`, `docs/agent-guide.md`, `docs/operating-guide.md`, and `src/awino/modes.py` lacked the `stance:` field.

## Decision

1. Add `"session"` to `ROOT_ALLOWED_DIRS` in `src/awino/tidy.py`.
2. Add `README.md` and `src/` to `_fixture()` in `src/awino/exam.py`.
3. Standardize the status header with `| stance: <stance>` across all personas and documentation.

## Verification

1. `uv run awino exam` fires 18/18 capabilities (100%).
2. `uv run pytest` passes.
3. `uv run ruff check src tests` passes.
4. `awino doctor --fast` reports `HEALTH ok=15 warn=2 fail=0`.
