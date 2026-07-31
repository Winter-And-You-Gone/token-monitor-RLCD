# Install RLCD bridge daemon as a Windows Task Scheduler job.
# PowerShell equivalent of scripts/install-bridge-linux.sh.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install-bridge-windows.ps1
# Run from anywhere - it resolves paths relative to the repo root.
#
# The task runs `uv run python bridge.py` directly (no .cmd wrapper, no VBS).
# uv is a console app but Task Scheduler launches it via taskhostw.exe with no
# visible window, so nothing lingers on the desktop. This is more reliable
# than a hidden-cmd VBS launcher, which silently fails in non-interactive
# logon sessions.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = (Resolve-Path "$ScriptDir\..").Path
$BridgeDir = Join-Path $RepoRoot "bridge"
$XmlTpl    = Join-Path $ScriptDir "rlcd-bridge.xml"
$TaskName  = "RLCD-Bridge"

if (-not (Test-Path $BridgeDir)) { throw "bridge dir not found: $BridgeDir" }
if (-not (Test-Path $XmlTpl))    { throw "task template not found: $XmlTpl" }

# Locate uv: prefer the uv-managed venv, fall back to the pip entry point.
$UvCandidates = @(
  "$env:APPDATA\uv\venv\Scripts\uv.exe",
  "$env:APPDATA\Python\Scripts\uv.exe"
)
$UvExe = $UvCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $UvExe) { throw "uv.exe not found in: $($UvCandidates -join ', ')" }

$user = "$env:USERDOMAIN\$env:USERNAME"

# Read template and substitute placeholders (path-agnostic, machine-agnostic).
$xml = Get-Content -Raw $XmlTpl
$xml = $xml -replace '\$\{USER\}',      [System.Security.SecurityElement]::Escape($user)
$xml = $xml -replace '\$\{REPO_ROOT\}', [System.Security.SecurityElement]::Escape($RepoRoot)
$xml = $xml -replace '\$\{UV_EXE\}',    [System.Security.SecurityElement]::Escape($UvExe)

$action    = New-ScheduledTaskAction -Execute $UvExe -Argument "run python bridge.py" -WorkingDirectory $BridgeDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
             -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -DontStopIfGoingOnBatteries -DontStopOnIdleEnd

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "Installed and started: $TaskName"
Write-Host "  uv:      $UvExe"
Write-Host "  Status:  schtasks /Query /TN `"$TaskName`" /V /FO LIST"
Write-Host "  Logs:    bridge\bridge-server.{out,err}.log"
Write-Host "  Test:    curl --noproxy '*' http://localhost:7777/healthz"
Write-Host ""
Write-Host "Manage:"
Write-Host "  Start:   schtasks /Run  /TN `"$TaskName`""
Write-Host "  Stop:    schtasks /End  /TN `"$TaskName`""
Write-Host "  Disable: schtasks /Change /TN `"$TaskName`" /DISABLE"
Write-Host "  Remove:  schtasks /Delete /TN `"$TaskName`" /F"
