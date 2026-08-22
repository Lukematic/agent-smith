#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Install Agent Smith into an agent harness.

.DESCRIPTION
    One command from a fresh clone to a working install. Installs uv if missing,
    syncs the environment, links the plugin, installs the persona, and verifies
    the result. Idempotent: safe to re-run after a git pull.

    Every step is verified rather than assumed. A silent partial install is worse
    than a loud failure, because it produces an agent that answers confidently
    from a broken knowledge base.

.PARAMETER Scope
    global  installs to ~/.agents/ so Smith works in every project (default)
    local   installs into the current project's .agents/ only

.PARAMETER NoLink
    Set up the environment but skip harness installation.

.PARAMETER NoTools
    Do not auto-install optional tools (just, seeds). Use in CI or on a locked-down
    machine where installing global tooling is not permitted.

.EXAMPLE
    ./install.ps1
    ./install.ps1 -Scope local
#>
[CmdletBinding()]
param(
    [ValidateSet('global', 'local')][string]$Scope = 'global',
    [switch]$NoLink,
    [switch]$NoTools,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$smithRoot = $PSScriptRoot

function Write-Step  { param([string]$m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m) Write-Host "  OK    $m" -ForegroundColor Green }
function Write-Warn2 { param([string]$m) Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Write-Bad   { param([string]$m) Write-Host "  FAIL  $m" -ForegroundColor Red }

Write-Host ""
Write-Host "Agent Smith installer" -ForegroundColor White
Write-Host "  source: $smithRoot"
Write-Host "  scope:  $Scope"
Write-Host ""

# ── 1. uv, the one hard dependency ───────────────────────────────────────────
Write-Step "Checking uv"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Ok "uv present ($(uv --version))"
} else {
    Write-Warn2 "uv not found, installing from astral.sh"
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Write-Bad "automatic install failed: $_"
        Write-Host "  Install manually from https://docs.astral.sh/uv/ then re-run." -ForegroundColor Yellow
        exit 1
    }
    # The installer edits PATH for future sessions, so surface it for this one.
    $uvBin = Join-Path $HOME ".local\bin"
    if (Test-Path $uvBin) { $env:PATH = "$uvBin;$env:PATH" }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Bad "uv installed but not on PATH. Open a new shell and re-run."
        exit 1
    }
    Write-Ok "uv installed ($(uv --version))"
}

# ── 2. environment ───────────────────────────────────────────────────────────
Write-Step "Syncing the project environment"
Push-Location $smithRoot
try {
    # Hardlinking fails on OneDrive, network shares, Docker volumes, and any
    # filesystem where the uv cache and the project sit on different backing
    # stores. Copy mode is slower on first run and works everywhere, which is the
    # right trade for an installer a stranger runs once.
    if (-not $env:UV_LINK_MODE) { $env:UV_LINK_MODE = 'copy' }
    uv sync --all-groups 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "uv sync exited $LASTEXITCODE" }
    Write-Ok "dependencies installed into .venv"
} catch {
    Write-Bad "sync failed: $_"
    Pop-Location
    exit 1
} 

# ── 3. just, installed automatically when a package manager is available ─────
# `just` is convenience rather than a requirement, so a failed install is a warning
# and never blocks. Every recipe also runs as `uv run smith ...`.
Write-Step "Checking just (task runner)"
if (Get-Command just -ErrorAction SilentlyContinue) {
    Write-Ok "just present"
} elseif ($NoTools) {
    Write-Warn2 "just not found, skipped by -NoTools. Use 'uv run smith ...' instead"
} else {
    # winget first: it is present on current Windows and needs no toolchain.
    # cargo is the fallback for machines that have Rust but not winget.
    $installers = @(
        @{ Name = 'winget'; Args = @('install', '--id', 'Casey.Just', '--source', 'winget',
                                     '--accept-package-agreements', '--accept-source-agreements',
                                     '--disable-interactivity') },
        @{ Name = 'scoop';  Args = @('install', 'just') },
        @{ Name = 'cargo';  Args = @('install', 'just') }
    )
    $installed = $false
    foreach ($candidate in $installers) {
        if (-not (Get-Command $candidate.Name -ErrorAction SilentlyContinue)) { continue }
        Write-Warn2 "just not found, installing with $($candidate.Name)"
        try {
            & $candidate.Name @($candidate.Args) 2>&1 | Out-Null
        } catch {
            Write-Warn2 "$($candidate.Name) failed: $_"
            continue
        }
        # winget updates PATH for future sessions only, so re-read it now.
        $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' +
                    [Environment]::GetEnvironmentVariable('PATH', 'User')
        if (Get-Command just -ErrorAction SilentlyContinue) {
            Write-Ok "just installed with $($candidate.Name)"
            $installed = $true
            break
        }
        Write-Warn2 "$($candidate.Name) reported success but just is not yet on PATH"
    }
    if (-not $installed) {
        Write-Warn2 "could not install just automatically. Optional: 'uv run smith ...' works without it"
    }
}

# ── 4. seeds, only when a JavaScript runtime already exists ──────────────────
# Seeds is an optional issue tracker. Installing a runtime to get it would be a
# large uninvited change, so this only runs when bun or npm is already present.
Write-Step "Checking seeds (optional issue tracker)"
if (Get-Command sd -ErrorAction SilentlyContinue) {
    Write-Ok "seeds present"
} elseif ($NoTools) {
    Write-Warn2 "seeds not found, skipped by -NoTools"
} else {
    $runtime = @('bun', 'npm') | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
    if (-not $runtime) {
        Write-Warn2 "seeds not installed and no bun or npm found. Optional, Smith works without it"
    } else {
        Write-Warn2 "seeds not found, installing with $runtime"
        try {
            if ($runtime -eq 'bun') { bun install -g '@os-eco/seeds-cli' 2>&1 | Out-Null }
            else { npm install -g '@os-eco/seeds-cli' 2>&1 | Out-Null }
        } catch {
            Write-Warn2 "$runtime failed: $_"
        }
        if (Get-Command sd -ErrorAction SilentlyContinue) {
            Write-Ok "seeds installed with $runtime"
        } else {
            Write-Warn2 "could not install seeds automatically. Optional, Smith works without it"
        }
    }
}

# ── 4. self-repair, so a fresh clone is coherent ─────────────────────────────
Write-Step "Regenerating derived files"
uv run smith fix --no-check | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

# ── 5. harness installation ──────────────────────────────────────────────────
# Delegated to `smith install`, which detects Claude Code, Goose, Kilo, and Cursor
# and adapts to each one's layout. Duplicating that logic in shell would mean two
# implementations drifting apart, and the shell copy would be the stale one.
if ($NoLink) {
    Write-Step "Skipping harness install (-NoLink)"
} else {
    Write-Step "Installing into every detected agent harness"

    $installArgs = @('install')
    if ($Scope -eq 'local') { $installArgs += @('--scope', 'project') }

    $output = & uv run smith @installArgs 2>&1
    $installOk = $LASTEXITCODE -eq 0
    $output | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

    if ($installOk) {
        Write-Ok "persona and skills installed"
    } else {
        Write-Warn2 "no supported harness directory found"
        Write-Host "  Name one explicitly, for example:  uv run smith install --harness claude" -ForegroundColor Yellow
    }

    # ── 5b. selectable modes for Kilo and Roo ────────────────────────────────
    # A mode is a separate mechanism from a persona: it appears in the mode
    # selector and declares tool groups the editor enforces. Skipping this step
    # left a fresh clone with no modes at all, which is the bug this fixes.
    Write-Step "Installing selectable modes (Kilo, Roo)"
    $modeArgs = @('install-mode', '--force')
    if ($Scope -eq 'local') { $modeArgs += @('--scope', 'project') }

    $modeOut = & uv run smith @modeArgs 2>&1
    $modeOk = $LASTEXITCODE -eq 0
    $modeOut | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    if ($modeOk) {
        Write-Ok "modes installed, reload the editor window to see them"
    } else {
        Write-Warn2 "no Kilo or Roo installation found, skipping modes"
    }
}

# ── 6. verify, never assume ──────────────────────────────────────────────────
Write-Step "Verifying the install"
$doctor = uv run smith doctor --fast 2>&1
$doctor | ForEach-Object { Write-Host "  $_" }
$doctorFailed = $LASTEXITCODE -ne 0

Write-Step "Running the test suite"
uv run pytest -q 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Host "  $_" }
$testsFailed = $LASTEXITCODE -ne 0

Pop-Location

Write-Host ""
if ($doctorFailed -or $testsFailed) {
    Write-Host "INSTALL INCOMPLETE" -ForegroundColor Red
    Write-Host "  Fix what the doctor reported, then run: uv run smith fix" -ForegroundColor Yellow
    exit 1
}

Write-Host "INSTALL COMPLETE" -ForegroundColor Green
Write-Host ""
Write-Host "Next:" -ForegroundColor White
Write-Host "  In your project:      smith onboard"
Write-Host "  Then plan work:       smith plan `"<your task>`""
Write-Host "  Health check:         smith doctor"
Write-Host "  Available skills:     smith skills"
Write-Host ""
