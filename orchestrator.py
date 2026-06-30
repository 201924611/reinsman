"""중앙 핵심 에이전트(오케스트레이터).

'사람'처럼 행동한다: 하나의 목적(goal)을 받으면, 직접 도구를 쓰거나
필요에 따라 spawn_agent 툴로 하위 에이전트를 만들어 일을 분배하고,
끝까지 목적을 완수한 뒤 결과를 보고한다.
"""
from __future__ import annotations

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
)

import config
import tracing
from agent_factory import (
    build_factory_server, build_knowledge_server, build_notion_server,
    current_task_id, current_orch_span, run_overrides,
)
from agent_loader import load_agent
from applog import get_logger
from task_store import store
from tools.publish import build_publish_server

logger = get_logger()

# 중앙 에이전트 정의는 agents/orchestrator.md 에서 읽어온다.
# (코드를 고치지 않고 .md 파일만 수정해 역할/모델을 바꿀 수 있다)
_FALLBACK_PROMPT = (
    "너는 24시간 상주하는 '중앙 핵심 에이전트'다. 목적을 받아 끝까지 수행하고, "
    "필요하면 spawn_agent 툴로 하위 에이전트를 만들어 위임한다."
)

# 시스템 프롬프트에 주입할 지식 인덱스 최대 길이(비대화 방지). 넘으면 앞부분만 싣는다.
_INDEX_MAX_CHARS = 6000


def _knowledge_index_block() -> str:
    """knowledge/20_Meta/Index.md(자동 갱신 인덱스)를 프롬프트 주입용 블록으로 만든다.

    orchestrator.md는 "도메인 작업 전 Skills를 먼저 읽어라"라고 시키지만, 그동안
    *어떤 지식이 있는지* 목록을 에이전트에게 보여주는 다리가 없었다. 이 블록이 그 다리다.
    cwd가 workspace/ 라 상대경로 'knowledge/'와 어긋나므로, 절대경로를 함께 안내해
    Read 도구로 바로 펼쳐볼 수 있게 한다. 인덱스가 없거나 비면 빈 문자열(주입 안 함).
    """
    try:
        from knowledge_store import INDEX_PATH, KB_DIR
        if not INDEX_PATH.exists():
            return ""
        idx = INDEX_PATH.read_text(encoding="utf-8").strip()
        if not idx:
            return ""
        if len(idx) > _INDEX_MAX_CHARS:
            idx = idx[:_INDEX_MAX_CHARS] + "\n... (인덱스 일부 생략 — 전체는 Index.md 참조)"
        return (
            "\n\n[보유 지식 인덱스 — 일을 시작하기 전에 관련 문서를 먼저 읽어라]\n"
            f"지식 저장소 절대경로: {KB_DIR}\n"
            "아래는 그동안 누적된 knowledge/ 인덱스다. 지금 목적과 관련된 Skills/Topics/Projects/Decisions 항목이\n"
            f"있으면, Read 도구로 `{KB_DIR}` 아래 해당 `.md`(위키링크 경로 + .md)를 먼저 펼쳐 노하우를 적용한 뒤 시작해라.\n"
            "같은 조사를 반복하지 말고 이미 쌓인 도메인 지식을 재사용해라.\n\n"
            + idx
        )
    except Exception:  # noqa: BLE001
        return ""


def _make_options(resume_session: str | None = None) -> ClaudeAgentOptions:
    factory = build_factory_server()
    agent = load_agent("orchestrator")
    ov = run_overrides.get() or {}
    # 자기개선/A·B: 라이브 파일을 바꾸지 않고 '후보 프롬프트'를 주입할 수 있다.
    #   overrides.system_prompt(원문) 또는 overrides.system_prompt_proposal(self_improve 제안 id).
    override_prompt = ov.get("system_prompt")
    if not override_prompt and ov.get("system_prompt_proposal"):
        try:
            import self_improve
            override_prompt = self_improve.proposal_text(ov["system_prompt_proposal"])
        except Exception:  # noqa: BLE001
            override_prompt = None
    system_prompt = override_prompt or (
        agent.system_prompt if agent and agent.system_prompt else _FALLBACK_PROMPT)
    # 정산 계좌는 .env(PAYOUT_ACCOUNT)에만 두고 런타임에만 프롬프트로 주입한다.
    # (md/git에는 비밀을 남기지 않음. 에이전트는 체크리스트 안내용으로만 사용.)
    if config.PAYOUT_ACCOUNT:
        system_prompt += (
            f"\n\n[정산 수취 계좌(운영자)] {config.PAYOUT_ACCOUNT} — "
            "최종 수익이 입금될 계좌다. 너는 이 계좌에 접근/이체하지 않는다. "
            "'사람 1회 설정 체크리스트'에서 '이 계좌를 플랫폼 정산계좌로 등록'으로만 안내한다."
        )
    # 보유 지식 인덱스를 런타임 주입 — orchestrator.md(범용)는 그대로 두고,
    # 도메인 지식(Skills/Topics 등)을 에이전트가 발견·재사용할 수 있게 목록을 깔아준다.
    system_prompt += _knowledge_index_block()
    model = ov.get("model") or (agent.model if agent and agent.model else config.AGENT_MODEL)
    # 정의에 allowed_tools가 있으면 그것으로 제한하되, spawn_agent는 항상 보장.
    # (빈 리스트면 전체 도구 사용 — 직접 작업 + 하위 에이전트 생성 둘 다 가능)
    allowed = list(agent.allowed_tools) if agent and agent.allowed_tools else []
    if allowed and "mcp__factory__spawn_agent" not in allowed:
        allowed.append("mcp__factory__spawn_agent")
    # in-process 서버(항상) + 노션 외부 MCP(NOTION_TOKEN 있을 때만).
    servers = {
        "factory": factory,
        "kb": build_knowledge_server(),
        "publish": build_publish_server(),
    }
    notion = build_notion_server()
    if notion:
        servers["notion"] = notion
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        permission_mode=config.PERMISSION_MODE,
        cwd=str(config.WORKSPACE_DIR),
        max_turns=ov.get("max_turns") or config.MAX_TURNS,
        mcp_servers=servers,
        allowed_tools=allowed,
        # resume_session이 있으면 그 세션의 대화 맥락을 그대로 이어서 진행한다.
        # (턴 한도로 중단됐던 작업을 끊긴 지점부터 계속) — 없으면 새 세션.
        resume=resume_session,
    )


async def run_goal(task_id: str, goal: str, resume_session: str | None = None,
                   variant: str = "default", overrides: dict | None = None) -> str:
    """하나의 목적을 끝까지 수행한다. task_store에 진행상황을 기록한다.

    턴 한도(MAX_TURNS)로 목표를 못 끝내면, 같은 SDK 세션(대화 맥락)을 이어받아
    자동으로 다시 이어서 수행한다. 최대 MAX_AUTO_RESUMES 회까지 반복하며
    (무한 루프/비용 폭주 방지), 그래도 못 끝내면 'incomplete'로 두고 수동 /resume 을 안내한다.

    resume_session 이 주어지면 그 세션을 이어받아 시작한다(없으면 새 세션).
    variant/overrides 로 '다른 구조'를 정의해 A/B 비교할 수 있다(모델·턴 등).
    """
    # 이 async-task 컨텍스트에 현재 목적의 id를 박아두면,
    # spawn_agent 툴이 호출될 때 자동으로 올바른 task에 이벤트를 기록한다.
    current_task_id.set(task_id)
    run_overrides.set(overrides or {})

    # 추적 시작 (이 task의 trace 생성)
    try:
        tracing.store.start_trace(task_id, goal, variant)
    except Exception:  # noqa: BLE001
        pass

    cur_prompt = goal               # 이번 회차에 줄 프롬프트(최초=goal, 이후=이어하기 지시)
    cur_session = resume_session    # 이번 회차에 이어받을 세션(없으면 새 세션)
    attempt = 0                     # 0=최초 실행, 1+=자동 이어하기 회차
    result = "(결과 텍스트 없음)"

    while True:
        kind = (f"자동 이어하기 {attempt}회차" if attempt
                else ("이어하기" if cur_session else "오케스트레이션"))
        logger.info(f"[{task_id}] === {kind} 시작 === goal={cur_prompt} resume={cur_session}")
        store.update(task_id, status="running")
        store.append_event(task_id, "start", f"{kind} 수신: {cur_prompt}")

        options = _make_options(cur_session)
        final_chunks: list[str] = []
        result_msg: ResultMessage | None = None  # 마지막 결과 메시지(세션 ID·턴 수·종료 사유)
        # 루프 중 어디서든(특히 init SystemMessage) 본 session_id를 보존한다.
        # → query()가 '예외'로 중단돼 result_msg가 없어도 같은 세션을 이어받을 수 있다.
        seen_session_id: str | None = cur_session
        exc_max_turns = False  # SDK가 턴 한도를 예외로 던진 경우 표시

        # 추적: 이번 회차의 오케스트레이터 span 시작 (서브에이전트 span의 parent가 됨)
        span_id = None
        try:
            ov = run_overrides.get() or {}
            span_id = tracing.store.start_span(
                task_id, role="orchestrator", kind="orchestrator",
                model=ov.get("model") or config.AGENT_MODEL,
            )
            current_orch_span.set(span_id)
        except Exception:  # noqa: BLE001
            span_id = None

        try:
            async for message in query(prompt=cur_prompt, options=options):
                if isinstance(message, SystemMessage):
                    if getattr(message, "session_id", None):
                        seen_session_id = message.session_id
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text = block.text.strip()
                            if text:
                                final_chunks.append(text)
                                store.append_event(task_id, "think", text[:500])
                        elif isinstance(block, ToolUseBlock) and span_id:
                            try:
                                tracing.store.add_tool(task_id, span_id, block.name, block.input)
                            except Exception:  # noqa: BLE001
                                pass
                elif isinstance(message, ResultMessage):
                    result_msg = message
                    if message.session_id:
                        seen_session_id = message.session_id
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            # SDK가 턴 한도를 'ResultMessage'가 아니라 '예외'로 던지는 경로가 있다.
            # 이 경우에도 진짜 실패가 아니라 '미달성'이므로 자동 이어하기로 처리한다.
            if "maximum number of turns" in msg or "max_turns" in msg:
                exc_max_turns = True
                store.append_event(task_id, "think", f"턴 한도 예외 감지 — 이어하기로 처리: {msg[:140]}")
            else:
                if span_id:
                    try:
                        tracing.store.end_span(task_id, span_id, status="error",
                                               session_id=seen_session_id)
                    except Exception:  # noqa: BLE001
                        pass
                store.update(task_id, status="error", error=msg)
                store.append_event(task_id, "error", f"오케스트레이터 실패: {e}")
                raise

        result = "\n\n".join(final_chunks).strip() or "(결과 텍스트 없음)"

        # 세션 ID는 항상 보존한다 — 자동/수동 이어하기(resume)에 쓰인다.
        session_id = seen_session_id
        num_turns = result_msg.num_turns if result_msg else None

        # 추적: 이번 회차 오케스트레이터 span 종료 (토큰/턴/비용/시간 기록)
        if span_id:
            try:
                tracing.store.end_span(
                    task_id, span_id, status="ok", session_id=session_id,
                    usage=(result_msg.usage if result_msg else None),
                    cost_usd=(result_msg.total_cost_usd if result_msg else None),
                    num_turns=num_turns,
                    duration_ms=(result_msg.duration_ms if result_msg else None),
                )
            except Exception:  # noqa: BLE001
                pass

        # 턴 한도로 잘렸는지 판별: 진짜 완수(done)와 미완(incomplete)을 구분한다.
        # (ResultMessage 경로 + 예외 경로 둘 다 커버)
        hit_max_turns = exc_max_turns or bool(
            result_msg
            and (
                "max_turns" in (result_msg.subtype or "")
                or (num_turns is not None and num_turns >= config.MAX_TURNS)
            )
        )

        if hit_max_turns and attempt < config.MAX_AUTO_RESUMES:
            # 목표 미달성(턴 소진) → 같은 세션을 이어받아 자동으로 다시 이어서 수행한다.
            attempt += 1
            store.update(task_id, status="running",
                         session_id=session_id, num_turns=num_turns)
            store.append_event(
                task_id, "auto_resume",
                f"턴 한도({config.MAX_TURNS}) 도달 — 자동 이어하기 "
                f"{attempt}/{config.MAX_AUTO_RESUMES}회차 진행 (session={session_id})",
            )
            cur_session = session_id              # 끊긴 세션 이어받기
            cur_prompt = config.get_resume_prompt()  # 이어하기 지시(RESUME_PROMPT.txt)
            continue                              # 루프 처음으로 → 다시 수행

        if hit_max_turns:
            # 자동 이어하기 한도까지 다 썼는데도 미완료 → incomplete (수동 /resume 가능)
            store.update(task_id, status="incomplete", result=result,
                         session_id=session_id, num_turns=num_turns)
            store.append_event(
                task_id, "incomplete",
                f"자동 이어하기 {config.MAX_AUTO_RESUMES}회 모두 소진 — 여전히 미완료. "
                f"POST /tasks/{task_id}/resume 로 수동 이어하기 가능. (session={session_id})",
            )
        else:
            store.update(task_id, status="done", result=result,
                         session_id=session_id, num_turns=num_turns)
            store.append_event(task_id, "done", "목적 완수")
        return result
