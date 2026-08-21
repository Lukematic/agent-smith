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

.EXAMPLE
    ./install.ps1
    ./install.ps1 -Scope local
#>
[CmdletBinding()]
param(
    [ValidateSet('global', 'local')][string]$Scope = 'global',
    [switch]$NoLink,
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
    uv sync --all-groups | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "uv sync exited $LASTEXITCODE" }
    Write-Ok "dependencies installed into .venv"
} catch {
    Write-Bad "sync failed: $_"
    Pop-Location
    exit 1
} 

# ── 3. optional but recommended: just ────────────────────────────────────────
Write-Step "Checking just (optional task runner)"
if (Get-Command just -ErrorAction SilentlyContinue) {
    Write-Ok "just present"
} else {
    Write-Warn2 "just not found. Recipes still run via 'uv run smith ...'"
    Write-Host "  Install: winget install --id Casey.Just  (or cargo install just)" -ForegroundColor DarkGray
}

# ── 4. self-repair, so a fresh clone is coherent ─────────────────────────────
Write-Step "Regenerating derived files"
uv run smith fix --no-check | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

# ── 5. harness installation ──────────────────────────────────────────────────
if ($NoLink) {
    Write-Step "Skipping harness install (-NoLink)"
} else {
    Write-Step "Installing into the agent harness"

    $base = if ($Scope -eq 'global') { Join-Path $HOME ".agents" } else { Join-Path (Get-Location) ".agents" }
    $pluginDir = Join-Path $base "plugins"
    $agentDir  = Join-Path $base "agents"
    $skillDir  = Join-Path $base "skills"
    New-Item -ItemType Directory -Force -Path $pluginDir, $agentDir, $skillDir | Out-Null

    $pluginLink = Join-Path $pluginDir "agent-smith"

    # A symlink means a git pull updates the install with no re-run. That is the
    # whole reason to prefer it over copying.
    $linked = $false
    if ((Test-Path $pluginLink) -and -not $Force) {
        Write-Ok "plugin already present at $pluginLink"
        $linked = $true
    } else {
        if (Test-Path $pluginLink) { Remove-Item $pluginLink -Recurse -Force }
        try {
            New-Item -ItemType SymbolicLink -Path $pluginLink -Target $smithRoot -ErrorAction Stop | Out-Null
            Write-Ok "plugin symlinked, a git pull now updates it automatically"
            $linked = $true
        } catch {
            Write-Warn2 "symlink needs Developer Mode or an elevated shell, falling back to a junction"
            try {
                New-Item -ItemType Junction -Path $pluginLink -Target $smithRoot -ErrorAction Stop | Out-Null
                Write-Ok "plugin junctioned"
                $linked = $true
            } catch {
                Write-Bad "could not link the plugin: $_"
                Write-Host "  Enable Developer Mode, or run this shell as Administrator." -ForegroundColor Yellow
            }
        }
    }

    # The persona is a separate artifact: plugins carry skills and hooks only.
    $personaSource = Join-Path $smithRoot "agents\agent-smith.md"
    $personaTarget = Join-Path $agentDir "agent-smith.md"
    if (Test-Path $personaSource) {
        Copy-Item $personaSource $personaTarget -Force
        Write-Ok "persona installed to $personaTarget"
    } else {
        Write-Bad "agents/agent-smith.md is missing from the repo"
    }

    if (-not $linked) {
        Write-Warn2 "skills will not load until the plugin link succeeds"
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
Write-Host "  In an agent session:  @agent-smith what is a harness?"
Write-Host "  Health check:         uv run smith doctor"
Write-Host "  Available skills:     uv run smith skills"
Write-Host "  Open a gated run:     uv run smith gate open code-change `"objective`" --scope path"
Write-Host ""
