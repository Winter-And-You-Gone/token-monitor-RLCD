@echo off
REM RLCD pet hook wrapper for zcode plugins.
REM Forwards the event name to the project's bridge/pet_hook.js with --agent zcode.
REM
REM %1 = event name (SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/Stop/PermissionRequest)
REM
REM zcode injects CLAUDE_PLUGIN_ROOT (see zcode.cjs Vkt env injection) pointing at
REM this plugin's root directory (rlcd-pet-zcode/). The project root is one level
REM up, so pet_hook.js is reached without hardcoding absolute paths. pet_hook.js
REM reads bridge/.env itself for RLCD_AUTH_TOKEN / RLCD_BRIDGE_URL, so no env setup
REM is needed here.
REM
REM Fail-open: any error exits 0 so a stopped bridge never blocks the agent.

if "%~1"=="" exit /b 0

setlocal

set "PLUGIN_ROOT=%CLAUDE_PLUGIN_ROOT%"
if not defined PLUGIN_ROOT (
  REM Fallback: derive from this script's location if zcode didn't inject the env.
  set "PLUGIN_ROOT=%~dp0.."
)

REM PLUGIN_ROOT is rlcd-pet-zcode/, one level below the project root,
REM so bridge/pet_hook.js is reached via a single "..".
set "PET_HOOK=%PLUGIN_ROOT%\..\bridge\pet_hook.js"

where node >nul 2>nul
if errorlevel 1 exit /b 0

if not exist "%PET_HOOK%" exit /b 0

node "%PET_HOOK%" %~1 --agent zcode
exit /b 0
