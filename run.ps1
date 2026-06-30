# 24시간 구동 런처.
# 서버가 비정상 종료되어도 자동으로 다시 띄운다 (감시 루프).
# 사용: 우클릭 > PowerShell로 실행, 또는  powershell -ExecutionPolicy Bypass -File run.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 가상환경이 있으면 활성화
$venv = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venv) { . $venv }

Write-Host "[agent-core] 24h 구동 시작. 중지하려면 Ctrl+C 두 번." -ForegroundColor Green

while ($true) {
    try {
        python server.py
    } catch {
        Write-Host "[agent-core] 서버 예외: $_" -ForegroundColor Red
    }
    Write-Host "[agent-core] 서버가 종료됨. 5초 후 재시작..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
