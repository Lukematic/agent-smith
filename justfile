# Agent Smith — task runner
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
    uv run smith doctor

# Fast health check: skips lint, format, and tests
doctor-fast:
    uv run smith doctor --fast

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
check: lint test validate tidy-check
    @echo "ALL GATES PASSED"

# Everything the doctor checks plus the slow quality gates
verify:
    uv run smith doctor

# ── knowledge harness ────────────────────────────────────────────────────────

# Diff the local registry against upstream and write a drift report
drift:
    uv run smith drift

# Fetch one knowledge file into the cache with provenance
fetch PATH:
    uv run smith fetch "{{PATH}}"

# Refresh stale cache entries and report registry drift
update:
    uv run smith update

# Show cache size, age, and lesson count
status:
    uv run smith status

# ── authoring gates ──────────────────────────────────────────────────────────

# Validate every skill and agent definition. Exits nonzero on FAIL.
validate:
    uv run smith validate skills agents emitted

# Validate one file with full per-check output
validate-one PATH:
    uv run smith validate "{{PATH}}" --verbose

# Prove the validator actually blocks bad input
validate-selftest:
    uv run smith validate --selftest

# ── hygiene ──────────────────────────────────────────────────────────────────

# Repair what is mechanically fixable, report what needs judgement
fix:
    uv run smith fix

# Also archive stray root files
fix-all:
    uv run smith fix --aggressive

# List every skill with its path
skills:
    uv run smith skills

# Find clutter: stray docs, orphaned cache, empty dirs, duplicated content
tidy-check:
    uv run smith tidy --dry-run

# Archive clutter to archive/YYYY-MM-DD/ rather than deleting it
tidy:
    uv run smith tidy

# Delete disposable artifacts. Cache is disposable; memory is not.
clean:
    uv run smith clean

# ── install into the agent harness ───────────────────────────────────────────

# Install the persona and skills into every detected harness
install-harness:
    uv run smith install

# Where is Smith installed?
install-status:
    uv run smith install-status

# Symlink this repo as a global goose plugin plus install the persona
link:
    uv run smith link

# What a human reads first
[private]
help:
    @just --list


