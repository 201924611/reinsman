# LEDGER — promo-agent-core (단일 진실 원천)

> 세션/턴 한도로 끊겨도 다음 실행은 이 파일만 읽고 이어간다. 각 단계 끝 즉시 갱신.

## 현재 단계
✅ 전체 완료 (STEP1~5 + save_knowledge). 남은 건 사람 1클릭(HANDOFF.md).

## 마지막 체크포인트
- 2026-07-18: STEP3(plan.md)·STEP4(자산 10종) 완료. VERIFICATION.md: 사실검증 전항목 PASS, 실행가능성 PASS(데모GIF만 사람 1회 녹화). LICENSE(MIT) 레포 루트 생성.

## 다음 할 일 (1줄)
STEP5: HANDOFF.md(사람 1클릭 체크리스트+측정법) → save_knowledge 요약.

## 단계 체크박스
- [x] STEP 1 리서치(경량): OSS 에이전트 도구 실제 홍보법·성과 → '이미 있는 방법' 목록 (DoD)
- [x] STEP 2 novelty 후보 생성 + 게이트(★점수+자기반박) → 채택 1~2 + 탈락사유 (DoD)
- [x] STEP 3 실행 계획: 단계·일정·채널·성공지표 → plan.md (DoD)
- [x] STEP 4 자산 제작: 문안·스크립트·레포 diff·아티팩트 + 각 검증게이트 PASS (DoD)
- [x] STEP 5 HANDOFF: 사람 1클릭 체크리스트 + 측정법 (HANDOFF.md)
- [x] save_knowledge 요약 저장

## 생성 자산 경로
- LEDGER.md, research.md, candidates.md, plan.md, VERIFICATION.md, PUBLIC_LEDGER.md
- assets/PROVENANCE.md, assets/README-provenance-snippet.md, assets/reproduce.ps1, assets/reproduce.sh, assets/demo_capture_plan.md
- channels/showhn.md, channels/reddit_selfhosted.md, channels/x_thread.md, channels/awesome_pr.md
- (레포 루트) LICENSE  ← 신규

---

## ⚠️ 검증된 그라운드트루스 (사실검증 게이트 근거 — 과장 금지)
- **실제 GitHub URL = `https://github.com/201924611/auto_-orchestration`** (미션의 'agent-core'는 프로젝트 별칭. 링크는 반드시 실제 URL 사용).
- **LICENSE 파일 없음** — README/미션은 MIT라 하나 레포에 LICENSE 파일이 없다. → 자산에 LICENSE(MIT) 파일 생성 포함 + 체크리스트 명시. "MIT" 문구는 LICENSE 추가 후에만 사실.
- **스타 수 미확인** — 홍보 문안에 스타/사용자 수치 절대 주장 금지.
- 검증된 차별화 3축(측정치 포함, knowledge 출처):
  1. Self-tooling: 필요 도구를 에이전트가 코드로 자작 → 3중 자동관문(정적위험스캔·셀프테스트·라이브로드) + kill-switch/arm/audit. 라이브 증거: int_to_roman 자동생성→2026→MMXXVI(사람코드 0줄).
  2. Context isolation(run_isolated): 동일 태스크 비용 $3.74→$1.42(-62%), 턴당 컨텍스트 ~30k 평평 유지, 작업길이 상한 없음. trace/eval 계측.
  3. 24/7 goal 오케스트레이션 + 옵시디언 그래프 기억(자동 index/graph) + 내장 trace/eval 자동채점.
- 정직한 한계: 채널 폭·MCP 생태계 규모는 성숙 하네스에 못 미침. 강점은 폭이 아니라 자율성의 깊이.

## 실제 레포 검증 파일(문안 주장 근거로 사용 가능)
- server.py, orchestrator.py, agent_factory.py(spawn_agent/build_loop/save_knowledge), safety_gate.py, self_improve.py, routines.py, evaluation.py, tracing.py, knowledge/(옵시디언 위키), templates/(costar·react·expert·archivist·planner·executor·evaluator).
