#!/usr/bin/env pwsh
<#
.SYNOPSIS
    One-line bootstrap for Agent Smith on a machine with nothing installed.

.DESCRIPTION
    Clones the repository, installs uv if missing, creates an isolated
    environment, links Smith into the agent harness, and verifies the result.

    Run directly from the web:
        irm https://raw.githubusercontent.com/Lukematic/agent-smith/main/bootstrap.ps1 | iex

    Every step is verified rather than assumed. A silent partial install produces
    an agent that answers confidently from a broken knowledge base, which is worse
    than a loud failure.

.NOTES
    Environment overrides:
        SMITH_REPO   repository URL, for a fork
        SMITH_DIR    clone location, default ~/dev/agent-smith
        SMITH_REF    branch or tag to check out, default main
#>
[CmdletBinding()]
param(
    [string]$Repo = $(if ($env:SMITH_REPO) { $env:SMITH_REPO } else { "https://github.com/Lukematic/agent-smith.git" }),
    [string]$Dir  = $(if ($env:SMITH_DIR)  { $env:SMITH_DIR }  else { Join-Path $HOME "dev\agent-smith" }),
    [string]$Ref  = $(if ($env:SMITH_REF)  { $env:SMITH_REF }  else { "main" }),
    [switch]$Local
)

$ErrorActionPreference = 'Stop'

function Step { param([string]$m) Write-Host "==> $m" -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host "  OK    $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Bad  { param([string]$m) Write-Host "  FAIL  $m" -ForegroundColor Red }

Write-Host ""
Write-Host "Agent Smith bootstrap" -ForegroundColor White
Write-Host "  repo: $Repo"
Write-Host "  into: $Dir"
Write-Host ""

# ── git, which cannot be installed silently ──────────────────────────────────
Step "Checking git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Bad "git is required and is not on PATH"
    Write-Host "  Install: winget install --id Git.Git" -ForegroundColor Yellow
    exit 1
}
Ok "git present"

# ── clone or update ──────────────────────────────────────────────────────────
Step "Fetching the repository"
if (Test-Path (Join-Path $Dir ".git")) {
    Push-Location $Dir
    # An existing clone may carry local lessons, so pull rather than replace it.
    git fetch --quiet origin
    git checkout --quiet $Ref
    git pull --quiet --ff-only
    Ok "updated the existing clone at $Dir"
    Pop-Location
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dir) | Out-Null
    git clone --quiet --branch $Ref $Repo $Dir
    if ($LASTEXITCODE -ne 0) { Bad "clone failed"; exit 1 }
    Ok "cloned to $Dir"
}

# ── uv, the one dependency the installer can provide ─────────────────────────
Step "Checking uv"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Ok "uv present ($(uv --version))"
} else {
    Warn "uv not found, installing from astral.sh"
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Bad "automatic install failed: $_"
        Write-Host "  Install manually from https://docs.astral.sh/uv/ then re-run." -ForegroundColor Yellow
        exit 1
    }
    # The installer edits PATH for future sessions; surface it for this one.
    $uvBin = Join-Path $HOME ".local\bin"
    if (Test-Path $uvBin) { $env:PATH = "$uvBin;$env:PATH" }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Bad "uv installed but not on PATH. Open a new shell and re-run."
        exit 1
    }
    Ok "uv installed"
}

# ── hand off to the repository's own installer ───────────────────────────────
Step "Running the installer"
Push-Location $Dir
try {
    $installArgs = @()
    if ($Local) { $installArgs += @('-Scope', 'local') }
    & (Join-Path $Dir "install.ps1") @installArgs
    $installFailed = $LASTEXITCODE -ne 0
} finally {
    Pop-Location
}

Write-Host ""
if ($installFailed) {
    Bad "installation reported problems above"
    Write-Host "  Fix what the doctor reported, then run: cd $Dir; uv run smith fix" -ForegroundColor Yellow
    exit 1
}

Write-Host "BOOTSTRAP COMPLETE" -ForegroundColor Green
Write-Host ""
Write-Host "Smith lives at: $Dir" -ForegroundColor White
Write-Host "Update it with: cd $Dir; git pull" -ForegroundColor DarkGray
Write-Host ""
Write-Host "First commands, in any project:" -ForegroundColor White
Write-Host "  smith context     what Smith thinks home and project are"
Write-Host "  smith mission     what Smith thinks the project is for"
Write-Host "  smith doctor      health, with remedies"
Write-Host ""

