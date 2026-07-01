# agent-core server launcher (full-path python + Node/Java on PATH + watchdog loop).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File run_server.ps1
$dir = "C:\Users\offic\Desktop\claude\agent-core"
$py  = "C:\Users\offic\AppData\Local\Programs\Python\Python312\python.exe"
$jbr = "C:\Program Files\JetBrains\IntelliJ IDEA 2026.1.2\jbr\bin"
$env:PATH = "C:\Program Files\nodejs;$jbr;$env:PATH"
Set-Location $dir
while ($true) {
    Write-Host "[agent-core] starting server.py ..."
    & $py server.py
    Write-Host "[agent-core] server exited; restart in 5s"
    Start-Sleep -Seconds 5
}
