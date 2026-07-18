"""Central core agent (the orchestrator).

Behaves like a human: given a single goal, it either uses tools directly or,
when needed, spawns sub-agents via the spawn_agent tool to distribute the work,
then sees the goal through to completion and reports the result.
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

from reinsman import config

from reinsman.observability import tracing

from reinsman.runtime.agent_factory import (
    build_factory_server, build_knowledge_server, build_notion_server,
    build_generated_server,
    current_task_id, current_orch_span, run_overrides,
)
from reinsman.prompts.agent_loader import load_agent
from reinsman.applog import get_logger
from reinsman.storage.task_store import store
from reinsman.tools.publish import build_publish_server

logger = get_logger()

# The central agent definition is loaded from agents/orchestrator.md.
# (You can change its role/model by editing the .md file alone, without touching code.)
_FALLBACK_PROMPT = (
    "You are the always-on 'central core agent'. Take a goal and carry it through "
    "to completion, and when needed, delegate by spawning sub-agents via the spawn_agent tool."
)

# Max length of the knowledge index injected into the system prompt (to avoid bloat).
# If it exceeds this, only the leading portion is included.
_INDEX_MAX_CHARS = 6000


def _knowledge_index_block() -> str:
    """Turn knowledge/20_Meta/Index.md (the auto-updated index) into a block for prompt injection.

    orchestrator.md tells the agent to "read the relevant Skills before starting domain work,"
    but until now there was no bridge that showed the agent *what knowledge exists*. This block
    is that bridge. Since the cwd is workspace/, the relative path 'knowledge/' won't resolve,
    so we also provide the absolute path, letting the agent open entries directly with the Read
    tool. Returns an empty string (no injection) if the index is missing or empty.
    """
    try:
        from reinsman.kb.knowledge_store import INDEX_PATH, KB_DIR
        if not INDEX_PATH.exists():
            return ""
        idx = INDEX_PATH.read_text(encoding="utf-8").strip()
        if not idx:
            return ""
        if len(idx) > _INDEX_MAX_CHARS:
            idx = idx[:_INDEX_MAX_CHARS] + "\n... (index truncated — see Index.md for the full list)"
        return (
            "\n\n[Available knowledge index — read the relevant documents before you start]\n"
            f"Knowledge store absolute path: {KB_DIR}\n"
            "Below is the accumulated knowledge/ index. If there are Skills/Topics/Projects/Decisions entries\n"
            f"relevant to the current goal, open the corresponding `.md` (wikilink path + .md) under `{KB_DIR}` with the Read tool first, apply that know-how, then begin.\n"
            "Don't repeat research that's already been done — reuse the domain knowledge already accumulated.\n\n"
            + idx
        )
    except Exception:  # noqa: BLE001
        return ""


def _make_options(resume_session: str | None = None) -> ClaudeAgentOptions:
    factory = build_factory_server()
    agent = load_agent("orchestrator")
    ov = run_overrides.get() or {}
    # Self-improvement / A-B: a 'candidate prompt' can be injected without touching the live file.
    #   overrides.system_prompt (raw text) or overrides.system_prompt_proposal (a self_improve proposal id).
    override_prompt = ov.get("system_prompt")
    if not override_prompt and ov.get("system_prompt_proposal"):
        try:
            from reinsman.runtime import self_improve

            override_prompt = self_improve.proposal_text(ov["system_prompt_proposal"])
        except Exception:  # noqa: BLE001
            override_prompt = None
    system_prompt = override_prompt or (
        agent.system_prompt if agent and agent.system_prompt else _FALLBACK_PROMPT)
    # The payout account lives only in .env (PAYOUT_ACCOUNT) and is injected into the prompt
    # only at runtime. (No secrets are left in md/git. The agent uses it for checklist guidance only.)
    if config.PAYOUT_ACCOUNT:
        system_prompt += (
            f"\n\n[Payout receiving account (operator)] {config.PAYOUT_ACCOUNT} — "
            "this is the account where final revenue will be deposited. You do not access or "
            "transfer to this account. In the 'one-time human setup checklist', only advise "
            "'register this account as the platform payout account'."
        )
    # Inject the available-knowledge index at runtime — leave orchestrator.md (general-purpose)
    # untouched and lay out a list so the agent can discover and reuse domain knowledge (Skills/Topics, etc.).
    system_prompt += _knowledge_index_block()
    model = ov.get("model") or (agent.model if agent and agent.model else config.AGENT_MODEL)
    # If the definition has allowed_tools, restrict to those, but always guarantee spawn_agent.
    # (An empty list means all tools are available — both direct work and spawning sub-agents.)
    allowed = list(agent.allowed_tools) if agent and agent.allowed_tools else []
    if allowed and "mcp__factory__spawn_agent" not in allowed:
        allowed.append("mcp__factory__spawn_agent")
    # In-process servers (always) + the external Notion MCP (only when NOTION_TOKEN is set).
    servers = {
        "factory": factory,
        "kb": build_knowledge_server(),
        "publish": build_publish_server(),
    }
    notion = build_notion_server()
    if notion:
        servers["notion"] = notion
    generated = build_generated_server()   # self-built tools (if any) — orchestrator reuses them too
    if generated:
        servers["generated"] = generated
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        permission_mode=config.PERMISSION_MODE,
        cwd=str(config.WORKSPACE_DIR),
        max_turns=ov.get("max_turns") or config.MAX_TURNS,
        mcp_servers=servers,
        allowed_tools=allowed,
        # If resume_session is set, continue with that session's conversation context intact.
        # (Pick up work interrupted by the turn limit from where it left off.) Otherwise, a new session.
        resume=resume_session,
    )


async def run_goal(task_id: str, goal: str, resume_session: str | None = None,
                   variant: str = "default", overrides: dict | None = None) -> str:
    """Carry a single goal through to completion, recording progress in task_store.

    If the goal isn't finished within the turn limit (MAX_TURNS), it automatically resumes
    the same SDK session (conversation context) and continues. This repeats up to
    MAX_AUTO_RESUMES times (to prevent infinite loops / runaway cost); if it still isn't
    finished, the task is left 'incomplete' and manual /resume is suggested.

    If resume_session is given, it picks up that session (otherwise a new session).
    variant/overrides let you define a 'different configuration' for A/B comparison (model, turns, etc.).
    """
    # Pinning the current goal's id into this async-task context means that when the
    # spawn_agent tool is called, events are automatically recorded against the right task.
    current_task_id.set(task_id)
    run_overrides.set(overrides or {})

    # Start tracing (create this task's trace)
    try:
        tracing.store.start_trace(task_id, goal, variant)
    except Exception:  # noqa: BLE001
        pass

    cur_prompt = goal               # prompt for this round (initially = goal, later = resume instruction)
    cur_session = resume_session    # session to resume this round (none = new session)
    attempt = 0                     # 0 = initial run, 1+ = auto-resume round
    result = "(no result text)"

    while True:
        kind = (f"auto-resume round {attempt}" if attempt
                else ("resume" if cur_session else "orchestration"))
        logger.info(f"[{task_id}] === {kind} started === goal={cur_prompt} resume={cur_session}")
        store.update(task_id, status="running")
        store.append_event(task_id, "start", f"{kind} received: {cur_prompt}")

        options = _make_options(cur_session)
        final_chunks: list[str] = []
        result_msg: ResultMessage | None = None  # last result message (session ID, turn count, stop reason)
        # Preserve the session_id seen anywhere in the loop (especially the init SystemMessage).
        # → Even if query() aborts with an 'exception' and there's no result_msg, we can still resume the same session.
        seen_session_id: str | None = cur_session
        exc_max_turns = False  # set when the SDK throws the turn limit as an exception

        # Tracing: start this round's orchestrator span (becomes the parent of sub-agent spans)
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
            # There's a path where the SDK throws the turn limit as an 'exception' rather than a
            # 'ResultMessage'. This isn't a real failure either — it's just 'not finished' — so
            # we handle it as an auto-resume.
            if "maximum number of turns" in msg or "max_turns" in msg:
                exc_max_turns = True
                store.append_event(task_id, "think", f"Turn limit exception detected — handling as resume: {msg[:140]}")
            else:
                if span_id:
                    try:
                        tracing.store.end_span(task_id, span_id, status="error",
                                               session_id=seen_session_id)
                    except Exception:  # noqa: BLE001
                        pass
                store.update(task_id, status="error", error=msg)
                store.append_event(task_id, "error", f"Orchestrator failed: {e}")
                raise

        result = "\n\n".join(final_chunks).strip() or "(no result text)"

        # Always preserve the session ID — it's used for auto/manual resume.
        session_id = seen_session_id
        num_turns = result_msg.num_turns if result_msg else None

        # Tracing: end this round's orchestrator span (record tokens/turns/cost/time)
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

        # Determine whether we were cut off by the turn limit: distinguish real completion (done)
        # from unfinished (incomplete). (Covers both the ResultMessage path and the exception path.)
        hit_max_turns = exc_max_turns or bool(
            result_msg
            and (
                "max_turns" in (result_msg.subtype or "")
                or (num_turns is not None and num_turns >= config.MAX_TURNS)
            )
        )

        if hit_max_turns and attempt < config.MAX_AUTO_RESUMES:
            # Goal not achieved (turns exhausted) → resume the same session and automatically continue.
            attempt += 1
            store.update(task_id, status="running",
                         session_id=session_id, num_turns=num_turns)
            store.append_event(
                task_id, "auto_resume",
                f"Turn limit ({config.MAX_TURNS}) reached — auto-resume "
                f"round {attempt}/{config.MAX_AUTO_RESUMES} (session={session_id})",
            )
            cur_session = session_id              # resume the interrupted session
            cur_prompt = config.get_resume_prompt()  # resume instruction (RESUME_PROMPT.txt)
            continue                              # back to the top of the loop → run again

        if hit_max_turns:
            # Exhausted all auto-resumes and still not done → incomplete (manual /resume available)
            store.update(task_id, status="incomplete", result=result,
                         session_id=session_id, num_turns=num_turns)
            store.append_event(
                task_id, "incomplete",
                f"All {config.MAX_AUTO_RESUMES} auto-resumes exhausted — still incomplete. "
                f"Resume manually with POST /tasks/{task_id}/resume. (session={session_id})",
            )
        else:
            store.update(task_id, status="done", result=result,
                         session_id=session_id, num_turns=num_turns)
            store.append_event(task_id, "done", "goal completed")
        return result
