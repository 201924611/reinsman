#!/usr/bin/env bash
# reproduce.sh — reproduce the "Self-Run Launch" on your own machine (macOS/Linux)
# Prereq: agent-core server running locally (python -m agent_core) — default port 8848.
# Requires: curl, and (optional) jq for pretty task_id extraction.
set -euo pipefail

BASE="${AGENT_CORE_URL:-http://127.0.0.1:8848}"

GOAL='우리 공개 오픈소스 agent-core를 이제까지 없었던 방법으로 홍보하는 계획을 세우고, workspace/promo-agent-core/ 에 실행 가능한 자산(문안·스크립트·레포 개선·아티팩트)까지 전부 만들어라. 상태는 LEDGER.md에 단계별로 남기고, 모든 주장은 실제 레포에서 확인 가능한 사실만 쓰라.'

# health check
curl -fsS "$BASE/health" >/dev/null || { echo "server not reachable at $BASE — start it: python -m agent_core"; exit 1; }

# submit
RESP=$(curl -fsS -X POST "$BASE/goal" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary "$(printf '{"goal":%s}' "$(printf '%s' "$GOAL" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')")")

if command -v jq >/dev/null 2>&1; then
  TID=$(printf '%s' "$RESP" | jq -r '.task_id')
else
  TID=$(printf '%s' "$RESP" | sed -n 's/.*"task_id"[^"]*"\([^"]*\)".*/\1/p')
fi
echo "Submitted. task_id = $TID"
echo "Watch it:  $BASE/tasks/$TID"

# poll
while :; do
  sleep 20
  STATUS=$(curl -fsS "$BASE/tasks/$TID" | (jq -r '.status' 2>/dev/null || sed -n 's/.*"status"[^"]*"\([^"]*\)".*/\1/p'))
  echo "  status: $STATUS"
  case "$STATUS" in running|pending|queued) ;; *) break;; esac
done

echo "Done. Verifiable provenance:"
echo "  trace: $BASE/trace/$TID/view"
echo "  eval : POST $BASE/tasks/$TID/evaluate ; GET $BASE/eval/$TID"
echo "  files: workspace/promo-agent-core/"
