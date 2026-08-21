#!/usr/bin/env bash
# Install Agent Smith into an agent harness.
#
# One command from a fresh clone to a working install. Installs uv if missing,
# syncs the environment, links the plugin, installs the persona, and verifies the
# result. Idempotent: safe to re-run after a git pull.
#
# Usage:
#   ./install.sh                 # global install to ~/.agents/
#   ./install.sh --local         # project-local install to ./.agents/
#   ./install.sh --no-link       # environment only

set -euo pipefail

SMITH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCOPE="global"
NO_LINK=0
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --local)   SCOPE="local" ;;
        --global)  SCOPE="global" ;;
        --no-link) NO_LINK=1 ;;
        --force)   FORCE=1 ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step() { printf "${CYAN}==> %s${NC}\n" "$1"; }
ok()   { printf "${GREEN}  OK    %s${NC}\n" "$1"; }
warn() { printf "${YELLOW}  WARN  %s${NC}\n" "$1"; }
bad()  { printf "${RED}  FAIL  %s${NC}\n" "$1"; }

echo
echo "Agent Smith installer"
echo "  source: $SMITH_ROOT"
echo "  scope:  $SCOPE"
echo

# ── 1. uv, the one hard dependency ───────────────────────────────────────────
step "Checking uv"
if command -v uv >/dev/null 2>&1; then
    ok "uv present ($(uv --version))"
else
    warn "uv not found, installing from astral.sh"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || { bad "uv installed but not on PATH, open a new shell"; exit 1; }
    ok "uv installed ($(uv --version))"
fi

# ── 2. environment ───────────────────────────────────────────────────────────
step "Syncing the project environment"
cd "$SMITH_ROOT"
uv sync --all-groups >/dev/null
ok "dependencies installed into .venv"

# ── 3. optional task runner ──────────────────────────────────────────────────
step "Checking just (optional task runner)"
if command -v just >/dev/null 2>&1; then
    ok "just present"
else
    warn "just not found. Recipes still run via 'uv run smith ...'"
    echo "  Install: brew install just  (or cargo install just)"
fi

# ── 4. self-repair, so a fresh clone is coherent ─────────────────────────────
step "Regenerating derived files"
uv run smith fix --no-check | sed 's/^/  /'

# ── 5. harness installation ──────────────────────────────────────────────────
# Delegated to `smith install`, which detects Claude Code, Goose, Kilo, and Cursor
# and adapts to each one's layout. Duplicating that logic in shell would mean two
# implementations drifting apart, and the shell copy would be the stale one.
if [ "$NO_LINK" -eq 1 ]; then
    step "Skipping harness install (--no-link)"
else
    step "Installing into every detected agent harness"
    INSTALL_SCOPE=""
    [ "$SCOPE" = "local" ] && INSTALL_SCOPE="--scope project"

    if uv run smith install $INSTALL_SCOPE 2>&1 | sed 's/^/  /'; then
        ok "persona and skills installed"
    else
        warn "no supported harness directory found"
        echo "  Name one explicitly, for example:  uv run smith install --harness claude"
    fi
fi

# ── 6. verify, never assume ──────────────────────────────────────────────────
step "Verifying the install"
DOCTOR_FAILED=0
uv run smith doctor --fast 2>&1 | sed 's/^/  /' || DOCTOR_FAILED=1

step "Running the test suite"
TESTS_FAILED=0
uv run pytest -q 2>&1 | tail -2 | sed 's/^/  /' || TESTS_FAILED=1

echo
if [ "$DOCTOR_FAILED" -ne 0 ] || [ "$TESTS_FAILED" -ne 0 ]; then
    printf "${RED}INSTALL INCOMPLETE${NC}\n"
    echo "  Fix what the doctor reported, then run: uv run smith fix"
    exit 1
fi

printf "${GREEN}INSTALL COMPLETE${NC}\n"
echo
echo "Next:"
echo "  In an agent session:  @agent-smith what is a harness?"
echo "  Health check:         uv run smith doctor"
echo "  Available skills:     uv run smith skills"
echo "  Open a gated run:     uv run smith gate open code-change \"objective\" --scope path"
echo
