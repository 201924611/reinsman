# reproduce.ps1 — reproduce the "Self-Run Launch" on your own machine (Windows PowerShell)
# Prereq: agent-core server running locally (python -m agent_core) — default port 8848.
# This POSTs the exact launch goal, then polls until the task finishes.

$ErrorActionPreference = "Stop"
$Base = if ($env:AGENT_CORE_URL) { $env:AGENT_CORE_URL } else { "http://127.0.0.1:8848" }

$goalText = "우리 공개 오픈소스 agent-core를 이제까지 없었던 방법으로 홍보하는 계획을 세우고, " +
            "workspace/promo-agent-core/ 에 실행 가능한 자산(문안·스크립트·레포 개선·아티팩트)까지 전부 만들어라. " +
            "상태는 LEDGER.md에 단계별로 남기고, 모든 주장은 실제 레포에서 확인 가능한 사실만 쓰라."

# health check
try { Invoke-RestMethod "$Base/health" | Out-Null }
catch { Write-Error "agent-core server not reachable at $Base. Start it: python -m agent_core"; exit 1 }

# submit goal (UTF-8 bytes — Korean-safe)
$json  = @{ goal = $goalText } | ConvertTo-Json
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
$r = Invoke-RestMethod "$Base/goal" -Method Post -Body $bytes -ContentType "application/json; charset=utf-8"
$tid = $r.task_id
Write-Host "Submitted. task_id = $tid"
Write-Host "Watch it:  $Base/tasks/$tid"

# poll
do {
  Start-Sleep -Seconds 20
  $t = Invoke-RestMethod "$Base/tasks/$tid"
  Write-Host ("  status: {0}" -f $t.status)
} while ($t.status -in @("running","pending","queued"))

Write-Host "Done. Inspect the trace + eval for verifiable provenance:"
Write-Host "  trace: $Base/trace/$tid/view"
Write-Host "  eval : POST $Base/tasks/$tid/evaluate  then GET $Base/eval/$tid"
Write-Host "  files: workspace/promo-agent-core/"
