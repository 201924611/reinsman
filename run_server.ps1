# agent-core server launcher (watchdog loop).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File run_server.ps1
Set-Location $PSScriptRoot
$py = "python"   # or set a full path to your python.exe
# If sub-agents build web/Java projects, ensure Node/Java are on PATH, e.g.:
# $env:PATH = "C:\Program Files\nodejs;C:\path\to\jbr\bin;$env:PATH"
while ($true) {
    Write-Host "[agent-core] starting (python -m agent_core) ..."
    & $py -m agent_core
    Write-Host "[agent-core] server exited; restart in 5s"
    Start-Sleep -Seconds 5
}
