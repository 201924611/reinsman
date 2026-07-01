# 24-hour launcher.
# Automatically restarts the server even if it exits abnormally (watchdog loop).
# Usage: right-click > Run with PowerShell, or  powershell -ExecutionPolicy Bypass -File run.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Activate the virtual environment if present
$venv = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venv) { . $venv }

Write-Host "[agent-core] Starting 24h operation. Press Ctrl+C twice to stop." -ForegroundColor Green

while ($true) {
    try {
        python server.py
    } catch {
        Write-Host "[agent-core] Server exception: $_" -ForegroundColor Red
    }
    Write-Host "[agent-core] Server exited. Restarting in 5 seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
