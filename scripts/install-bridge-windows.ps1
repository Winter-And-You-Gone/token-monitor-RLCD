# Install RLCD bridge daemon as a Windows Task Scheduler job.
# PowerShell equivalent of scripts/install-bridge-linux.sh.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install-bridge-windows.ps1
# Run from anywhere - it resolves paths relative to the repo root.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = (Resolve-Path "$ScriptDir\..").Path
$BridgeDir = Join-Path $RepoRoot "bridge"
$XmlTpl    = Join-Path $ScriptDir "rlcd-bridge.xml"
$Launcher  = Join-Path $ScriptDir "run-bridge-hidden.vbs"
$TaskName  = "RLCD-Bridge"

if (-not (Test-Path $BridgeDir)) { throw "bridge dir not found: $BridgeDir" }
if (-not (Test-Path $XmlTpl))    { throw "task template not found: $XmlTpl" }
if (-not (Test-Path $Launcher))  { throw "launcher not found: $Launcher" }

$user = "$env:USERDOMAIN\$env:USERNAME"

# Read template and substitute placeholders (path-agnostic, machine-agnostic).
$xml = Get-Content -Raw $XmlTpl
$xml = $xml -replace '\$\{USER\}',      [System.Security.SecurityElement]::Escape($user)
$xml = $xml -replace '\$\{REPO_ROOT\}', [System.Security.SecurityElement]::Escape($RepoRoot)

$action    = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$Launcher`"" -WorkingDirectory $BridgeDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
             -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -DontStopIfGoingOnBatteries -DontStopOnIdleEnd

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "Installed and started: $TaskName"
Write-Host "  Status:  schtasks /Query /TN `"$TaskName`" /V /FO LIST"
Write-Host "  Logs:    bridge\bridge-server.{out,err}.log"
Write-Host "  Test:    curl --noproxy '*' http://localhost:7777/healthz"
Write-Host ""
Write-Host "Manage:"
Write-Host "  Start:   schtasks /Run  /TN `"$TaskName`""
Write-Host "  Stop:    schtasks /End  /TN `"$TaskName`""
Write-Host "  Disable: schtasks /Change /TN `"$TaskName`" /DISABLE"
Write-Host "  Remove:  schtasks /Delete /TN `"$TaskName`" /F"
