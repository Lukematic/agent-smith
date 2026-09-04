# A.W.I.N.O. task runner
# https://github.com/casey/just
#
# Every command an agent or human needs, in one discoverable place.
# `just` with no arguments lists them.

set windows-shell := ["pwsh.exe", "-NoLogo", "-NoProfile", "-Command"]

# ── default: show what is available ──────────────────────────────────────────
default:
    @just --list

# ── setup ────────────────────────────────────────────────────────────────────

# Create the venv and install everything including dev deps
install:
    uv sync --all-groups

# Verify the toolchain is present before anything else runs
doctor:
    uv run awino doctor

# Fast health check: skips lint, format, and tests
doctor-fast:
    uv run awino doctor --fast

# Toolchain versions, for debugging a broken environment
versions:
    @uv --version
    @just --version
    @uv run python -c "import sys; print('python', sys.version.split()[0])"
    @uv run ruff --version

# ── quality gates ────────────────────────────────────────────────────────────

# Format code and fix what is auto-fixable
fmt:
    uv run ruff format src tests
    uv run ruff check --fix src tests

# Lint without modifying anything (what CI runs)
lint:
    uv run ruff check src tests
    uv run ruff format --check src tests

# Run the test suite
test:
    uv run pytest

test-cov:
    uv run pytest --cov=smith --cov-report=term-missing

# The single gate. Nothing ships unless this passes.
# `lint` already covers formatting; tidy-check runs last because it is advisory
# about caches and must not block on regenerable artifacts.
check: lint test validate selftest tidy-check
    @echo "ALL GATES PASSED"

# Audit active surfaces for unclassified legacy branding
branding:
    uv run pytest tests/test_branding.py -q

# Prove the validator still blocks a deliberately broken artifact. A validator
# verified only against passing input is untested.
selftest:
    uv run awino validate --selftest

# Everything the doctor checks plus the slow quality gates
verify:
    uv run awino doctor
    uv run awino tidy --dry-run
    uv run awino validate skills agents emitted

# ── knowledge harness ────────────────────────────────────────────────────────

# Diff the local registry against upstream and write a drift report
drift:
    uv run awino drift

# Fetch one knowledge file into the cache with provenance
fetch PATH:
    uv run awino fetch "{{PATH}}"

# Refresh stale cache entries and report registry drift
update:
    uv run awino update

# Show cache size, age, and lesson count
status:
    uv run awino status

# ── authoring gates ──────────────────────────────────────────────────────────

# Validate every skill and agent definition. Exits nonzero on FAIL.
validate:
    uv run awino validate skills agents emitted

# Validate one file with full per-check output
validate-one PATH:
    uv run awino validate "{{PATH}}" --verbose

# Prove the validator actually blocks bad input
validate-selftest:
    uv run awino validate --selftest

# ── hygiene ──────────────────────────────────────────────────────────────────

# Repair what is mechanically fixable, report what needs judgement
fix:
    uv run awino fix

# Also archive stray root files
fix-all:
    uv run awino fix --aggressive

# List every skill with its path
skills:
    uv run awino skills

# Find clutter: stray docs, orphaned cache, empty dirs, duplicated content
tidy-check:
    uv run awino tidy --dry-run

# Archive clutter to archive/YYYY-MM-DD/ rather than deleting it
tidy:
    uv run awino tidy

# Delete disposable artifacts. Cache is disposable; memory is not.
clean:
    uv run awino clean

# ── install into the agent harness ───────────────────────────────────────────

# Install the persona and skills into every detected harness
install-harness:
    uv run awino install

# Where is A.W.I.N.O. installed?
install-status:
    uv run awino install-status

# Symlink this repo as a global goose plugin plus install the persona
link:
    uv run awino link

# What a human reads first
[private]
help:
    @just --list



