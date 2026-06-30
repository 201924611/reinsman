"""HTTP API 서버 — 24시간 상주하며 중앙 에이전트에 명령(목적)을 전달한다.

엔드포인트:
  POST /goal          {"goal": "..."}      -> 목적 접수, task_id 반환 (백그라운드 실행)
  GET  /tasks                              -> 전체 작업 목록
  GET  /tasks/{id}                         -> 특정 작업 상세 + 진행 이벤트
  POST /tasks/{id}/cancel                  -> 실행 중 작업 취소
  GET  /health                             -> 헬스체크

실행: python server.py   (또는 run.ps1)
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import config
import routines
from agent_loader import list_agents, load_agent
from applog import get_logger
from template_engine import list_templates, load_template
from orchestrator import run_goal
from task_store import store

logger = get_logger()

# 스케줄러 설정 (.env)
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
SCHEDULER_CHECK_SECONDS = int(os.getenv("SCHEDULER_CHECK_SECONDS", "300"))  # due 체크 주기

# 동시 실행 목적 수 제한 (폭주/비용 방지)
_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_TASKS)
# 실행 중인 asyncio.Task 핸들 (취소용)
_running: dict[str, asyncio.Task] = {}


class GoalRequest(BaseModel):
    goal: str
    variant: str = "default"          # 구조/실험 라벨 (A/B 비교용)
    overrides: dict | None = None     # 구조 오버라이드: {model, subagent_model, max_turns}


class RoutineRequest(BaseModel):
    name: str
    prompt: str                       # 주기마다 에이전트에게 넘길 계획(goal)
    interval_hours: float = 24.0
    first_delay_hours: float | None = None  # 첫 실행까지 대기(없으면 interval 후)


async def _scheduler_loop():
    """주기 루틴 디스패처 — 도래한 루틴마다 goal을 제출(=전용 에이전트 1개 생성).
    각 goal은 독립 오케스트레이터로 돌며 스스로 서브에이전트를 만들어 계획을 실행한다."""
    logger.info(f"[scheduler] 시작 — {SCHEDULER_CHECK_SECONDS}s 마다 루틴 점검")
    while True:
        try:
            for r in routines.store.due():
                tid = _submit_goal(r.prompt, variant=f"routine:{r.name}",
                                   source=f"routine {r.id}({r.name})")
                routines.store.mark_ran(r.id, tid)
                logger.info(f"[scheduler] 루틴 발사: {r.name} → task {tid} (다음: {r.next_run})")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[scheduler] 루프 오류: {e}")
        await asyncio.sleep(SCHEDULER_CHECK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 시작 시: 이전에 'running'으로 남아있던(비정상 종료된) 작업을 정리
    for t in store.list():
        if t.status in ("running", "queued"):
            store.update(t.id, status="error", error="서버 재시작으로 중단됨")
    sched_task = None
    if SCHEDULER_ENABLED:
        sched_task = asyncio.create_task(_scheduler_loop())
    yield
    if sched_task:
        sched_task.cancel()


app = FastAPI(title="Agent Core", version="1.0.0", lifespan=lifespan)


async def _execute(task_id: str, goal: str, resume_session: str | None = None,
                   variant: str = "default", overrides: dict | None = None) -> None:
    async with _semaphore:
        try:
            await run_goal(task_id, goal, resume_session=resume_session,
                           variant=variant, overrides=overrides)
        except Exception:  # 상세 에러는 run_goal 내부에서 store에 기록됨
            pass
        finally:
            _running.pop(task_id, None)


def _submit_goal(goal: str, variant: str = "default", overrides: dict | None = None,
                 source: str | None = None) -> str:
    """goal을 새 task로 등록하고 백그라운드 실행을 건다(요청·스케줄러 공용)."""
    task_id = uuid.uuid4().hex[:12]
    store.create(task_id, goal)
    if source:
        store.append_event(task_id, "source", source)
    handle = asyncio.create_task(
        _execute(task_id, goal, variant=variant, overrides=overrides))
    _running[task_id] = handle
    return task_id


@app.post("/goal")
async def submit_goal(req: GoalRequest):
    goal = req.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal이 비어 있습니다.")
    task_id = _submit_goal(goal, variant=req.variant, overrides=req.overrides)
    return {"task_id": task_id, "status": "queued", "goal": goal, "variant": req.variant}


@app.get("/tasks")
async def list_tasks():
    return [
        {
            "id": t.id,
            "goal": t.goal,
            "status": t.status,
            "created_at": t.created_at,
            "updated_at": t.updated_at,
        }
        for t in store.list()
    ]


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    t = store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="없는 task_id")
    return {
        "id": t.id,
        "goal": t.goal,
        "status": t.status,
        "result": t.result,
        "error": t.error,
        "session_id": t.session_id,
        "num_turns": t.num_turns,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "events": t.events,
    }


@app.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    """턴 한도로 미완료(incomplete)되거나 실패/취소된 작업을 이어서 수행한다.

    - 끊긴 작업의 SDK 세션(session_id)을 이어받아 대화 맥락 그대로 계속하고,
    - knowledge/ · workspace/ 에 남은 데이터를 확인하라는 이어하기 프롬프트를 준다.
    이어하기 프롬프트는 RESUME_PROMPT.txt 에서 편집할 수 있다.
    """
    t = store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="없는 task_id")
    if task_id in _running:
        raise HTTPException(status_code=409, detail="이미 실행 중인 작업입니다.")
    if t.status not in ("incomplete", "error", "cancelled", "done"):
        raise HTTPException(status_code=400, detail=f"이어하기 불가 상태: {t.status}")

    cont_prompt = config.get_resume_prompt()
    # 이어하기는 같은 구조(variant)를 유지한다 (trace에서 읽음)
    import tracing
    tr = tracing.store.get(task_id) or {}
    variant = tr.get("variant", "default")
    store.append_event(task_id, "resume", f"이어하기 시작 (session={t.session_id})")
    handle = asyncio.create_task(
        _execute(task_id, cont_prompt, resume_session=t.session_id, variant=variant))
    _running[task_id] = handle
    return {"task_id": task_id, "status": "resuming", "resume_session": t.session_id}


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    handle = _running.get(task_id)
    if not handle:
        raise HTTPException(status_code=404, detail="실행 중인 작업이 아닙니다.")
    handle.cancel()
    store.update(task_id, status="cancelled")
    store.append_event(task_id, "cancel", "사용자 취소")
    return {"task_id": task_id, "status": "cancelled"}


@app.get("/agents")
async def agents():
    """agents/ 폴더의 고정 에이전트 정의(중앙 오케스트레이터 등)."""
    out = []
    for name in list_agents():
        a = load_agent(name)
        if a:
            out.append({
                "name": a.name,
                "description": a.description,
                "model": a.model or "(기본)",
                "allowed_tools": a.allowed_tools or "(전체)",
            })
    return out


@app.get("/templates")
async def templates():
    """인용된 프롬프트 템플릿 목록과 출처."""
    out = []
    for name in list_templates():
        loaded = load_template(name)
        meta = loaded[0] if loaded else {}
        out.append({
            "name": name,
            "description": meta.get("description", ""),
            "source": meta.get("source", ""),
        })
    return out


@app.get("/runtime")
async def runtime_agents():
    """지금 실행 중인(아직 삭제 안 된) 런타임 하위 에이전트 md 목록."""
    return [p.stem for p in config.RUNTIME_AGENTS_DIR.glob("*.md")]


@app.get("/knowledge")
async def knowledge():
    """영속 지식 저장소(knowledge/10_Wiki)에 저장된 문서 목록."""
    from knowledge_store import list_entries
    return {"count": len(list_entries()), "entries": list_entries()}


class FeedbackRequest(BaseModel):
    note: str                       # 사용자 피드백 한 줄 (분류·교훈 검증용)
    approved: bool | None = None    # 교훈 승인 여부(선택) — True/False면 note 앞에 표기


@app.post("/knowledge/feedback")
async def knowledge_feedback(req: FeedbackRequest):
    """사용자 피드백을 Policy.md에 누적한다(경량 휴리스틱: 다음 분류·교훈 판단 시 archivist/오케스트레이터가 참고).
    교훈 검증 게이트의 사용자 확인 채널 — 사실은 자동 저장하되 교훈·규칙은 이 피드백으로 확정/교정한다."""
    note = (req.note or "").strip()
    if not note:
        return {"ok": False, "error": "note가 비어 있습니다."}
    if req.approved is not None:
        note = f"[{'승인' if req.approved else '반려'}] {note}"
    from knowledge_store import record_feedback
    record_feedback(note)
    return {"ok": True, "recorded": note}


@app.get("/traces")
async def list_traces():
    """기록된 trace 목록(구조 라벨 + 토큰/도구 집계)."""
    import tracing
    out = []
    for tid in tracing.store.list_ids():
        tr = tracing.store.get(tid)
        if tr:
            out.append({"task_id": tid, "variant": tr.get("variant"),
                        "goal": (tr.get("goal") or "")[:80],
                        **tracing.TraceStore.totals(tr)})
    return out


@app.get("/trace/{task_id}")
async def get_trace(task_id: str):
    """한 task의 전체 trace(span·도구체인·세션) + 집계."""
    import tracing
    tr = tracing.store.get(task_id)
    if not tr:
        raise HTTPException(status_code=404, detail="trace 없음")
    return {**tr, "totals": tracing.TraceStore.totals(tr)}


@app.get("/trace/{task_id}/view", response_class=HTMLResponse)
async def trace_view(task_id: str):
    import viewer
    return viewer.trace_view_html(task_id)


@app.post("/tasks/{task_id}/evaluate")
async def evaluate_task(task_id: str):
    """task를 자동 채점(LLM judge + 규칙지표)하고 결과를 저장·반환."""
    import evaluation
    return await evaluation.evaluate(task_id)


@app.get("/eval/{task_id}")
async def get_eval(task_id: str):
    import evaluation
    e = evaluation.get_eval(task_id)
    if not e:
        raise HTTPException(status_code=404, detail="평가 없음 (POST /tasks/{id}/evaluate 먼저)")
    return e


@app.get("/compare")
async def compare(ids: str):
    """여러 task를 구조(variant)별로 나란히 비교 (trace 집계 + eval 점수)."""
    import tracing
    import evaluation
    rows = []
    for tid in [x.strip() for x in ids.split(",") if x.strip()]:
        tr = tracing.store.get(tid) or {}
        tot = tracing.TraceStore.totals(tr) if tr else {}
        ev = evaluation.get_eval(tid) or {}
        j = ev.get("judge", {})
        t = store.get(tid)
        rows.append({
            "task_id": tid,
            "variant": tr.get("variant", "default"),
            "status": (t.status if t else "?"),
            "overall": j.get("overall"),
            "completion": j.get("completion"), "quality": j.get("quality"),
            "safety": j.get("safety"), "efficiency": j.get("efficiency"),
            "input_tokens": tot.get("input_tokens"), "output_tokens": tot.get("output_tokens"),
            "tool_calls": tot.get("tool_calls"), "subagents": tot.get("subagents"),
            "num_turns": tot.get("num_turns"), "cost_usd": tot.get("cost_usd"),
        })
    return {"rows": rows}


@app.get("/compare/view", response_class=HTMLResponse)
async def compare_view(ids: str):
    import viewer
    return viewer.compare_view_html(ids)


class ProposeRequest(BaseModel):
    target: str = "orchestrator"      # orchestrator 또는 템플릿 이름
    n: int = 8                        # 분석할 최근 eval 개수


class ABRequest(BaseModel):
    proposal_id: str
    goal: str                         # 현행 vs 후보를 비교할 테스트 목적


@app.post("/self-improve/propose")
async def si_propose(req: ProposeRequest):
    """최근 eval을 분석해 대상 프롬프트 개선안을 만든다. (라이브 파일 안 건드림)"""
    import self_improve
    return await self_improve.propose(target=req.target, n=req.n)


@app.get("/self-improve/proposals")
async def si_list():
    import self_improve
    return self_improve.list_proposals()


@app.get("/self-improve/proposal/{pid}")
async def si_get(pid: str):
    import self_improve
    p = self_improve.get_proposal(pid)
    if not p:
        raise HTTPException(status_code=404, detail="없는 제안")
    return p


@app.post("/self-improve/apply/{pid}")
async def si_apply(pid: str):
    """후보를 라이브 프롬프트에 적용(사람 승인 게이트). 적용 전 자동 백업."""
    import self_improve
    r = self_improve.apply(pid)
    if "error" in r:
        raise HTTPException(status_code=400, detail=r["error"])
    return r


@app.post("/self-improve/revert")
async def si_revert(target: str = "orchestrator"):
    import self_improve
    r = self_improve.revert(target)
    if "error" in r:
        raise HTTPException(status_code=400, detail=r["error"])
    return r


@app.post("/self-improve/ab")
async def si_ab(req: ABRequest):
    """같은 goal을 현행(baseline) vs 후보(candidate) 프롬프트로 동시에 돌린다.
    끝나면 각각 evaluate 후 /compare 로 점수 비교. (후보는 라이브 변경 없이 주입)"""
    import self_improve
    if not self_improve.proposal_text(req.proposal_id):
        raise HTTPException(status_code=404, detail="없는 제안")
    goal = req.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal이 비어 있습니다.")
    base_id = uuid.uuid4().hex[:12]
    cand_id = uuid.uuid4().hex[:12]
    store.create(base_id, goal)
    store.create(cand_id, goal)
    _running[base_id] = asyncio.create_task(
        _execute(base_id, goal, variant="baseline"))
    _running[cand_id] = asyncio.create_task(
        _execute(cand_id, goal, variant=f"candidate:{req.proposal_id}",
                 overrides={"system_prompt_proposal": req.proposal_id}))
    return {"baseline": base_id, "candidate": cand_id,
            "compare_after_done": f"/compare/view?ids={base_id},{cand_id}",
            "note": "둘 다 done 되면 각각 POST /tasks/{id}/evaluate 후 위 compare 링크 확인"}


@app.get("/routines")
async def list_routines():
    """등록된 주기 루틴(24h마다 도는 '하는 일') 목록 + 다음 실행 예정."""
    return {
        "scheduler_enabled": SCHEDULER_ENABLED,
        "check_seconds": SCHEDULER_CHECK_SECONDS,
        "routines": [r.__dict__ for r in routines.store.list()],
    }


@app.post("/routines")
async def add_routine(req: RoutineRequest):
    """주기 루틴 등록. 스케줄러가 interval_hours 마다 prompt를 goal로 제출한다."""
    name = req.name.strip()
    prompt = req.prompt.strip()
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="name과 prompt가 필요합니다.")
    r = routines.store.add(name, prompt, interval_hours=req.interval_hours,
                           first_delay_hours=req.first_delay_hours)
    return r.__dict__


@app.delete("/routines/{rid}")
async def delete_routine(rid: str):
    if not routines.store.remove(rid):
        raise HTTPException(status_code=404, detail="없는 루틴")
    return {"removed": rid}


@app.post("/routines/{rid}/toggle")
async def toggle_routine(rid: str):
    r = routines.store.toggle(rid)
    if not r:
        raise HTTPException(status_code=404, detail="없는 루틴")
    return {"id": rid, "enabled": r.enabled}


@app.post("/routines/{rid}/run")
async def run_routine_now(rid: str):
    """루틴을 지금 즉시 1회 실행(다음 정기 일정도 interval 후로 재설정)."""
    r = routines.store.get(rid)
    if not r:
        raise HTTPException(status_code=404, detail="없는 루틴")
    tid = _submit_goal(r.prompt, variant=f"routine:{r.name}",
                       source=f"routine {r.id}({r.name}) 수동 실행")
    routines.store.mark_ran(r.id, tid)
    return {"routine": rid, "task_id": tid, "next_run": routines.store.get(rid).next_run}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": config.AGENT_MODEL,
        "subagent_model": config.SUBAGENT_MODEL,
        "running": list(_running.keys()),
        "max_concurrent": config.MAX_CONCURRENT_TASKS,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
