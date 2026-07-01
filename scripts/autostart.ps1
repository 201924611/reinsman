# Auto-start agent-core at Windows logon — no admin required (uses the Startup folder).
# Install:   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Install
# Remove:    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Uninstall
# Drops a hidden launcher in the user's Startup folder that runs run_server.ps1
# (watchdog: restarts the server if it exits). Runs only for the current user.
param(
    [switch]$Install,
    [switch]$Uninstall
)

$repo    = Split-Path $PSScriptRoot -Parent          # scripts/ lives under the repo root
$startup = [Environment]::GetFolderPath('Startup')
$vbs     = Join-Path $startup "AgentCore.vbs"

if ($Uninstall) {
    if (Test-Path $vbs) { Remove-Item $vbs -Force; Write-Host "[agent-core] autostart removed: $vbs" }
    else { Write-Host "[agent-core] no autostart entry found." }
    return
}

if (-not $Install) {
    Write-Host "Usage: autostart.ps1 -Install | -Uninstall"
    return
}

$runner = Join-Path $repo "run_server.ps1"
if (-not (Test-Path $runner)) { Write-Error "run_server.ps1 not found at $runner"; return }

# A .vbs launcher runs PowerShell with no visible window (unlike a .cmd, which flashes).
$vbsBody = @"
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$runner""", 0, False
"@
Set-Content -Path $vbs -Value $vbsBody -Encoding ASCII
Write-Host "[agent-core] autostart installed: $vbs"
Write-Host "[agent-core] launches run_server.ps1 hidden at each logon. Start now:  powershell -File `"$runner`""
