"""Dynamic sub-agent factory.

Provides the `spawn_agent` tool that the central agent (orchestrator) calls.
When invoked, it:
  1. Selects a cited prompt template (templates/<template>.md),
  2. Fills in the values the central agent supplied (role / task / context),
  3. Writes a runtime_agents/<id>.md file, then configures and runs an agent from it,
  4. Deletes that md file once everything is done.

In other words, the central agent decides, like a human would, "this job goes to
this kind of specialist," spins up as many workers as it needs on the fly, and
cleans them up when they're done.
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

from agent_core import config
from agent_core.runtime import self_tooling

from agent_core.observability import tracing

from agent_core.prompts.agent_loader import load_agent_file
from agent_core.applog import get_logger
from agent_core.prompts.template_engine import render, DEFAULT_TEMPLATE

logger = get_logger()

# Retry when a sub-agent claude.exe spawn transiently fails due to concurrency contention.
# Contention (main + subs booting at once) doesn't clear quickly, so keep generous backoff (1/2/3s wasn't enough).
_SPAWN_MAX_RETRIES = 5
_SPAWN_BACKOFF = [3, 8, 15, 25, 40]  # seconds, per attempt

# Cap on concurrent spawns during parallel fan-out (avoids claude.exe boot contention). Safe parallelism together with retries.
_MAX_PARALLEL_SPAWNS = int(os.getenv("MAX_PARALLEL_SPAWNS", "4"))
_PARALLEL_SEM = asyncio.Semaphore(_MAX_PARALLEL_SPAWNS)

# Backoff retry for transient Anthropic API capacity errors (429/529/500). Overload
# genuinely needs time to recover, so wait with increasing delays. This keeps the build_loop
# executor from dying on it and lets it get through.
_API_MAX_RETRIES = 4
_API_BACKOFF = [5, 15, 30, 60]  # seconds, per attempt

# Anti-hang timeouts for spawns: if claude.exe stalls with neither a response nor an error, it never finishes (this happens in practice).
# Allow _BOOT_TIMEOUT until the first message (cold start); after that, if the gap between messages
# exceeds _IDLE_TIMEOUT, close (aclose) and retry.
_BOOT_TIMEOUT = 180   # seconds — until the first message
_IDLE_TIMEOUT = 420   # seconds — max silence between messages

# task_id of the goal currently running (kept safely per async task).
current_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_task_id", default=None
)
# Current orchestrator span_id — used as the parent of sub-agent spans (trace grouping).
current_orch_span: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_orch_span", default=None
)
# Structural/experiment overrides (model, turns, etc.) — for A/B comparison. {} means config defaults.
run_overrides: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "run_overrides", default={}
)

# Common instruction always appended to every sub-worker
_SUBAGENT_SUFFIX = (
    "\n\nYou are a sub-worker invoked in service of a larger goal. Handle only the "
    "sub-task assigned to you, and always leave a text summary of your result at the end."
)


def _write_runtime_agent(agent_id: str, role: str, template_name: str,
                         template_meta: dict, filled_body: str):
    """Write a runtime_agents/<id>.md file from the filled-in template.
    Cites the template source in a comment."""
    source = template_meta.get("source", "")
    cite = f"<!-- template cited: {template_name} | source: {source} -->\n\n" if source else ""
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
                       context: str = "", model: str | None = None) -> str:
    """Generate a runtime md from a template, run the sub-agent, then delete
    that md file on completion and return the final text result.

    model: model to use for this sub-agent only (e.g. 'claude-haiku-4-5'). When
    given it takes precedence (offload cheap collection/grunt work to a smaller model)."""
    agent_id = "sub-" + uuid.uuid4().hex[:8]
    template = template or DEFAULT_TEMPLATE
    meta, filled = render(template, role, task, context)
    md_path = _write_runtime_agent(agent_id, role, template, meta, filled)
    logger.info(f"[spawn] {agent_id} role={role} template={template} -> {md_path.name}")

    agent = load_agent_file(md_path)
    ov = run_overrides.get() or {}
    sub_model = model or ov.get("subagent_model") or (agent.model if agent and agent.model else config.SUBAGENT_MODEL)
    options = ClaudeAgentOptions(
        system_prompt=agent.system_prompt if agent else filled + _SUBAGENT_SUFFIX,
        model=sub_model,
        permission_mode=config.PERMISSION_MODE,
        cwd=str(config.WORKSPACE_DIR),
        max_turns=ov.get("max_turns") or config.MAX_TURNS,
        allowed_tools=(agent.allowed_tools if agent else []),
        # Give sub-agents the kb tool too, so they can persist collected data to the store.
        # If a Notion token is present, also expose the notion tools (e.g. an archivist loading directly into a Notion DB).
        mcp_servers=_subagent_servers(),
    )

    # ---- Tracing: start the sub-agent span (parent = current orchestrator span) ----
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
        # On Windows, launching several claude.exe instances (bundled, 246MB) at once causes
        # some spawns to fail transiently (file contention → the SDK reports 'not found'/exit 143).
        # The files are fine, so retry only early spawn failures where 'not a single line was received yet' (avoids duplication).
        attempt = 0
        while True:
            attempt += 1
            chunks = []
            got_any = False        # whether any message was received (= cold start complete)
            agen = query(prompt=task, options=options).__aiter__()
            try:
                while True:
                    # _BOOT_TIMEOUT until the first message; after that, _IDLE_TIMEOUT for silence between messages.
                    # If it hangs with no response or error → TimeoutError → close and retry (previously it stalled forever and even froze the server).
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
                break  # normal completion
            except asyncio.TimeoutError:
                try:
                    await agen.aclose()   # clean up the hung subprocess
                except Exception:  # noqa: BLE001
                    pass
                if attempt <= _SPAWN_MAX_RETRIES:
                    back = _SPAWN_BACKOFF[min(attempt - 1, len(_SPAWN_BACKOFF) - 1)]
                    logger.warning(f"[spawn-timeout] {agent_id} {attempt}/{_SPAWN_MAX_RETRIES} "
                                   f"no response (hang) — retrying in {back}s")
                    await asyncio.sleep(back)
                    continue
                raise
            except Exception as e:  # noqa: BLE001
                try:
                    await agen.aclose()
                except Exception:  # noqa: BLE001
                    pass
                msg = str(e); low = msg.lower(); name = type(e).__name__
                # (1) claude.exe concurrent-spawn contention: retry only before the stream starts (= no output).
                spawn_fail = (not chunks) and (
                    "not found" in low or "exit code 143" in msg
                    or "failed to start" in low
                    or "clinotfound" in name.lower() or "cliconnection" in name.lower()
                )
                # (2) Transient Anthropic API capacity errors (overload 529 / rate limit 429 / 500).
                #     The CLI sends is_error=True + subtype="success", so it masquerades as 'error result: success'.
                api_transient = (
                    "error result" in low or "overloaded" in low
                    or "rate limit" in low or "rate_limit" in low
                    or " 429" in msg or " 529" in msg or " 500" in msg
                )
                if spawn_fail and attempt <= _SPAWN_MAX_RETRIES:
                    back = _SPAWN_BACKOFF[min(attempt - 1, len(_SPAWN_BACKOFF) - 1)]
                    logger.warning(f"[spawn-retry] {agent_id} {attempt}/{_SPAWN_MAX_RETRIES} "
                                   f"early spawn failure — retrying in {back}s: {msg[:100]}")
                    await asyncio.sleep(back)
                    continue
                if api_transient and attempt <= _API_MAX_RETRIES:
                    back = _API_BACKOFF[min(attempt - 1, len(_API_BACKOFF) - 1)]
                    logger.warning(f"[api-retry] {agent_id} {attempt}/{_API_MAX_RETRIES} "
                                   f"transient API error — retrying in {back}s: {msg[:100]}")
                    await asyncio.sleep(back)
                    continue
                raise
        return "\n".join(chunks).strip() or "(sub-agent returned no text result)"
    finally:
        # Tracing: end the span (records tokens/turns/cost/time)
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
        # Once all sub-agent activity is done (whether success or failure), delete the generated md file
        try:
            md_path.unlink(missing_ok=True)
            logger.info(f"[cleanup] {agent_id} done — md deleted: {md_path.name}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[cleanup] {agent_id} failed to delete md: {e}")


# ---- Build loop: plan → (execute → evaluate) repeat ----
# The planner / executor / evaluator agents communicate through 'main (this function)'
# and iterate up to `rounds` times. Terminates early once the evaluator passes.


def _parse_verdict(text: str) -> dict:
    """Parse the evaluator's final JSON ({passed,score,improvements,structural})."""
    m = re.search(r'\{[^{}]*"passed".*\}', text, re.DOTALL)
    if not m:
        return {"passed": False, "score": None, "improvements": [], "structural": False}
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {"passed": False, "score": None, "improvements": [], "structural": False}


def _snap_ignore(_d, names):
    """Heavy/unnecessary directories to exclude when snapshotting."""
    skip = {"node_modules", ".git", ".vite", "dist-ssr", "target", "__pycache__"}
    return [n for n in names if n in skip]


_SHOT_DIR = config.ROOT / "tools" / "screenshot"
_SHOT_NODE = r"C:\Program Files\nodejs\node.exe"


async def _round_screenshot(dist_dir, out_dir, label: str, port: int = 4199):
    """Briefly serve the built static output (dist_dir) and capture a single screenshot.
    Wrapped in try at the call site so a failure never blocks the build loop."""
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
    """Plan→execute→evaluate loop. Each stage runs as a sub-agent from a dedicated template.
    min_rounds: even after the evaluator passes, keep iterating up to this count (prevents premature termination).
    snapshot_path: workspace-relative path. If given, snapshot the output each round and,
                   at the end, restore the 'highest-scoring' round's result (guards against a regressing final round).
    replan: if True, feed each round's evaluation back to the 'planner' to revise the plan itself
            (evaluate→replan loop). If False, the plan is fixed once and only the executor gets feedback (legacy behavior).
    skeleton: 'keep'=preserve the current skeleton (layout/nav paradigm) and only raise quality within it.
              'redesign'=redesign from scratch starting at the skeleton paradigm (visibly different from before at a glance).
              (The planner follows this directive, not any code/template.)
    escalate: if True and started with skeleton='keep', when 'keep' fails to raise the score (conditions below)
              automatically switch to skeleton='redesign' and overhaul the skeleton (staged escalation, once):
                (1) score stagnation (Δ<0.02 vs previous) twice in a row (2) score drop (regression) (3) below baseline
                (4) evaluator judges a structural limit (structural=true).
    baseline: reference score for escalation trigger (3) (if this round's score is below it, change the skeleton). 0 disables (3)."""
    min_rounds = max(1, min(min_rounds, rounds))
    skeleton = (skeleton or "keep").strip().lower()
    _DIR_REDESIGN = (
        "[Skeleton policy: REDESIGN] Rethink the entire skeleton (navigation/layout paradigm) from scratch. "
        "Keep only what is explicitly specified (calculation logic, data fields, etc.). Compare at least 2 alternative "
        "paradigms and pick the better one — e.g. top bar + tabs, step-by-step wizard, full-screen focus, card canvas, 2-pane. "
        "**It must look visibly different from before at a glance.**"
    )
    _DIR_KEEP = (
        "[Skeleton policy: KEEP] The current skeleton (layout/navigation paradigm) is already proven and good — keep it. "
        "Do not overhaul the skeleton or screen structure; preserve it. Within it, raise **only the polish** via "
        "information hierarchy, consistent spacing/alignment, empty/loading/error states, microcopy, accessibility, and measured design tokens."
    )
    skeleton_directive = _DIR_REDESIGN if skeleton == "redesign" else _DIR_KEEP
    _emit("build", f"build loop start — plan→execute→evaluate up to {rounds} rounds "
                   f"(min {min_rounds} forced · replan={'on' if replan else 'off'} · skeleton={skeleton}"
                   f" · escalate={'on' if escalate else 'off'}"
                   f"{f', baseline={baseline}' if baseline else ''})")

    # Per-round output snapshot store (under state, gitignored)
    loop_id = "loop-" + uuid.uuid4().hex[:6]
    snap_root = config.STATE_DIR / "build_snapshots" / loop_id
    snap_src = (config.WORKSPACE_DIR / snapshot_path) if snapshot_path else None

    # 1) Initial plan — designed per the skeleton policy (keep/redesign)
    plan = await run_subagent(
        "planner",
        f"{skeleton_directive}\n\nDraw up a build plan for the following goal.\n[Goal]\n{task}",
        template="planner", context=context)
    _emit("plan", f"initial plan complete (skeleton={skeleton})")

    history: list[dict] = []
    feedback = ""
    final_exec = ""
    best = {"round": 0, "score": -1.0, "snap": None, "exec": ""}  # track the highest-scoring round
    prev_score: float | None = None   # previous round's score (for stagnation/regression checks)
    stagnant = 0                       # consecutive stagnation count
    escalated = False                  # skeleton escalation happens only once

    for i in range(1, rounds + 1):
        # 2) Execute — apply the plan + improvements from the previous evaluation
        exec_task = (
            f"[Full plan]\n{plan}\n\n[This round {i}/{rounds}]\n"
            + (f"Prioritize incorporating the previous evaluation's improvements; revise and refine accordingly:\n{feedback}"
               if feedback else "Implement the plan's 'this round's execution items'.")
        )
        execution = await run_subagent("executor", exec_task, template="executor", context=context)
        final_exec = execution
        _emit("execute", f"round {i}/{rounds} execution complete")

        # One progress screenshot per round (when shot_dist is set) — for tracking visual change
        if shot_dist:
            try:
                shots_dir = config.ROOT / "round_shots" / loop_id
                shot = await _round_screenshot(config.WORKSPACE_DIR / shot_dist, shots_dir,
                                               f"round{i}")
                if shot:
                    _emit("shot", f"round {i}/{rounds} screenshot: round_shots/{loop_id}/round{i}.png")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[build_loop] round {i} screenshot failed: {e}")

        # 3) Evaluate — score against acceptance criteria + improvements
        eval_task = (
            f"[Goal]\n{task}\n\n[Plan including acceptance criteria]\n{plan}\n\n"
            f"[This round's execution report]\n{execution}"
        )
        verdict_text = await run_subagent("evaluator", eval_task, template="evaluator", context=context)
        v = _parse_verdict(verdict_text)
        passed, score = bool(v.get("passed")), v.get("score")
        improvements = v.get("improvements") or []
        history.append({"round": i, "passed": passed, "score": score, "improvements": improvements})
        _emit("evaluate", f"round {i}/{rounds} evaluation: passed={passed}, score={score} (min={min_rounds})")

        # best-round tracking: snapshot this round's output and remember it if it's the highest score.
        sc = float(score) if isinstance(score, (int, float)) else -1.0
        if snap_src and snap_src.exists():
            try:
                snap_i = snap_root / f"r{i}"
                shutil.copytree(snap_src, snap_i, ignore=_snap_ignore, dirs_exist_ok=True)
                if sc > best["score"]:
                    best = {"round": i, "score": sc, "snap": snap_i, "exec": execution}
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[build_loop] snapshot failed r{i}: {e}")
        elif sc > best["score"]:
            best = {"round": i, "score": sc, "snap": None, "exec": execution}

        # ── Skeleton escalation: started with keep but the score isn't rising → switch to redesign (once) ──
        if escalate and skeleton == "keep" and not escalated and i < rounds:
            structural = bool(v.get("structural"))
            trig = None
            if structural:
                trig = "evaluator judged a structural limit (structural=true)"
            elif baseline and sc >= 0 and sc < baseline:
                trig = f"below previous best/baseline (score {sc:.3f} < {baseline:.3f})"
            elif prev_score is not None and sc < prev_score:
                trig = f"score drop (regression) {prev_score:.3f}→{sc:.3f}"
            elif prev_score is not None and (sc - prev_score) < 0.02:
                stagnant += 1
                if stagnant >= 2:
                    trig = "score stagnant twice in a row (Δ<0.02)"
            else:
                stagnant = 0
            if trig:
                escalated = True
                skeleton = "redesign"
                skeleton_directive = _DIR_REDESIGN
                _emit("build", f"⚡ skeleton escalation triggered (round {i}): {trig} → switching to skeleton=redesign, redesigning skeleton")
                replan_now = (
                    f"{skeleton_directive}\n\n[Original goal]\n{task}\n\n"
                    f"[The keep (preserve-skeleton) policy has hit a score ceiling so far — trigger: {trig}]\n"
                    f"[Previous plan]\n{plan}\n\n[Previous round {i} execution result]\n{execution}\n\n"
                    f"[Evaluation improvements]\n" + "\n".join(f"- {x}" for x in improvements) + "\n\n"
                    "Now redesign from scratch starting at the skeleton (navigation/layout paradigm). "
                    "Keep only the calculation logic, data fields, and safeguards. Compare 2+ alternative paradigms, pick the better one, "
                    "and produce a revised plan that looks visibly different from before, in the same format."
                )
                plan = await run_subagent("planner", replan_now, template="planner", context=context)
                _emit("plan", f"round {i} escalation replan (redesign) complete → applies to next round {i+1}")
                prev_score = sc
                feedback = "\n".join(f"- {x}" for x in improvements) or "Push the skeleton-redesign result even further."
                continue  # on escalation, skip the normal break/replan block
        prev_score = sc

        # Even on a pass, don't stop before the minimum rounds (prevents premature termination → real iterative improvement).
        if passed and i >= min_rounds:
            break
        feedback = "\n".join(f"- {x}" for x in improvements)
        if not feedback:
            feedback = (
                "Passing, but below the minimum iterations. Push it one level higher: "
                "information hierarchy, consistent spacing/alignment, empty/loading/error states, microcopy, accessibility, mobile responsiveness."
            )

        # 4) Replan — feed the evaluation back to the 'planner' to revise the plan itself.
        #    (Without this, the plan is fixed once and only the executor gets patched, so the structure never changes.)
        #    Right after the last round there is no next execution, so don't replan.
        if replan and i < rounds:
            replan_task = (
                f"{skeleton_directive}\n\n"
                f"[Original goal]\n{task}\n\n"
                f"[Previous plan]\n{plan}\n\n"
                f"[Previous round {i} execution report]\n{execution}\n\n"
                f"[Evaluator verdict] passed={passed}, score={score}\n"
                f"[Evaluator improvements]\n{feedback}\n\n"
                "Based on the evaluation above, revise 'the plan itself'. Drop items that had no effect, incorporate the improvements, "
                "and produce a **revised plan** in the same format with updated screen information design, execution items, and acceptance criteria for the next round. "
                "Follow the skeleton directive above exactly."
            )
            plan = await run_subagent("planner", replan_task, template="planner", context=context)
            _emit("plan", f"round {i} evaluation incorporated → replan complete (applies to next round {i+1})")

    # ---- best-round adoption: if the last round isn't the top score, restore the highest-scoring output ----
    last_round = history[-1]["round"] if history else 0
    restored_note = ""
    adopted_exec = final_exec
    if best["round"] and best["round"] != last_round:
        if best["snap"] and snap_src:
            try:
                shutil.copytree(best["snap"], snap_src, ignore=_snap_ignore, dirs_exist_ok=True)
                restored_note = (
                    f"\n\n⚠️ best-round adopted: the final round {last_round} regressed (lower score), so "
                    f"the top-scoring **round {best['round']} (score {best['score']})** output was restored to `{snapshot_path}`."
                )
                adopted_exec = best["exec"] or final_exec
                _emit("build", f"best-round restored — round {best['round']} (score {best['score']}) → {snapshot_path}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[build_loop] best-round restore failed: {e}")
        else:
            # Without a snapshot we can't restore files, so just note it in the report (for transparency)
            restored_note = (
                f"\n\nNote: the top score was round {best['round']} (score {best['score']}), but "
                f"since snapshot_path was not set, files couldn't be restored and the final round {last_round} remains on disk."
            )

    # Clean up snapshots
    try:
        if snap_root.exists():
            shutil.rmtree(snap_root, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    best_line = f"top score round {best['round']} (score {best['score']})" if best["round"] else "N/A"
    shots_note = (f"\n[Per-round screenshots] round_shots/{loop_id}/round1..{len(history)}.png"
                  if shot_dist else "")
    return (
        f"Build loop complete ({len(history)} rounds). {best_line}.{restored_note}{shots_note}\n\n"
        f"[Adopted result]\n{adopted_exec}\n\n"
        f"[Evaluation history]\n{json.dumps(history, ensure_ascii=False, indent=2)}"
    )


# ---- In-process SDK MCP tools exposed to the central agent ----


def _emit(kind: str, message: str, **extra) -> None:
    """Record a progress event on the current task. (Lazy import to avoid circular refs.)"""
    tid = current_task_id.get()
    if not tid:
        return
    from agent_core.storage.task_store import store
    store.append_event(tid, kind, message, **extra)


@tool(
    "spawn_agent",
    "Create a new sub-agent to delegate a sub-task and receive its result. "
    "role: the role to assign the worker (free-form string, e.g. 'data analyst'). "
    "task: the specific task to delegate. "
    "template: the prompt template to use — costar (general specialist) / react (step-by-step reasoning with tools) / "
    "expert (expert persona) / default (basic). "
    "context: background/context needed for the task (optional; empty string if none).",
    {"role": str, "task": str, "template": str, "context": str},
)
async def spawn_agent_tool(args: dict) -> dict:
    role = str(args.get("role", "general worker")).strip() or "general worker"
    task = str(args.get("task", "")).strip()
    template = str(args.get("template", "") or DEFAULT_TEMPLATE).strip()
    context = str(args.get("context", ""))
    if not task:
        return {"content": [{"type": "text", "text": "Error: task is empty."}]}

    _emit("spawn", f"sub-agent created: role={role}, template={template}",
          role=role, task=task, template=template)

    try:
        result = await run_subagent(role, task, template, context)
    except Exception as e:  # noqa: BLE001
        _emit("error", f"sub-agent failed: {e}")
        return {"content": [{"type": "text", "text": f"sub-agent run failed: {e}"}]}

    _emit("result", f"sub-agent ({role}) complete", role=role)
    return {"content": [{"type": "text", "text": result}]}


@tool(
    "build_loop",
    "Run a 'plan→execute→evaluate' loop for any task worth iterating for quality — building web/apps/files, and also complex analysis, research, writing, or debugging. "
    "Dedicated planner→executor→evaluator agents communicate through main, iterating up to `rounds` times (default 5) to improve, "
    "and terminate early once the evaluation passes. Use this whenever the task is complex, hard, or high-stakes (not a simple single step); the file/web options below are optional and only apply to file/web outputs. "
    "task: the goal (what to produce or solve). rounds: max iterations (default 5). min_rounds: minimum forced iterations even on a pass (default 3, prevents premature termination). "
    "snapshot_path: workspace-relative output path (e.g. 'myapp/frontend'). If given, snapshot each round and restore the 'highest-scoring' round (guards against a regressing final round). "
    "shot_dist: workspace-relative path to the 'built static folder' (e.g. 'myapp/frontend/dist'). If given, capture one screenshot into round_shots/ at the end of each round. "
    "replan: whether to feed each round's evaluation back to the planner to revise the plan (default true = evaluate→replan loop on; false = plan fixed once, only the executor gets feedback). "
    "skeleton: 'keep' (default) = preserve the current skeleton and only improve polish within it / 'redesign' = redesign from scratch starting at the skeleton paradigm. "
    "escalate: default true. If you start with keep but the score stagnates/regresses/falls below baseline, or the evaluator judges a structural limit, automatically switch to redesign once. "
    "baseline: the escalation reference point (if this round's score is below it, change the skeleton). Pass the previous best score to get 'change the skeleton if it doesn't improve on before'. 0 disables it. "
    "context: background such as tech stack, file locations, and what to keep.",
    {"task": str, "rounds": int, "min_rounds": int, "snapshot_path": str, "shot_dist": str,
     "replan": bool, "skeleton": str, "escalate": bool, "baseline": float, "context": str},
)
async def build_loop_tool(args: dict) -> dict:
    task = str(args.get("task", "")).strip()
    if not task:
        return {"content": [{"type": "text", "text": "Error: task is empty."}]}
    try:
        rounds = int(args.get("rounds") or 5)
    except (TypeError, ValueError):
        rounds = 5
    rounds = max(1, min(rounds, 8))  # upper cap to prevent runaway
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
        _emit("error", f"build loop failed: {e}")
        return {"content": [{"type": "text", "text": f"build loop run failed: {e}"}]}
    return {"content": [{"type": "text", "text": result}]}


@tool(
    "spawn_parallel",
    "Run 'several' independent sub-tasks 'at once (in parallel)'. Use it when you can split the plan into fine-grained, dependency-free parts and run them together. "
    "subtasks: a JSON array string. Each element is {\"role\":\"role\",\"task\":\"specific task\",\"template\":\"costar|react|expert|default (optional)\",\"context\":\"background (optional)\"}. "
    f"Concurrency is automatically capped at {_MAX_PARALLEL_SPAWNS}; the rest wait and are retried automatically. All results are collected and returned together. "
    "Do not use it for dependent tasks where order matters or a later task needs an earlier result (use spawn_agent sequentially for those).",
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
                "text": "Error: subtasks must be a non-empty JSON array. Example: [{\"role\":\"researcher\",\"task\":\"...\"}]"}]}
    items = items[:8]  # upper cap to prevent runaway
    _emit("parallel", f"parallel run start — {len(items)} tasks (concurrency max {_MAX_PARALLEL_SPAWNS})")

    async def _one(idx: int, it: dict):
        role = (str(it.get("role", "general worker")).strip() or "general worker")
        task = str(it.get("task", "")).strip()
        template = str(it.get("template", "") or DEFAULT_TEMPLATE).strip()
        context = str(it.get("context", ""))
        if not task:
            return (idx, role, "(skipped: task is empty)")
        async with _PARALLEL_SEM:   # concurrent-spawn cap
            _emit("spawn", f"[parallel {idx + 1}] {role}", role=role, task=task, template=template)
            try:
                res = await run_subagent(role, task, template, context)
            except Exception as e:  # noqa: BLE001
                res = f"failed: {e}"
            _emit("result", f"[parallel {idx + 1}] {role} complete", role=role)
            return (idx, role, res)

    results = await asyncio.gather(*[_one(i, it) for i, it in enumerate(items)
                                     if isinstance(it, dict)])
    blocks = [f"### [{i + 1}] {role}\n{res}" for i, role, res in sorted(results)]
    _emit("parallel", f"parallel run complete — {len(results)} tasks")
    return {"content": [{"type": "text", "text": "\n\n".join(blocks)}]}


@tool(
    "run_isolated",
    "Run several steps, each in a FRESH sub-agent, with the context isolated. Each step's "
    "observations/tool-noise live and die inside that step and do not accumulate onto the next -> "
    "avoids O(N^2) context blow-up, cost, and hitting the turn limit on long multi-step tasks. "
    "Memory is carried forward via a structured state file: each step appends 'result / carry-over / "
    "failure', and the next step's agent reads it first (only the summary crosses over, not raw history "
    "= lossy but cheap). Use for long, mostly-independent sequential work (reading/collecting/processing "
    "in chunks). For fully independent, parallelizable work use spawn_parallel; for one continuous chain "
    "of reasoning use a single spawn_agent. "
    "steps: JSON array; each item {\"role\":\"...\",\"task\":\"this step's work\",\"template\":\"(optional)\"}. "
    "context: shared background for every step (optional). state_name: state-file name (optional). "
    "model: worker model (optional, e.g. 'claude-haiku-4-5' to make collection cheap).",
    {"steps": str, "context": str, "state_name": str, "model": str},
)
async def run_isolated_tool(args: dict) -> dict:
    import uuid as _uuid
    raw = str(args.get("steps", "")).strip()
    try:
        steps = json.loads(raw)
        if not isinstance(steps, list) or not steps:
            raise ValueError
    except Exception:  # noqa: BLE001
        return {"content": [{"type": "text",
                "text": "Error: steps must be a non-empty JSON array, e.g. [{\"role\":\"...\",\"task\":\"...\"}]"}]}
    steps = [it for it in steps[:20] if isinstance(it, dict)]
    shared_ctx = str(args.get("context", ""))
    worker_model = str(args.get("model", "")).strip() or None
    name = str(args.get("state_name", "")).strip() or ("iso-" + _uuid.uuid4().hex[:8])

    state_dir = config.STATE_DIR / "isolated"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"{name}.md"
    if not state_path.exists():
        header = f"# Isolated run state — {name}\n"
        if shared_ctx:
            header += f"\n## Shared context\n{shared_ctx}\n"
        header += "\n(Each step appends 'result / carry-over / failure' below; the next step reads it.)\n"
        state_path.write_text(header, encoding="utf-8")

    _emit("isolated", f"isolated run start — {len(steps)} steps, state=state/isolated/{name}.md"
                      + (f", worker_model={worker_model}" if worker_model else ""))

    results = []
    for idx, it in enumerate(steps):
        role = (str(it.get("role", "step worker")).strip() or "step worker")
        stask = str(it.get("task", "")).strip()
        template = str(it.get("template", "") or DEFAULT_TEMPLATE).strip()
        if not stask:
            results.append((idx, role, "(empty task, skipped)"))
            continue
        try:
            state_txt = state_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            state_txt = ""
        worker_task = (
            f"[Accumulated state — prior steps' result/carry-over/failure summaries. "
            f"No raw observations; rely on this summary only]\n{state_txt}\n\n"
            f"[This step {idx + 1}/{len(steps)}]\n{stask}\n\n"
            f"[Return format — return ONLY these three text sections (no images)]\n"
            f"RESULT: <this step's output summary; state key facts/numbers/names explicitly>\n"
            f"CARRY: <facts the next step must know, 1-5 lines, or 'none'>\n"
            f"FAILED: <any approach you tried that did not work (so the next step won't repeat it), or 'none'>"
        )
        _emit("spawn", f"[isolated {idx + 1}/{len(steps)}] {role}", role=role, task=stask, template=template)
        try:
            res = await run_subagent(role, worker_task, template, shared_ctx, model=worker_model)
        except Exception as e:  # noqa: BLE001
            res = f"failed: {e}"
        try:
            with state_path.open("a", encoding="utf-8") as f:
                f.write(f"\n\n## Step {idx + 1}: {role}\n{res}\n")
        except Exception:  # noqa: BLE001
            pass
        _emit("result", f"[isolated {idx + 1}/{len(steps)}] {role} done", role=role)
        results.append((idx, role, res))

    blocks = [f"### Step {i + 1} — {role}\n{res}" for i, role, res in results]
    _emit("isolated", f"isolated run complete — {len(results)} steps")
    return {"content": [{"type": "text",
            "text": "\n\n".join(blocks) + f"\n\n(isolated state file: state/isolated/{name}.md)"}]}


@tool(
    "request_tool",
    "When you hit a task your current tools can't do AND it's pure compute/string/data work "
    "(e.g. a parser, converter, validator, calculator), build that tool yourself instead of giving up. "
    "A tool-smith writes the code; it is registered only if it passes automated gates "
    "(static safety scan + self-test + load check). Once registered it's available from the NEXT "
    "spawn/goal and persists across sessions (capability compounds). "
    "name: short tool name. purpose: what it does. signature: inputs and return. context: background (optional). "
    "Note: tools that delete files, run processes, do network I/O, or touch secrets are auto-blocked — don't make those.",
    {"name": str, "purpose": str, "signature": str, "context": str},
)
async def request_tool_tool(args: dict) -> dict:
    if not self_tooling.is_armed():
        return {"content": [{"type": "text", "text":
                "Self-tooling is off. A human must arm it once "
                "(state/self_tooling.json armed=true, and no state/STOP). Tool creation disabled for now."}]}

    name = self_tooling.slug(args.get("name", ""))
    purpose = str(args.get("purpose", "")).strip()
    signature = str(args.get("signature", "")).strip()
    context = str(args.get("context", ""))
    if not purpose:
        return {"content": [{"type": "text", "text": "Error: purpose (what the tool does) is required."}]}

    _emit("self_tool", f"self-tooling: forging tool '{name}' — {purpose[:60]}")

    spec = (f"Tool name: {name}\nWhat it does: {purpose}\nInputs/return: {signature}\n"
            "Implement as a pure function with no side effects. TOOLS list and _selftest() are required.")
    try:
        code_raw = await run_subagent("tool smith", spec, template="tool-smith", context=context)
    except Exception as e:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"tool-smith run failed: {e}"}]}

    code = self_tooling.extract_code(code_raw)
    if "@tool" not in code or "TOOLS" not in code or "_selftest" not in code:
        self_tooling.record({"name": name, "status": "rejected", "reason": "missing @tool/TOOLS/_selftest"})
        return {"content": [{"type": "text", "text":
                f"Rejected: generated code lacks required shape (@tool/TOOLS/_selftest). '{name}' not registered."}]}

    hits = self_tooling.static_scan(code)
    if hits:
        self_tooling.record({"name": name, "status": "blocked", "reason": f"dangerous: {', '.join(hits)}"})
        _emit("self_tool", f"blocked: '{name}' dangerous [{', '.join(hits)}] — discarded")
        return {"content": [{"type": "text", "text":
                f"Blocked: tool '{name}' touches dangerous categories [{', '.join(hits)}]. Auto-discarded."}]}

    path = self_tooling.GENERATED_DIR / f"{name}.py"
    try:
        path.write_text(code, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"write failed: {e}"}]}

    ok, out = self_tooling.run_selftest(path)
    if not ok:
        try:
            path.unlink()
        except Exception:  # noqa: BLE001
            pass
        self_tooling.record({"name": name, "status": "failed", "reason": f"self-test failed: {out[:200]}"})
        _emit("self_tool", f"failed: '{name}' self-test — discarded")
        return {"content": [{"type": "text", "text":
                f"Failed: tool '{name}' did not pass its self-test -> auto-discarded.\n{out[:300]}"}]}

    if build_generated_server() is None:
        try:
            path.unlink()
        except Exception:  # noqa: BLE001
            pass
        self_tooling.record({"name": name, "status": "failed", "reason": "MCP load failed"})
        return {"content": [{"type": "text", "text": f"Failed: tool '{name}' MCP load check failed -> discarded."}]}

    self_tooling.record({"name": name, "status": "registered", "purpose": purpose})
    _emit("self_tool", f"registered: tool '{name}' — available from the next spawn/goal")
    return {"content": [{"type": "text", "text":
            f"Success: forged, verified and registered tool '{name}' (tools/generated/{name}.py). "
            f"All gates (static scan / self-test / load) passed. It is available from the NEXT sub-agent spawn/goal."}]}


def build_generated_server():
    """Build an in-process MCP server from the persisted self-built tools in tools/generated/*.py.

    Each file must expose a module-level `TOOLS = [...]` (list of @tool objects).
    Broken files are skipped so one bad file can't take down the rest. None if no tools.
    """
    import importlib.util
    tools = []
    for f in sorted(self_tooling.GENERATED_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"generated_{f.stem}", f)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            for t in getattr(m, "TOOLS", []) or []:
                tools.append(t)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[generated] failed to load {f.name} (skipped): {e}")
    if not tools:
        return None
    return create_sdk_mcp_server(name="generated", version="0.1.0", tools=tools)


def build_factory_server():
    """Build an in-process MCP server: spawn_agent + spawn_parallel + build_loop + run_isolated + request_tool."""
    return create_sdk_mcp_server(
        name="agent-factory",
        version="1.2.0",
        tools=[spawn_agent_tool, spawn_parallel_tool, build_loop_tool,
               run_isolated_tool, request_tool_tool],
    )


# ---- Knowledge-saving tool (writes to the persistent knowledge/ store) ----

@tool(
    "save_knowledge",
    "Save collected/synthesized knowledge to the persistent wiki store (knowledge/). Unlike runtime md, it persists. "
    "title: document title. summary: one-line insight. content: key notes (bulleted markdown). "
    "category: placement — Projects/Topics/Decisions/Skills or a 'Topics/subdir' form (freely extensible). "
    "tags: comma-separated tags. related: comma-separated titles of related documents (links; 2 or more recommended). "
    "raw_text: original text to archive (optional).",
    {"title": str, "summary": str, "content": str, "category": str,
     "tags": str, "related": str, "raw_text": str},
)
async def save_knowledge_tool(args: dict) -> dict:
    from agent_core.kb.knowledge_store import save_knowledge

    title = str(args.get("title", "")).strip()
    if not title:
        return {"content": [{"type": "text", "text": "Error: title is empty."}]}
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
    _emit("kb", f"knowledge saved: {res['path']}")
    # Optional: git sync
    try:
        from agent_core.kb.knowledge_store import git_sync
        git_sync(f"save {res['path']}")
    except Exception:  # noqa: BLE001
        pass
    msg = f"saved: {res['path']}"
    if res.get("suggestion"):
        msg += f"\nSuggestion: {res['suggestion']}"
    return {"content": [{"type": "text", "text": msg}]}


def build_knowledge_server():
    """Build an in-process MCP server holding the save_knowledge tool."""
    return create_sdk_mcp_server(
        name="knowledge",
        version="1.0.0",
        tools=[save_knowledge_tool],
    )


def build_notion_server():
    """Register the official Notion MCP server (@notionhq/notion-mcp-server) over stdio.

    Unlike the in-process servers (factory/kb/publish), this MCP server runs as an
    external process (npx). Returns a config dict only when NOTION_TOKEN (.env) is
    present; otherwise returns None to disable it (the Notion tools aren't exposed at all).

    The token is an internal integration token (ntn_...) and is injected via headers.
    It's exposed to the agent as mcp__notion__* tools.
    """
    if not config.NOTION_TOKEN:
        return None
    headers = json.dumps({
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": config.NOTION_VERSION,
    })
    # On Windows the npx launcher is npx.cmd (based on the bundled node environment PATH).
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    return {
        "type": "stdio",
        "command": npx,
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env": {"OPENAPI_MCP_HEADERS": headers},
    }


def _subagent_servers():
    """MCP servers for sub-agents: kb (always) + notion (when a token is present) +
    generated (self-built tools, when any exist — reused on every later spawn)."""
    servers = {"kb": build_knowledge_server()}
    notion = build_notion_server()
    if notion:
        servers["notion"] = notion
    generated = build_generated_server()
    if generated:
        servers["generated"] = generated
    return servers
