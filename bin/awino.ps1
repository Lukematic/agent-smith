#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Launch this checked-out A.W.I.N.O. installation from PowerShell.

.DESCRIPTION
    This is the shipped Windows launcher. It captures the caller's directory as
    the target project, uses this clone's locked environment, and forwards the
    original arguments to `awino`. It never creates a virtual environment in the
    target project.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AwinoArguments
)

$ErrorActionPreference = 'Stop'
$awinoHome = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'DEGRADED  uv is required for the deterministic A.W.I.N.O. CLI. Install uv and retry.'
}

$env:AWINO_PROJECT = (Get-Location).Path
$env:UV_LINK_MODE = if ($env:UV_LINK_MODE) { $env:UV_LINK_MODE } else { 'copy' }
Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
Remove-Item Env:CONDA_PREFIX -ErrorAction SilentlyContinue

& uv run --frozen --no-dev --project $awinoHome awino @AwinoArguments
exit $LASTEXITCODE
