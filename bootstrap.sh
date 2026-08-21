#!/usr/bin/env bash
# One-line bootstrap for Agent Smith on a machine with nothing installed.
#
#   curl -fsSL https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.sh | sh
#
# Clones the repository, installs uv if missing, creates an isolated environment,
# links Smith into the agent harness, and verifies the result.
#
# Every step is verified rather than assumed. A silent partial install produces an
# agent that answers confidently from a broken knowledge base, which is worse than
# a loud failure.
#
# Environment overrides:
#   SMITH_REPO   repository URL, for a fork
#   SMITH_DIR    clone location, default ~/dev/agent-smith
#   SMITH_REF    branch or tag, default main

set -euo pipefail

# Copy mode works on network shares and Docker volumes where hardlinking fails.
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

REPO="${SMITH_REPO:-https://github.com/Lukematic/agent-smith.git}"
DIR="${SMITH_DIR:-$HOME/dev/agent-smith}"
REF="${SMITH_REF:-main}"
SCOPE_ARG=""

for arg in "$@"; do
    case "$arg" in
        --local) SCOPE_ARG="--local" ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    esac
done

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step() { printf "${CYAN}==> %s${NC}\n" "$1"; }
ok()   { printf "${GREEN}  OK    %s${NC}\n" "$1"; }
warn() { printf "${YELLOW}  WARN  %s${NC}\n" "$1"; }
bad()  { printf "${RED}  FAIL  %s${NC}\n" "$1"; }

echo
echo "Agent Smith bootstrap"
echo "  repo: $REPO"
echo "  into: $DIR"
echo

# ── git, which cannot be installed silently ──────────────────────────────────
step "Checking git"
command -v git >/dev/null 2>&1 || { bad "git is required and is not on PATH"; exit 1; }
ok "git present"

# ── clone or update ──────────────────────────────────────────────────────────
step "Fetching the repository"
if [ -d "$DIR/.git" ]; then
    # An existing clone may carry local lessons, so pull rather than replace it.
    git -C "$DIR" fetch --quiet origin
    git -C "$DIR" checkout --quiet "$REF"
    git -C "$DIR" pull --quiet --ff-only
    ok "updated the existing clone at $DIR"
else
    mkdir -p "$(dirname "$DIR")"
    git clone --quiet --branch "$REF" "$REPO" "$DIR"
    ok "cloned to $DIR"
fi

# ── uv, the one dependency the installer can provide ─────────────────────────
step "Checking uv"
if command -v uv >/dev/null 2>&1; then
    ok "uv present ($(uv --version))"
else
    warn "uv not found, installing from astral.sh"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || { bad "uv installed but not on PATH, open a new shell"; exit 1; }
    ok "uv installed"
fi

# ── hand off to the repository's own installer ───────────────────────────────
step "Running the installer"
cd "$DIR"
chmod +x install.sh 2>/dev/null || true
INSTALL_FAILED=0
./install.sh $SCOPE_ARG || INSTALL_FAILED=1

echo
if [ "$INSTALL_FAILED" -ne 0 ]; then
    bad "installation reported problems above"
    echo "  Fix what the doctor reported, then run: cd $DIR && uv run smith fix"
    exit 1
fi

printf "${GREEN}BOOTSTRAP COMPLETE${NC}\n"
echo
echo "Smith lives at: $DIR"
echo "Update it with: cd $DIR && git pull"
echo
echo "First commands, in any project:"
echo "  smith context     what Smith thinks home and project are"
echo "  smith mission     what Smith thinks the project is for"
echo "  smith doctor      health, with remedies"
echo


