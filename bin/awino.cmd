@echo off
setlocal
set "PLUGIN_ROOT=%~dp0.."

where uv >nul 2>nul
if errorlevel 1 (
  >&2 echo DEGRADED: A.W.I.N.O. agent and skills are active, but the deterministic ledger CLI needs uv and Python.
  >&2 echo Install uv, then explicitly run: uv sync --directory "%PLUGIN_ROOT%" --frozen
  if "%~1"=="hook" exit /b 0
  exit /b 1
)

if not exist "%PLUGIN_ROOT%\.venv\Scripts\awino.exe" if not exist "%PLUGIN_ROOT%\.venv\bin\awino" (
  >&2 echo DEGRADED: A.W.I.N.O. agent and skills are active, but the deterministic ledger CLI environment is not prepared.
  >&2 echo Nothing was installed automatically. Explicitly run: uv sync --directory "%PLUGIN_ROOT%" --frozen
  if "%~1"=="hook" exit /b 0
  exit /b 1
)

uv run --no-sync --directory "%PLUGIN_ROOT%" awino %*
