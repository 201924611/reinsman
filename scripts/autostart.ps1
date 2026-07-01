# Register (or remove) agent-core to auto-start at Windows logon.
# Install:   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Install
# Remove:    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Uninstall
# Creates a hidden logon task that runs run_server.ps1 (watchdog: restarts the server if it exits).
param(
    [switch]$Install,
    [switch]$Uninstall,
    [string]$TaskName = "AgentCore"
)

$repo = Split-Path $PSScriptRoot -Parent   # scripts/ lives under the repo root

if ($Uninstall) {
    try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; Write-Host "[agent-core] autostart removed ($TaskName)." }
    catch { Write-Host "[agent-core] no task named $TaskName." }
    return
}

if (-not $Install) {
    Write-Host "Usage: autostart.ps1 -Install | -Uninstall  [-TaskName <name>]"
    return
}

$runner = Join-Path $repo "run_server.ps1"
if (-not (Test-Path $runner)) { Write-Error "run_server.ps1 not found at $runner"; return }

$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "agent-core server — auto-start at logon" -Force | Out-Null
Write-Host "[agent-core] autostart installed ($TaskName). It will launch run_server.ps1 at next logon."
Write-Host "[agent-core] to start now:  powershell -File `"$runner`"    |  to remove:  autostart.ps1 -Uninstall"
