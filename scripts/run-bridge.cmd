@echo off
:: RLCD bridge launcher (called by Task Scheduler at logon / on failure).
:: Keeps the bridge daemon alive; Task Scheduler restarts it on crash.
:: Path-agnostic: resolves the repo root from this script's own location.

setlocal
set "REPO_ROOT=%~dp0.."
set "BRIDGE_DIR=%REPO_ROOT%\bridge"
set "UV=%APPDATA%\uv\venv\Scripts\uv.exe"
if not exist "%UV%" set "UV=%APPDATA%\Python\Scripts\uv.exe"

cd /d "%BRIDGE_DIR%"
call "%UV%" run python bridge.py
