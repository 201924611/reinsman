"""하위 에이전트 동적 생성기.

중앙 에이전트(오케스트레이터)가 호출하는 `spawn_agent` 툴을 제공한다.
호출되면:
  1. 인용된 프롬프트 템플릿(templates/<template>.md)을 고르고
  2. 중앙 에이전트가 준 값(role / task / context)을 채워
  3. runtime_agents/<id>.md 파일을 생성한 뒤, 그 파일로 에이전트를 구성해 실행하고
  4. 모든 동작이 끝나면 그 md 파일을 삭제한다.

즉, 중앙 에이전트는 사람처럼 "이 일은 이런 전문가에게"라고 판단해
필요한 만큼 작업자를 즉석에서 만들어 쓰고, 끝나면 정리한다.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import shutil
import sys
import uuid

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    tool,
    create_sdk_mcp_server,
)

import config
import tracing
from agent_loader import load_agent_file
from applog import get_logger
from template_engine import render, DEFAULT_TEMPLATE

logger = get_logger()

# 하위에이전트 claude.exe 스폰이 동시성 경합으로 일시 실패할 때 재시도.
# 경합(메인+서브 동시 부팅)은 짧게는 안 풀려, 백오프를 넉넉히 둔다(1·2·3초로는 부족했음).
_SPAWN_MAX_RETRIES = 5
_SPAWN_BACKOFF = [3, 8, 15, 25, 40]  # seconds, attempt별

# 병렬 fan-out 시 동시 스폰 상한(claude.exe 동시부팅 경합 방지). 재시도와 함께 안전 병렬.
_MAX_PARALLEL_SPAWNS = int(os.getenv("MAX_PARALLEL_SPAWNS", "4"))
_PARALLEL_SEM = asyncio.Semaphore(_MAX_PARALLEL_SPAWNS)

# Anthropic API 일시 용량 오류(429/529/500)에 대한 백오프 재시도. 과부하는 실제로
# 기다려야 회복되므로 점증 대기. build_loop 실행가가 이걸로 죽지 않고 통과하게 한다.
_API_MAX_RETRIES = 4
_API_BACKOFF = [5, 15, 30, 60]  # seconds, attempt별

# 스폰 행(hang) 방지 타임아웃: claude.exe가 응답도 에러도 없이 멈추면 영영 안 끝난다(실제로 발생).
# 첫 메시지(cold-start)까지 _BOOT_TIMEOUT, 이후 메시지 사이 무응답은 _IDLE_TIMEOUT을 넘기면
# 끊고(aclose) 재시도한다.
_BOOT_TIMEOUT = 180   # seconds — 첫 메시지까지
_IDLE_TIMEOUT = 420   # seconds — 메시지 간 무응답 한계

# 현재 실행 중인 목적의 task_id (async-task 별로 안전하게 보관).
current_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_task_id", default=None
)
# 현재 오케스트레이터 span_id — 서브에이전트 span의 parent로 쓰인다(추적 그룹핑).
current_orch_span: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_orch_span", default=None
)
# 구조/실험 오버라이드(모델·턴 등) — A/B 비교용. {} 면 config 기본값.
run_overrides: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "run_overrides", default={}
)

# 하위 작업자에게 항상 덧붙이는 공통 지시
_SUBAGENT_SUFFIX = (
    "\n\n너는 더 큰 목적을 위해 호출된 하위 작업자다. 맡은 하위 작업만 처리하고, "
    "마지막에 반드시 결과 요약을 텍스트로 남겨라."
)


def _write_runtime_agent(agent_id: str, role: str, template_name: str,
                         template_meta: dict, filled_body: str):
    """템플릿을 채운 내용으로 runtime_agents/<id>.md 파일을 만든다.
    템플릿 출처(source)를 주석으로 인용해 남긴다."""
    source = template_meta.get("source", "")
    cite = f"<!-- 인용 템플릿: {template_name} | 출처: {source} -->\n\n" if source else ""
    content = (
        f"---\n"
        f"name: {agent_id}\n"
        f"role: {role}\n"
        f"model: {config.SUBAGENT_MODEL}\n"
        f"template: {template_name}\n"
        f"---\n"
        f"{cite}{filled_body}{_SUBAGENT_SUFFIX}\n"
    )
    path = config.RUNTIME_AGENTS_DIR / f"{agent_id}.md"
    path.write_text(content, encoding="utf-8")
    return path


async def run_subagent(role: str, task: str, template: str = DEFAULT_TEMPLATE,
                       context: str = "") -> str:
    """템플릿 기반으로 런타임 md를 생성해 하위 에이전트를 실행하고,
    종료되면 그 md 파일을 삭제한 뒤 최종 텍스트 결과를 반환한다."""
    agent_id = "sub-" + uuid.uuid4().hex[:8]
    template = template or DEFAULT_TEMPLATE
    meta, filled = render(template, role, task, context)
    md_path = _write_runtime_agent(agent_id, role, template, meta, filled)
    logger.info(f"[spawn] {agent_id} role={role} template={template} -> {md_path.name}")

    agent = load_agent_file(md_path)
    ov = run_overrides.get() or {}
    sub_model = ov.get("subagent_model") or (agent.model if agent and agent.model else config.SUBAGENT_MODEL)
    options = ClaudeAgentOptions(
        system_prompt=agent.system_prompt if agent else filled + _SUBAGENT_SUFFIX,
        model=sub_model,
        permission_mode=config.PERMISSION_MODE,
        cwd=str(config.WORKSPACE_DIR),
        max_turns=ov.get("max_turns") or config.MAX_TURNS,
        allowed_tools=(agent.allowed_tools if agent else []),
        # 서브에이전트도 수집 데이터를 영속 저장소에 넣을 수 있게 kb 툴 제공.
        # 노션 토큰이 있으면 notion 도구도 함께 제공(예: 사서가 노션 DB에 직접 적재).
        mcp_servers=_subagent_servers(),
    )

    # ---- 추적: 서브에이전트 span 시작 (parent = 현재 오케스트레이터 span) ----
    tid = current_task_id.get()
    span_id = None
    if tid:
        try:
            span_id = tracing.store.start_span(
                tid, role=role, kind="subagent", model=sub_model,
                template=template, parent=current_orch_span.get(),
            )
        except Exception:  # noqa: BLE001
            span_id = None

    chunks: list[str] = []
    sess: str | None = None
    rmsg: ResultMessage | None = None
    try:
        # Windows에서 claude.exe(번들 246MB)를 동시에 여러 개 띄우면 일부 스폰이
        # 일시적으로 실패한다(파일 경합 → SDK는 'not found'/exit 143으로 표기). 파일은
        # 멀쩡하므로, '아직 한 줄도 못 받은' 스폰 초기 실패에 한해 재시도한다(중복 방지).
        attempt = 0
        while True:
            attempt += 1
            chunks = []
            got_any = False        # 메시지를 하나라도 받았는지(=cold-start 완료)
            agen = query(prompt=task, options=options).__aiter__()
            try:
                while True:
                    # 첫 메시지까지 _BOOT_TIMEOUT, 이후 메시지 간 무응답은 _IDLE_TIMEOUT.
                    # 응답·에러 없이 행(hang)나면 TimeoutError → 끊고 재시도(예전엔 영영 멈춰 서버까지 얼었음).
                    try:
                        message = await asyncio.wait_for(
                            agen.__anext__(),
                            timeout=(_IDLE_TIMEOUT if got_any else _BOOT_TIMEOUT),
                        )
                    except StopAsyncIteration:
                        break
                    got_any = True
                    if isinstance(message, SystemMessage):
                        if getattr(message, "session_id", None):
                            sess = message.session_id
                    elif isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                chunks.append(block.text)
                            elif isinstance(block, ToolUseBlock) and tid and span_id:
                                try:
                                    tracing.store.add_tool(tid, span_id, block.name, block.input)
                                except Exception:  # noqa: BLE001
                                    pass
                    elif isinstance(message, ResultMessage):
                        rmsg = message
                        if message.session_id:
                            sess = message.session_id
                break  # 정상 종료
            except asyncio.TimeoutError:
                try:
                    await agen.aclose()   # 행난 서브프로세스 정리
                except Exception:  # noqa: BLE001
                    pass
                if attempt <= _SPAWN_MAX_RETRIES:
                    back = _SPAWN_BACKOFF[min(attempt - 1, len(_SPAWN_BACKOFF) - 1)]
                    logger.warning(f"[spawn-timeout] {agent_id} {attempt}/{_SPAWN_MAX_RETRIES} "
                                   f"무응답(hang) — {back}s 후 재시도")
                    await asyncio.sleep(back)
                    continue
                raise
            except Exception as e:  # noqa: BLE001
                try:
                    await agen.aclose()
                except Exception:  # noqa: BLE001
                    pass
                msg = str(e); low = msg.lower(); name = type(e).__name__
                # (1) claude.exe 동시 스폰 경합: 스트림 시작 전(=출력 없음)에만 재시도.
                spawn_fail = (not chunks) and (
                    "not found" in low or "exit code 143" in msg
                    or "failed to start" in low
                    or "clinotfound" in name.lower() or "cliconnection" in name.lower()
                )
                # (2) Anthropic API 일시 용량 오류(과부하 529 / 레이트리밋 429 / 500).
                #     CLI가 is_error=True + subtype="success"로 보내 'error result: success'로 위장됨.
                api_transient = (
                    "error result" in low or "overloaded" in low
                    or "rate limit" in low or "rate_limit" in low
                    or " 429" in msg or " 529" in msg or " 500" in msg
                )
                if spawn_fail and attempt <= _SPAWN_MAX_RETRIES:
                    back = _SPAWN_BACKOFF[min(attempt - 1, len(_SPAWN_BACKOFF) - 1)]
                    logger.warning(f"[spawn-retry] {agent_id} {attempt}/{_SPAWN_MAX_RETRIES} "
                                   f"스폰 초기 실패 — {back}s 후 재시도: {msg[:100]}")
                    await asyncio.sleep(back)
                    continue
                if api_transient and attempt <= _API_MAX_RETRIES:
                    back = _API_BACKOFF[min(attempt - 1, len(_API_BACKOFF) - 1)]
                    logger.warning(f"[api-retry] {agent_id} {attempt}/{_API_MAX_RETRIES} "
                                   f"API 일시 오류 — {back}s 후 재시도: {msg[:100]}")
                    await asyncio.sleep(back)
                    continue
                raise
        return "\n".join(chunks).strip() or "(하위 에이전트가 텍스트 결과를 반환하지 않음)"
    finally:
        # 추적: span 종료 (토큰/턴/비용/시간 기록)
        if tid and span_id:
            try:
                tracing.store.end_span(
                    tid, span_id, status="ok", session_id=sess,
                    usage=(rmsg.usage if rmsg else None),
                    cost_usd=(rmsg.total_cost_usd if rmsg else None),
                    num_turns=(rmsg.num_turns if rmsg else None),
                    duration_ms=(rmsg.duration_ms if rmsg else None),
                )
            except Exception:  # noqa: BLE001
                pass
        # 서브 에이전트의 모든 동작이 끝나면(성공/실패 무관) 생성된 md 파일 삭제
        try:
            md_path.unlink(missing_ok=True)
            logger.info(f"[cleanup] {agent_id} 완료 — md 삭제: {md_path.name}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[cleanup] {agent_id} md 삭제 실패: {e}")


# ---- 빌드 루프: 계획 → (실행 → 평가) 반복 ----
# planner / executor / evaluator 세 에이전트가 '메인(이 함수)'을 통해 소통하며
# 최대 rounds회 반복한다. 평가가 합격(passed)하면 조기 종료.


def _parse_verdict(text: str) -> dict:
    """evaluator의 마지막 JSON({passed,score,improvements,structural})을 파싱."""
    m = re.search(r'\{[^{}]*"passed".*\}', text, re.DOTALL)
    if not m:
        return {"passed": False, "score": None, "improvements": [], "structural": False}
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {"passed": False, "score": None, "improvements": [], "structural": False}


def _snap_ignore(_d, names):
    """스냅샷 시 제외할 무거운/불필요 디렉터리."""
    skip = {"node_modules", ".git", ".vite", "dist-ssr", "target", "__pycache__"}
    return [n for n in names if n in skip]


_SHOT_DIR = config.ROOT / "tools" / "screenshot"
_SHOT_NODE = r"C:\Program Files\nodejs\node.exe"


async def _round_screenshot(dist_dir, out_dir, label: str, port: int = 4199):
    """빌드된 정적 산출물(dist_dir)을 잠깐 서빙하고 단일 스크린샷 1장을 남긴다.
    실패해도 빌드 루프를 막지 않도록 호출부에서 try로 감싼다."""
    from pathlib import Path
    dist_dir = Path(dist_dir)
    if not dist_dir.exists() or not (_SHOT_DIR / "shot1.mjs").exists():
        return None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    srv = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
        cwd=str(dist_dir),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.sleep(2)
        proc = await asyncio.create_subprocess_exec(
            _SHOT_NODE, "shot1.mjs", f"http://127.0.0.1:{port}", str(out_dir), label,
            cwd=str(_SHOT_DIR),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=70)
        shot = out_dir / f"{label}.png"
        return shot if shot.exists() else None
    finally:
        try:
            srv.terminate()
            await srv.wait()
        except Exception:  # noqa: BLE001
            pass


async def run_build_loop(task: str, rounds: int = 5, context: str = "",
                         min_rounds: int = 3, snapshot_path: str = "",
                         shot_dist: str = "", replan: bool = True,
                         skeleton: str = "keep", escalate: bool = True,
                         baseline: float = 0.0) -> str:
    """계획→실행→평가 루프. 각 단계는 전용 템플릿 서브에이전트로 수행한다.
    min_rounds: 평가가 합격(passed)해도 이 횟수까지는 강제로 반복 개선한다(조기종료 방지).
    snapshot_path: workspace 기준 상대경로. 주면 라운드별 산출물을 스냅샷해두고,
                   마지막에 '최고 점수' 라운드 결과를 복원한다(끝 라운드가 회귀하면 손해 방지).
    replan: True면 매 라운드 평가 결과를 '계획가'에게 다시 먹여 계획 자체를 갱신한다
            (평가→재계획 루프). False면 계획은 1회 고정, 실행가만 피드백을 받는다(구버전 동작).
    skeleton: 'keep'=현재 골격(레이아웃·네비 패러다임)을 유지하고 그 안에서 품질만 끌어올린다.
              'redesign'=골격 패러다임부터 백지에서 재설계한다(이전과 한눈에 달라 보이게).
              (계획가는 코드/템플릿이 아니라 이 지침을 따른다.)
    escalate: True이고 skeleton='keep'으로 시작했을 때, keep로는 점수가 안 오르면(아래 조건)
              자동으로 skeleton='redesign'으로 전환해 골격을 갈아엎는다(단계적 에스컬레이션, 1회):
                ① 점수 정체(직전 대비 Δ<0.02)가 2회 연속 ② 점수 하락(회귀) ③ baseline 미달
                ④ 평가가가 구조적 한계(structural=true) 판정.
    baseline: 에스컬레이션 ③ 기준 점수(이번 라운드 점수가 이 값 미만이면 골격 변경). 0이면 ③ 비활성."""
    min_rounds = max(1, min(min_rounds, rounds))
    skeleton = (skeleton or "keep").strip().lower()
    _DIR_REDESIGN = (
        "[골격 방침: REDESIGN] 전체 골격(네비게이션·레이아웃 패러다임)부터 백지에서 재검토하라. "
        "유지하는 것은 명시된 것(계산 로직·데이터 필드 등)뿐이다. 대안 패러다임을 최소 2개 비교해 "
        "더 나은 것을 골라라 — 예: 탑바+탭, 단계별 위저드, 풀스크린 포커스, 카드 캔버스, 2-pane. "
        "**이전과 한눈에 달라 보여야 한다.**"
    )
    _DIR_KEEP = (
        "[골격 방침: KEEP] 현재 골격(레이아웃·네비게이션 패러다임)은 이미 검증되어 좋다 — 유지한다. "
        "골격·화면 구성을 갈아엎지 말고 보존하라. 그 안에서 정보 위계·여백/정렬 일관성·빈/로딩/에러 "
        "상태·마이크로카피·접근성·실측 디자인 토큰 적용으로 **완성도만** 끌어올려라."
    )
    skeleton_directive = _DIR_REDESIGN if skeleton == "redesign" else _DIR_KEEP
    _emit("build", f"빌드 루프 시작 — 계획→실행→평가 최대 {rounds}회 "
                   f"(최소 {min_rounds}회 강제 · 재계획={'on' if replan else 'off'} · 골격={skeleton}"
                   f" · 에스컬레이션={'on' if escalate else 'off'}"
                   f"{f', baseline={baseline}' if baseline else ''})")

    # 라운드별 산출물 스냅샷 보관소 (state 하위, gitignore됨)
    loop_id = "loop-" + uuid.uuid4().hex[:6]
    snap_root = config.STATE_DIR / "build_snapshots" / loop_id
    snap_src = (config.WORKSPACE_DIR / snapshot_path) if snapshot_path else None

    # 1) 초기 계획 — 골격 방침(keep/redesign)에 따라 설계
    plan = await run_subagent(
        "계획가",
        f"{skeleton_directive}\n\n다음 목표의 구축 계획을 세워라.\n[목표]\n{task}",
        template="planner", context=context)
    _emit("plan", f"초기 계획 수립 완료 (골격={skeleton})")

    history: list[dict] = []
    feedback = ""
    final_exec = ""
    best = {"round": 0, "score": -1.0, "snap": None, "exec": ""}  # 최고 점수 라운드 추적
    prev_score: float | None = None   # 직전 라운드 점수(정체/회귀 판정용)
    stagnant = 0                       # 점수 정체 연속 횟수
    escalated = False                  # 골격 에스컬레이션 1회만

    for i in range(1, rounds + 1):
        # 2) 실행 — 계획 + 직전 평가 개선점 반영
        exec_task = (
            f"[전체 계획]\n{plan}\n\n[이번 라운드 {i}/{rounds}]\n"
            + (f"직전 평가의 개선점을 최우선으로 반영해 수정·보완하라:\n{feedback}"
               if feedback else "계획의 '이번 라운드 실행 항목'을 구현하라.")
        )
        execution = await run_subagent("실행가", exec_task, template="executor", context=context)
        final_exec = execution
        _emit("execute", f"{i}/{rounds}회차 실행 완료")

        # 라운드별 진행 스크린샷 1장 (shot_dist 지정 시) — 시각적 변화 추적용
        if shot_dist:
            try:
                shots_dir = config.ROOT / "round_shots" / loop_id
                shot = await _round_screenshot(config.WORKSPACE_DIR / shot_dist, shots_dir,
                                               f"round{i}")
                if shot:
                    _emit("shot", f"{i}/{rounds}회차 스크린샷: round_shots/{loop_id}/round{i}.png")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[build_loop] {i}R 스크린샷 실패: {e}")

        # 3) 평가 — 수용 기준 대비 채점 + 개선점
        eval_task = (
            f"[목표]\n{task}\n\n[수용 기준이 포함된 계획]\n{plan}\n\n"
            f"[이번 라운드 실행 결과 보고]\n{execution}"
        )
        verdict_text = await run_subagent("평가가", eval_task, template="evaluator", context=context)
        v = _parse_verdict(verdict_text)
        passed, score = bool(v.get("passed")), v.get("score")
        improvements = v.get("improvements") or []
        history.append({"round": i, "passed": passed, "score": score, "improvements": improvements})
        _emit("evaluate", f"{i}/{rounds}회차 평가: passed={passed}, score={score} (min={min_rounds})")

        # best-round 추적: 이번 라운드 산출물을 스냅샷하고, 최고 점수면 기억한다.
        sc = float(score) if isinstance(score, (int, float)) else -1.0
        if snap_src and snap_src.exists():
            try:
                snap_i = snap_root / f"r{i}"
                shutil.copytree(snap_src, snap_i, ignore=_snap_ignore, dirs_exist_ok=True)
                if sc > best["score"]:
                    best = {"round": i, "score": sc, "snap": snap_i, "exec": execution}
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[build_loop] 스냅샷 실패 r{i}: {e}")
        elif sc > best["score"]:
            best = {"round": i, "score": sc, "snap": None, "exec": execution}

        # ── 골격 에스컬레이션: keep로 시작했는데 점수가 안 오르면 redesign으로 전환(1회) ──
        if escalate and skeleton == "keep" and not escalated and i < rounds:
            structural = bool(v.get("structural"))
            trig = None
            if structural:
                trig = "평가가 구조적 한계 판정(structural=true)"
            elif baseline and sc >= 0 and sc < baseline:
                trig = f"이전 best/baseline 미달 (score {sc:.3f} < {baseline:.3f})"
            elif prev_score is not None and sc < prev_score:
                trig = f"점수 하락(회귀) {prev_score:.3f}→{sc:.3f}"
            elif prev_score is not None and (sc - prev_score) < 0.02:
                stagnant += 1
                if stagnant >= 2:
                    trig = "점수 정체 2회 연속 (Δ<0.02)"
            else:
                stagnant = 0
            if trig:
                escalated = True
                skeleton = "redesign"
                skeleton_directive = _DIR_REDESIGN
                _emit("build", f"⚡ 골격 에스컬레이션 발동 ({i}R): {trig} → skeleton=redesign 전환, 골격 재설계")
                replan_now = (
                    f"{skeleton_directive}\n\n[원 목표]\n{task}\n\n"
                    f"[지금까지 keep(골격 유지) 방침으로는 점수가 천장에 막혔다 — 트리거: {trig}]\n"
                    f"[직전 계획]\n{plan}\n\n[직전 {i}R 실행 결과]\n{execution}\n\n"
                    f"[평가 개선점]\n" + "\n".join(f"- {x}" for x in improvements) + "\n\n"
                    "이제 골격(네비게이션·레이아웃 패러다임)부터 백지에서 다시 설계하라. "
                    "유지할 것은 계산 로직·데이터 필드·안전장치뿐. 대안 패러다임 2개+ 비교해 더 나은 걸 골라 "
                    "이전과 한눈에 달라 보이는 개정 계획을 같은 형식으로 내라."
                )
                plan = await run_subagent("계획가", replan_now, template="planner", context=context)
                _emit("plan", f"{i}R 에스컬레이션 재계획(redesign) 완료 → 다음 {i+1}R 적용")
                prev_score = sc
                feedback = "\n".join(f"- {x}" for x in improvements) or "골격 재설계 결과를 더 끌어올려라."
                continue  # 에스컬레이션 시 일반 break/replan 블록 건너뜀
        prev_score = sc

        # 합격이어도 최소 라운드 전엔 멈추지 않는다(조기종료 방지 → 실제 반복 개선).
        if passed and i >= min_rounds:
            break
        feedback = "\n".join(f"- {x}" for x in improvements)
        if not feedback:
            feedback = (
                "합격 판정이지만 최소 반복 미달이다. 한 단계 더 끌어올려라: "
                "정보 위계, 여백·정렬 일관성, 빈/로딩/에러 상태, 마이크로카피, 접근성, 모바일 반응형."
            )

        # 4) 재계획 — 평가 결과를 '계획가'에게 다시 먹여 계획 자체를 갱신한다.
        #    (이게 없으면 계획은 1회 고정이고 실행가만 패치해 구조가 안 바뀜.)
        #    마지막 라운드 직후엔 다음 실행이 없으므로 재계획하지 않는다.
        if replan and i < rounds:
            replan_task = (
                f"{skeleton_directive}\n\n"
                f"[원 목표]\n{task}\n\n"
                f"[직전 계획]\n{plan}\n\n"
                f"[직전 {i}R 실행 결과 보고]\n{execution}\n\n"
                f"[평가가 판정] passed={passed}, score={score}\n"
                f"[평가가 개선점]\n{feedback}\n\n"
                "위 평가를 토대로 '계획 자체'를 개정하라. 효과 없던 항목은 버리고, 개선점을 반영해 "
                "다음 라운드의 화면 정보설계·실행 항목·수용 기준을 갱신한 **개정 계획**을 같은 형식으로 내라. "
                "골격 방침은 위 지침을 그대로 지켜라."
            )
            plan = await run_subagent("계획가", replan_task, template="planner", context=context)
            _emit("plan", f"{i}R 평가 반영 → 재계획 완료 (다음 {i+1}R 적용)")

    # ---- best-round 채택: 마지막 라운드가 최고점이 아니면 최고점 산출물을 복원 ----
    last_round = history[-1]["round"] if history else 0
    restored_note = ""
    adopted_exec = final_exec
    if best["round"] and best["round"] != last_round:
        if best["snap"] and snap_src:
            try:
                shutil.copytree(best["snap"], snap_src, ignore=_snap_ignore, dirs_exist_ok=True)
                restored_note = (
                    f"\n\n⚠️ best-round 채택: 마지막 {last_round}R가 회귀(낮은 점수)하여 "
                    f"최고점 **{best['round']}R(score {best['score']})** 산출물을 `{snapshot_path}`에 복원함."
                )
                adopted_exec = best["exec"] or final_exec
                _emit("build", f"best-round 복원 — {best['round']}R(score {best['score']}) → {snapshot_path}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[build_loop] best-round 복원 실패: {e}")
        else:
            # 스냅샷이 없으면 파일 복원은 못 하고 보고에만 명시(투명성)
            restored_note = (
                f"\n\n참고: 최고점은 {best['round']}R(score {best['score']})이나 "
                f"snapshot_path 미지정으로 파일 복원은 못 했고 마지막 {last_round}R가 디스크에 남음."
            )

    # 스냅샷 정리
    try:
        if snap_root.exists():
            shutil.rmtree(snap_root, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    best_line = f"최고점 {best['round']}R(score {best['score']})" if best["round"] else "N/A"
    shots_note = (f"\n[라운드별 스크린샷] round_shots/{loop_id}/round1..{len(history)}.png"
                  if shot_dist else "")
    return (
        f"빌드 루프 완료 ({len(history)}회 반복). {best_line}.{restored_note}{shots_note}\n\n"
        f"[채택된 결과]\n{adopted_exec}\n\n"
        f"[평가 이력]\n{json.dumps(history, ensure_ascii=False, indent=2)}"
    )


# ---- 중앙 에이전트에게 노출되는 SDK 내장(in-process) MCP 툴 ----


def _emit(kind: str, message: str, **extra) -> None:
    """진행 이벤트를 현재 task에 기록한다. (지연 import로 순환참조 회피)"""
    tid = current_task_id.get()
    if not tid:
        return
    from task_store import store
    store.append_event(tid, kind, message, **extra)


@tool(
    "spawn_agent",
    "하위 에이전트를 새로 만들어 하위 작업(task)을 위임하고 그 결과를 받는다. "
    "role: 작업자에게 부여할 역할(자유 문자열, 예: '데이터 분석가'). "
    "task: 위임할 구체적 작업. "
    "template: 사용할 프롬프트 템플릿 — costar(범용 전문가) / react(도구로 단계적 추론) / "
    "expert(전문가 페르소나) / default(기본). "
    "context: 작업에 필요한 배경/맥락(선택, 없으면 빈 문자열).",
    {"role": str, "task": str, "template": str, "context": str},
)
async def spawn_agent_tool(args: dict) -> dict:
    role = str(args.get("role", "범용 작업자")).strip() or "범용 작업자"
    task = str(args.get("task", "")).strip()
    template = str(args.get("template", "") or DEFAULT_TEMPLATE).strip()
    context = str(args.get("context", ""))
    if not task:
        return {"content": [{"type": "text", "text": "오류: task가 비어 있습니다."}]}

    _emit("spawn", f"하위 에이전트 생성: role={role}, template={template}",
          role=role, task=task, template=template)

    try:
        result = await run_subagent(role, task, template, context)
    except Exception as e:  # noqa: BLE001
        _emit("error", f"하위 에이전트 실패: {e}")
        return {"content": [{"type": "text", "text": f"하위 에이전트 실행 실패: {e}"}]}

    _emit("result", f"하위 에이전트({role}) 완료", role=role)
    return {"content": [{"type": "text", "text": result}]}


@tool(
    "build_loop",
    "웹/앱/파일 구축 작업을 '계획→실행→평가' 루프로 수행한다. "
    "planner→executor→evaluator 전용 에이전트가 메인을 통해 소통하며 최대 rounds회(기본 5) 반복 개선하고, "
    "평가가 합격하면 조기 종료한다. 단순 단일 작업이 아니라 '만들어내는' 작업에 쓴다. "
    "task: 구축 목표(무엇을 만들지). rounds: 최대 반복(기본 5). min_rounds: 합격해도 강제 반복할 최소 횟수(기본 3, 조기종료 방지). "
    "snapshot_path: workspace 기준 산출물 경로(예: 'myapp/frontend'). 주면 라운드별 스냅샷 후 '최고 점수' 라운드를 복원한다(끝 라운드 회귀 손해 방지). "
    "shot_dist: workspace 기준 '빌드된 정적 폴더' 경로(예: 'myapp/frontend/dist'). 주면 매 라운드 끝에 스크린샷 1장을 round_shots/에 남긴다. "
    "replan: 매 라운드 평가 결과를 계획가에게 다시 먹여 계획을 갱신할지(기본 true=평가→재계획 루프 on; false=계획 1회 고정·실행가만 피드백). "
    "skeleton: 'keep'(기본)=현재 골격 유지하고 그 안에서 완성도만 개선 / 'redesign'=골격 패러다임부터 백지 재설계. "
    "escalate: 기본 true. keep로 시작했는데 점수가 정체/회귀/baseline미달이거나 평가가가 구조적 한계로 판정하면 자동으로 redesign으로 1회 전환. "
    "baseline: 에스컬레이션 기준점(이번 라운드 점수가 이 값 미만이면 골격 변경). 직전 best 점수를 넣으면 '이전보다 개선 안 되면 골격 변경'이 된다. 0이면 비활성. "
    "context: 기술스택·파일위치·유지할 것 등 배경.",
    {"task": str, "rounds": int, "min_rounds": int, "snapshot_path": str, "shot_dist": str,
     "replan": bool, "skeleton": str, "escalate": bool, "baseline": float, "context": str},
)
async def build_loop_tool(args: dict) -> dict:
    task = str(args.get("task", "")).strip()
    if not task:
        return {"content": [{"type": "text", "text": "오류: task가 비어 있습니다."}]}
    try:
        rounds = int(args.get("rounds") or 5)
    except (TypeError, ValueError):
        rounds = 5
    rounds = max(1, min(rounds, 8))  # 폭주 방지 상한
    try:
        min_rounds = int(args.get("min_rounds") or 3)
    except (TypeError, ValueError):
        min_rounds = 3
    context = str(args.get("context", ""))
    snapshot_path = str(args.get("snapshot_path", "")).strip()
    shot_dist = str(args.get("shot_dist", "")).strip()
    replan = args.get("replan", True)
    replan = bool(replan) if not isinstance(replan, str) else replan.strip().lower() not in ("false", "0", "no", "off", "")
    skeleton = str(args.get("skeleton", "keep")).strip().lower() or "keep"
    escalate = args.get("escalate", True)
    escalate = bool(escalate) if not isinstance(escalate, str) else escalate.strip().lower() not in ("false", "0", "no", "off", "")
    try:
        baseline = float(args.get("baseline") or 0.0)
    except (TypeError, ValueError):
        baseline = 0.0
    try:
        result = await run_build_loop(task, rounds, context, min_rounds=min_rounds,
                                      snapshot_path=snapshot_path, shot_dist=shot_dist,
                                      replan=replan, skeleton=skeleton,
                                      escalate=escalate, baseline=baseline)
    except Exception as e:  # noqa: BLE001
        _emit("error", f"빌드 루프 실패: {e}")
        return {"content": [{"type": "text", "text": f"빌드 루프 실행 실패: {e}"}]}
    return {"content": [{"type": "text", "text": result}]}


@tool(
    "spawn_parallel",
    "서로 독립적인 하위 작업 '여러 개를 동시에(병렬)' 실행한다. 계획을 세부로 쪼개 의존성 없는 부분을 한꺼번에 돌릴 때 쓴다. "
    "subtasks: JSON 배열 문자열. 각 원소는 {\"role\":\"역할\",\"task\":\"구체적 작업\",\"template\":\"costar|react|expert|default(선택)\",\"context\":\"배경(선택)\"}. "
    f"동시 실행은 최대 {_MAX_PARALLEL_SPAWNS}개로 자동 제한되고 나머지는 대기·자동 재시도된다. 모든 결과를 모아 한 번에 돌려준다. "
    "순서가 중요하거나 앞 결과를 뒤가 써야 하는 의존 작업엔 쓰지 말 것(그땐 spawn_agent 순차).",
    {"subtasks": str},
)
async def spawn_parallel_tool(args: dict) -> dict:
    raw = str(args.get("subtasks", "")).strip()
    try:
        items = json.loads(raw)
        if not isinstance(items, list) or not items:
            raise ValueError
    except Exception:  # noqa: BLE001
        return {"content": [{"type": "text",
                "text": "오류: subtasks는 비어있지 않은 JSON 배열이어야 합니다. 예: [{\"role\":\"조사가\",\"task\":\"...\"}]"}]}
    items = items[:8]  # 폭주 방지 상한
    _emit("parallel", f"병렬 실행 시작 — {len(items)}개 (동시 최대 {_MAX_PARALLEL_SPAWNS})")

    async def _one(idx: int, it: dict):
        role = (str(it.get("role", "범용 작업자")).strip() or "범용 작업자")
        task = str(it.get("task", "")).strip()
        template = str(it.get("template", "") or DEFAULT_TEMPLATE).strip()
        context = str(it.get("context", ""))
        if not task:
            return (idx, role, "(task가 비어 있어 건너뜀)")
        async with _PARALLEL_SEM:   # 동시 스폰 상한
            _emit("spawn", f"[병렬{idx + 1}] {role}", role=role, task=task, template=template)
            try:
                res = await run_subagent(role, task, template, context)
            except Exception as e:  # noqa: BLE001
                res = f"실패: {e}"
            _emit("result", f"[병렬{idx + 1}] {role} 완료", role=role)
            return (idx, role, res)

    results = await asyncio.gather(*[_one(i, it) for i, it in enumerate(items)
                                     if isinstance(it, dict)])
    blocks = [f"### [{i + 1}] {role}\n{res}" for i, role, res in sorted(results)]
    _emit("parallel", f"병렬 실행 완료 — {len(results)}개")
    return {"content": [{"type": "text", "text": "\n\n".join(blocks)}]}


def build_factory_server():
    """spawn_agent + spawn_parallel + build_loop 툴을 담은 in-process MCP 서버를 만든다."""
    return create_sdk_mcp_server(
        name="agent-factory",
        version="1.0.0",
        tools=[spawn_agent_tool, spawn_parallel_tool, build_loop_tool],
    )


# ---- 지식 저장 툴 (영속 저장소 knowledge/ 에 기록) ----

@tool(
    "save_knowledge",
    "수집/합성한 지식을 영속 위키 저장소(knowledge/)에 저장한다. runtime md와 달리 유지된다. "
    "title: 문서 제목. summary: 한 줄 통찰. content: 핵심 정리(불릿 마크다운). "
    "category: 배치 위치 — Projects/Topics/Decisions/Skills 또는 'Topics/하위' 형태(자유 확장). "
    "tags: 쉼표 구분 태그. related: 쉼표 구분 관련 문서 제목(연결, 2개 이상 권장). "
    "raw_text: 보관할 원본 텍스트(선택).",
    {"title": str, "summary": str, "content": str, "category": str,
     "tags": str, "related": str, "raw_text": str},
)
async def save_knowledge_tool(args: dict) -> dict:
    from knowledge_store import save_knowledge

    title = str(args.get("title", "")).strip()
    if not title:
        return {"content": [{"type": "text", "text": "오류: title이 비어 있습니다."}]}
    tags = [t.strip() for t in str(args.get("tags", "")).split(",") if t.strip()]
    related = [r.strip() for r in str(args.get("related", "")).split(",") if r.strip()]
    res = save_knowledge(
        title,
        str(args.get("summary", "")),
        str(args.get("content", "")),
        category=str(args.get("category", "") or "Topics"),
        tags=tags,
        related=related,
        raw_text=str(args.get("raw_text", "")) or None,
    )
    _emit("kb", f"지식 저장: {res['path']}")
    # 옵션: git 동기화
    try:
        from knowledge_store import git_sync
        git_sync(f"저장 {res['path']}")
    except Exception:  # noqa: BLE001
        pass
    msg = f"저장 완료: {res['path']}"
    if res.get("suggestion"):
        msg += f"\n제안: {res['suggestion']}"
    return {"content": [{"type": "text", "text": msg}]}


def build_knowledge_server():
    """save_knowledge 툴을 담은 in-process MCP 서버를 만든다."""
    return create_sdk_mcp_server(
        name="knowledge",
        version="1.0.0",
        tools=[save_knowledge_tool],
    )


def build_notion_server():
    """노션 공식 MCP 서버(@notionhq/notion-mcp-server)를 stdio로 등록한다.

    in-process 서버(factory/kb/publish)와 달리 외부 프로세스(npx)로 띄우는
    MCP 서버다. NOTION_TOKEN(.env)이 있을 때만 설정 dict를 반환하고, 없으면
    None을 반환해 비활성화한다(노션 도구가 아예 노출되지 않음).

    토큰은 내부 인티그레이션 토큰(ntn_...)이며, 헤더로 주입한다. 에이전트에는
    mcp__notion__* 형태의 도구로 노출된다.
    """
    if not config.NOTION_TOKEN:
        return None
    headers = json.dumps({
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": config.NOTION_VERSION,
    })
    # Windows에서는 npx 런처가 npx.cmd 다(번들 node 환경 PATH 기준).
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    return {
        "type": "stdio",
        "command": npx,
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env": {"OPENAPI_MCP_HEADERS": headers},
    }


def _subagent_servers():
    """서브에이전트에 줄 MCP 서버 묶음: kb(항상) + notion(토큰 있을 때)."""
    servers = {"kb": build_knowledge_server()}
    notion = build_notion_server()
    if notion:
        servers["notion"] = notion
    return servers
