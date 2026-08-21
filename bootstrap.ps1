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
        SMITH_DIR    clone location, overrides the default below
        SMITH_REF    branch or tag to check out, default main

    Where it clones, in order:
        1. -Dir or SMITH_DIR, if given
        2. ./agent-smith in the current directory, when that is a sensible place
        3. ~/dev/agent-smith, when the current directory is a home or system root

    Cloning into the current directory is the least surprising default: you ran the
    command from somewhere deliberately. The fallback exists because dropping a
    clone into a home directory root or C:\ is not what anyone means.
#>
[CmdletBinding()]
param(
    [string]$Repo = $(if ($env:SMITH_REPO) { $env:SMITH_REPO } else { "https://github.com/Lukematic/agent-smith.git" }),
    [string]$Dir  = "",
    [string]$Ref  = $(if ($env:SMITH_REF)  { $env:SMITH_REF }  else { "main" }),
    [switch]$Local,
    [switch]$NoTools
)

$ErrorActionPreference = 'Stop'

function Step { param([string]$m) Write-Host "==> $m" -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host "  OK    $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Bad  { param([string]$m) Write-Host "  FAIL  $m" -ForegroundColor Red }

# ── decide where to clone ────────────────────────────────────────────────────
function Resolve-CloneDir {
    param([string]$Requested)

    if ($Requested) { return $Requested }
    if ($env:SMITH_DIR) { return $env:SMITH_DIR }

    $here = (Get-Location).Path

    # Refuse a few places where a stray clone would be genuinely unwelcome.
    $unsuitable = @(
        $HOME,
        [Environment]::GetFolderPath('MyDocuments'),
        [Environment]::GetFolderPath('Desktop'),
        [Environment]::GetFolderPath('UserProfile'),
        $env:SystemDrive + '\',
        $env:WINDIR
    ) | Where-Object { $_ }

    foreach ($bad in $unsuitable) {
        if ($here.TrimEnd('\') -ieq $bad.TrimEnd('\')) {
            $fallback = Join-Path $HOME "dev\agent-smith"
            Warn "current directory is $here, which is not a good place for a clone"
            Write-Host "  Using $fallback instead. Pass -Dir to choose your own." -ForegroundColor DarkGray
            return $fallback
        }
    }

    return (Join-Path $here "agent-smith")
}

$Dir = Resolve-CloneDir -Requested $Dir


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

