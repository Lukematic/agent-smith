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
#   ./install.sh --no-tools      # do not auto-install just or seeds

set -euo pipefail

SMITH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCOPE="global"
NO_LINK=0
NO_TOOLS=0
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --local)   SCOPE="local" ;;
        --global)  SCOPE="global" ;;
        --no-link)  NO_LINK=1 ;;
        --no-tools) NO_TOOLS=1 ;;
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
# Hardlinking fails on network shares, Docker volumes, and any filesystem where
# the uv cache and the project sit on different backing stores. Copy mode is
# slower on first run and works everywhere, which is the right trade for an
# installer a stranger runs once.
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
uv sync --all-groups >/dev/null
ok "dependencies installed into .venv"

# ── 3. just, installed automatically when a package manager is available ─────
# `just` is convenience rather than a requirement, so a failed install warns and
# never blocks. Every recipe also runs as `uv run smith ...`.
#
# `sudo` is used only for system package managers that require it, and only when
# a TTY exists so a piped bootstrap never hangs on a password prompt.
step "Checking just (task runner)"
if command -v just >/dev/null 2>&1; then
    ok "just present"
elif [ "$NO_TOOLS" -eq 1 ]; then
    warn "just not found, skipped by --no-tools. Use 'uv run smith ...' instead"
else
    SUDO=""
    if [ "$(id -u)" -ne 0 ] && [ -t 0 ] && command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    fi

    install_just() {
        # Ordered by how little they need: a user-scoped manager beats a system one.
        if command -v brew >/dev/null 2>&1; then
            warn "just not found, installing with brew"
            brew install just >/dev/null 2>&1 && return 0
        fi
        if command -v cargo >/dev/null 2>&1; then
            warn "just not found, installing with cargo"
            cargo install just >/dev/null 2>&1 && return 0
        fi
        if command -v apt-get >/dev/null 2>&1 && [ -n "$SUDO" ]; then
            warn "just not found, installing with apt-get"
            $SUDO apt-get update -qq >/dev/null 2>&1
            $SUDO apt-get install -y -qq just >/dev/null 2>&1 && return 0
        fi
        if command -v dnf >/dev/null 2>&1 && [ -n "$SUDO" ]; then
            warn "just not found, installing with dnf"
            $SUDO dnf install -y -q just >/dev/null 2>&1 && return 0
        fi
        if command -v pacman >/dev/null 2>&1 && [ -n "$SUDO" ]; then
            warn "just not found, installing with pacman"
            $SUDO pacman -S --noconfirm --quiet just >/dev/null 2>&1 && return 0
        fi
        return 1
    }

    if install_just && command -v just >/dev/null 2>&1; then
        ok "just installed"
    else
        warn "could not install just automatically. Optional: 'uv run smith ...' works without it"
    fi
fi

# ── 4. seeds, only when a JavaScript runtime already exists ──────────────────
# Seeds is an optional issue tracker. Installing a runtime to get it would be a
# large uninvited change, so this only runs when bun or npm is already present.
step "Checking seeds (optional issue tracker)"
if command -v sd >/dev/null 2>&1; then
    ok "seeds present"
elif [ "$NO_TOOLS" -eq 1 ]; then
    warn "seeds not found, skipped by --no-tools"
elif command -v bun >/dev/null 2>&1; then
    warn "seeds not found, installing with bun"
    bun install -g @os-eco/seeds-cli >/dev/null 2>&1
    command -v sd >/dev/null 2>&1 && ok "seeds installed with bun" || warn "bun install did not put sd on PATH"
elif command -v npm >/dev/null 2>&1; then
    warn "seeds not found, installing with npm"
    npm install -g @os-eco/seeds-cli >/dev/null 2>&1
    command -v sd >/dev/null 2>&1 && ok "seeds installed with npm" || warn "npm install did not put sd on PATH"
else
    warn "seeds not installed and no bun or npm found. Optional, Smith works without it"
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

    # A mode is a separate mechanism from a persona: it appears in the mode
    # selector and declares tool groups the editor enforces. Skipping this step
    # left a fresh clone with no modes at all, which is the bug this fixes.
    step "Installing selectable modes (Kilo, Roo)"
    if uv run smith install-mode --force $INSTALL_SCOPE 2>&1 | sed 's/^/  /'; then
        ok "modes installed, reload the editor window to see them"
    else
        warn "no Kilo or Roo installation found, skipping modes"
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
echo "  In your project:      smith onboard"
echo "  Then plan work:       smith plan \"<your task>\""
echo "  Health check:         smith doctor"
echo "  Available skills:     smith skills"
echo



