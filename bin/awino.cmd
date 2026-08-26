@echo off
setlocal
if not defined AWINO_PROJECT set "AWINO_PROJECT=%CD%"
set "PLUGIN_ROOT=%~dp0.."

where uv >nul 2>nul
if errorlevel 1 (
  >&2 echo DEGRADED: A.W.I.N.O. agent and skills are active, but the deterministic ledger CLI needs uv and Python.
  >&2 echo Install uv, then run this command again. The locked CLI environment will be prepared automatically.
  if "%~1"=="hook" exit /b 0
  exit /b 1
)

if not defined UV_LINK_MODE set "UV_LINK_MODE=copy"
set "VIRTUAL_ENV="
set "CONDA_PREFIX="
uv run --frozen --no-dev --directory "%PLUGIN_ROOT%" awino %*
