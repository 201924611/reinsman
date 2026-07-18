"""HTTP API server — always-on, forwarding commands (goals) to the central agent.

Endpoints:
  GET  /                                   -> built-in web chat UI
  POST /goal          {"goal": "..."}      -> accept a goal, return task_id (runs in background)
  GET  /tasks                              -> list all tasks
  GET  /tasks/{id}                         -> a specific task's details + progress events
  POST /tasks/{id}/cancel                  -> cancel a running task
  GET  /health                             -> health check

Run: python -m reinsman   (or run.ps1)
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from reinsman import config

from reinsman.runtime import routines

from reinsman.prompts.agent_loader import list_agents, load_agent
from reinsman.applog import get_logger
from reinsman.prompts.template_engine import list_templates, load_template
from reinsman.runtime.orchestrator import run_goal
from reinsman.runtime.webui import CHAT_HTML
from reinsman.storage.task_store import store

logger = get_logger()

# Scheduler settings (.env)
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
SCHEDULER_CHECK_SECONDS = int(os.getenv("SCHEDULER_CHECK_SECONDS", "300"))  # how often to check for due routines

# Limit on concurrently running goals (to prevent runaway load / cost)
_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_TASKS)
# Handles of running asyncio.Tasks (for cancellation)
_running: dict[str, asyncio.Task] = {}


class GoalRequest(BaseModel):
    goal: str
    variant: str = "default"          # configuration/experiment label (for A/B comparison)
    overrides: dict | None = None     # configuration overrides: {model, subagent_model, max_turns}


class RoutineRequest(BaseModel):
    name: str
    prompt: str                       # the plan (goal) handed to the agent each interval
    interval_hours: float = 24.0
    first_delay_hours: float | None = None  # delay before the first run (defaults to after one interval)


# ── Autonomy opt-in (default OFF): scheduled routines fire only when the user turns this on ──
_AUTONOMY_FILE = config.STATE_DIR / "autonomy.json"


def _autonomy_enabled() -> bool:
    try:
        return bool(json.loads(_AUTONOMY_FILE.read_text(encoding="utf-8")).get("enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _set_autonomy(enabled: bool) -> None:
    _AUTONOMY_FILE.write_text(json.dumps({"enabled": bool(enabled)}), encoding="utf-8")


# Ready-made routines the user can add with one click (values to drop into a routine).
ROUTINE_PRESETS = [
    {"name": "Self-directed work", "interval_hours": 24,
     "prompt": ("Review the knowledge vault (Index), recent tasks, and eval history. Pick the single most "
                "valuable next task that advances ongoing goals, and carry it out end to end (use build_loop "
                "if it's complex). Save what you learn with save_knowledge. Keep it to one focused task."),
     "description": "Every cycle, autonomously pick and do the most valuable task."},
    {"name": "Self-improvement", "interval_hours": 168,
     "prompt": "@self-improve",
     "description": "Analyze recent evals and auto-apply a prompt improvement (backup kept; revert available)."},
    {"name": "Knowledge digest", "interval_hours": 24,
     "prompt": ("Summarize what changed in the knowledge vault and recent tasks since the last run into a short "
                "digest, and save it with save_knowledge under Topics/Digest."),
     "description": "A recurring digest of new knowledge and tasks."},
]


async def _run_self_improve(r) -> None:
    """Handle an '@self-improve' routine by running the self-improvement cycle directly (not as a goal)."""
    from reinsman.runtime import self_improve
    routines.store.mark_ran(r.id, "self-improve")
    try:
        res = await self_improve.auto_cycle()
        logger.info(f"[scheduler] self-improve routine '{r.name}': {res}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[scheduler] self-improve routine failed: {e}")


async def _fire_routine(r) -> str:
    """Fire one routine. '@self-improve' runs the self-improve cycle; otherwise submit the prompt as a goal."""
    if r.prompt.strip().lower().startswith("@self-improve"):
        await _run_self_improve(r)
        return "self-improve"
    tid = _submit_goal(r.prompt, variant=f"routine:{r.name}", source=f"routine {r.id}({r.name})")
    routines.store.mark_ran(r.id, tid)
    logger.info(f"[scheduler] routine fired: {r.name} → task {tid} (next: {r.next_run})")
    return tid


async def _scheduler_loop():
    """Periodic routine dispatcher — fires due routines ONLY when autonomy is enabled (opt-in, default off)."""
    logger.info(f"[scheduler] started — checking every {SCHEDULER_CHECK_SECONDS}s (autonomy opt-in, default off)")
    while True:
        try:
            if _autonomy_enabled():
                for r in routines.store.due():
                    await _fire_routine(r)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[scheduler] loop error: {e}")
        await asyncio.sleep(SCHEDULER_CHECK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On server startup: clean up tasks left as 'running' (from an abnormal shutdown)
    for t in store.list():
        if t.status in ("running", "queued"):
            store.update(t.id, status="error", error="interrupted by server restart")
    sched_task = None
    if SCHEDULER_ENABLED:
        sched_task = asyncio.create_task(_scheduler_loop())
    yield
    if sched_task:
        sched_task.cancel()


app = FastAPI(title="Reinsman", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def chat_ui():
    """Built-in web chat UI — open http://127.0.0.1:8848 to talk to the agent."""
    return CHAT_HTML


async def _execute(task_id: str, goal: str, resume_session: str | None = None,
                   variant: str = "default", overrides: dict | None = None) -> None:
    async with _semaphore:
        try:
            await run_goal(task_id, goal, resume_session=resume_session,
                           variant=variant, overrides=overrides)
        except Exception:  # detailed errors are recorded to the store inside run_goal
            pass
        finally:
            _running.pop(task_id, None)


def _submit_goal(goal: str, variant: str = "default", overrides: dict | None = None,
                 source: str | None = None) -> str:
    """Register a goal as a new task and kick off background execution (shared by requests and the scheduler)."""
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
        raise HTTPException(status_code=400, detail="goal is empty.")
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
        raise HTTPException(status_code=404, detail="unknown task_id")
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
    """Resume a task that became incomplete due to the turn limit, or failed/was cancelled.

    - Picks up the interrupted task's SDK session (session_id) and continues with the
      conversation context intact, and
    - supplies a resume prompt telling it to check the data left in knowledge/ and workspace/.
    The resume prompt can be edited in RESUME_PROMPT.txt.
    """
    t = store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="unknown task_id")
    if task_id in _running:
        raise HTTPException(status_code=409, detail="task is already running.")
    if t.status not in ("incomplete", "error", "cancelled", "done"):
        raise HTTPException(status_code=400, detail=f"cannot resume from status: {t.status}")

    cont_prompt = config.get_resume_prompt()
    # Resuming keeps the same configuration (variant) (read from the trace)
    from reinsman.observability import tracing

    tr = tracing.store.get(task_id) or {}
    variant = tr.get("variant", "default")
    store.append_event(task_id, "resume", f"resume started (session={t.session_id})")
    handle = asyncio.create_task(
        _execute(task_id, cont_prompt, resume_session=t.session_id, variant=variant))
    _running[task_id] = handle
    return {"task_id": task_id, "status": "resuming", "resume_session": t.session_id}


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    handle = _running.get(task_id)
    if not handle:
        raise HTTPException(status_code=404, detail="task is not running.")
    handle.cancel()
    store.update(task_id, status="cancelled")
    store.append_event(task_id, "cancel", "cancelled by user")
    return {"task_id": task_id, "status": "cancelled"}


@app.get("/agents")
async def agents():
    """Fixed agent definitions in the agents/ folder (the central orchestrator, etc.)."""
    out = []
    for name in list_agents():
        a = load_agent(name)
        if a:
            out.append({
                "name": a.name,
                "description": a.description,
                "model": a.model or "(default)",
                "allowed_tools": a.allowed_tools or "(all)",
            })
    return out


@app.get("/templates")
async def templates():
    """List of prompt templates and their sources."""
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
    """List of runtime sub-agent md files currently active (not yet deleted)."""
    return [p.stem for p in config.RUNTIME_AGENTS_DIR.glob("*.md")]


@app.get("/knowledge")
async def knowledge():
    """List of documents stored in the persistent knowledge store (knowledge/10_Wiki)."""
    from reinsman.kb.knowledge_store import list_entries
    return {"count": len(list_entries()), "entries": list_entries()}


class FeedbackRequest(BaseModel):
    note: str                       # a one-line piece of user feedback
    approved: bool | None = None    # shorthand: True->signal "approved", False->"rejected"
    category: str | None = None     # the category the feedback is about (rel path under 10_Wiki)
    signal: str | None = None       # approved | praised | kept | edited | rejected | moved
    moved_to: str | None = None     # if you moved it, the corrected category (teaches a redirect)


@app.post("/knowledge/feedback")
async def knowledge_feedback(req: FeedbackRequest):
    """Record user feedback and feed the bandit loop: structured signals update a per-category
    reward tally, and repeated `moved_to` corrections make save_knowledge auto-redirect that
    category next time. A human-readable line is also appended to Policy.md."""
    note = (req.note or "").strip()
    if not note:
        return {"ok": False, "error": "note is empty."}
    signal = req.signal
    if signal is None and req.approved is not None:
        signal = "approved" if req.approved else "rejected"
    from reinsman.kb.knowledge_store import record_feedback
    record_feedback(note, category=req.category, signal=signal, moved_to=req.moved_to)
    return {"ok": True, "recorded": note, "category": req.category,
            "signal": signal, "moved_to": req.moved_to}


@app.get("/knowledge/policy")
async def knowledge_policy():
    """Current feedback policy: average reward per category + learned corrections (bandit state)."""
    from reinsman.kb.knowledge_store import policy_scores
    return policy_scores()


@app.get("/traces")
async def list_traces():
    """List of recorded traces (configuration label + token/tool aggregates)."""
    from reinsman.observability import tracing

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
    """A task's full trace (spans, tool chain, sessions) + aggregates."""
    from reinsman.observability import tracing

    tr = tracing.store.get(task_id)
    if not tr:
        raise HTTPException(status_code=404, detail="trace not found")
    return {**tr, "totals": tracing.TraceStore.totals(tr)}


@app.get("/trace/{task_id}/view", response_class=HTMLResponse)
async def trace_view(task_id: str):
    from reinsman.observability import viewer

    return viewer.trace_view_html(task_id)


@app.post("/tasks/{task_id}/evaluate")
async def evaluate_task(task_id: str):
    """Automatically score a task (LLM judge + rule-based metrics), then store and return the result."""
    from reinsman.observability import evaluation

    return await evaluation.evaluate(task_id)


@app.get("/eval/{task_id}")
async def get_eval(task_id: str):
    from reinsman.observability import evaluation

    e = evaluation.get_eval(task_id)
    if not e:
        raise HTTPException(status_code=404, detail="no evaluation (run POST /tasks/{id}/evaluate first)")
    return e


@app.get("/compare")
async def compare(ids: str):
    """Compare several tasks side by side by configuration (variant) (trace aggregates + eval scores)."""
    from reinsman.observability import tracing

    from reinsman.observability import evaluation

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
    from reinsman.observability import viewer

    return viewer.compare_view_html(ids)


class ProposeRequest(BaseModel):
    target: str = "orchestrator"      # "orchestrator" or a template name
    n: int = 8                        # number of recent evals to analyze


class ABRequest(BaseModel):
    proposal_id: str
    goal: str                         # test goal for comparing the current vs. candidate prompt


@app.post("/self-improve/propose")
async def si_propose(req: ProposeRequest):
    """Analyze recent evals and produce an improvement proposal for the target prompt. (Doesn't touch the live file.)"""
    from reinsman.runtime import self_improve

    return await self_improve.propose(target=req.target, n=req.n)


@app.get("/self-improve/proposals")
async def si_list():
    from reinsman.runtime import self_improve

    return self_improve.list_proposals()


@app.get("/self-improve/proposal/{pid}")
async def si_get(pid: str):
    from reinsman.runtime import self_improve

    p = self_improve.get_proposal(pid)
    if not p:
        raise HTTPException(status_code=404, detail="unknown proposal")
    return p


@app.post("/self-improve/apply/{pid}")
async def si_apply(pid: str):
    """Apply a candidate to the live prompt (human-approval gate). Auto-backup before applying."""
    from reinsman.runtime import self_improve

    r = self_improve.apply(pid)
    if "error" in r:
        raise HTTPException(status_code=400, detail=r["error"])
    return r


@app.post("/self-improve/revert")
async def si_revert(target: str = "orchestrator"):
    from reinsman.runtime import self_improve

    r = self_improve.revert(target)
    if "error" in r:
        raise HTTPException(status_code=400, detail=r["error"])
    return r


@app.post("/self-improve/ab")
async def si_ab(req: ABRequest):
    """Run the same goal simultaneously with the current (baseline) vs. candidate prompt.
    When done, evaluate each and compare scores via /compare. (The candidate is injected without any live change.)"""
    from reinsman.runtime import self_improve

    if not self_improve.proposal_text(req.proposal_id):
        raise HTTPException(status_code=404, detail="unknown proposal")
    goal = req.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal is empty.")
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
            "note": "Once both are done, run POST /tasks/{id}/evaluate on each, then open the compare link above"}


@app.get("/routines")
async def list_routines():
    """List of registered periodic routines + next scheduled run + whether autonomy is on."""
    return {
        "autonomy_enabled": _autonomy_enabled(),
        "scheduler_enabled": SCHEDULER_ENABLED,
        "check_seconds": SCHEDULER_CHECK_SECONDS,
        "routines": [r.__dict__ for r in routines.store.list()],
    }


@app.get("/routines/presets")
async def routine_presets():
    """Ready-made routines the user can add with one click."""
    return {"presets": ROUTINE_PRESETS}


@app.get("/scheduler")
async def scheduler_status():
    return {"autonomy_enabled": _autonomy_enabled(),
            "scheduler_enabled": SCHEDULER_ENABLED, "check_seconds": SCHEDULER_CHECK_SECONDS}


class SchedulerToggle(BaseModel):
    enabled: bool


@app.post("/scheduler")
async def set_scheduler(req: SchedulerToggle):
    """Master opt-in switch: when true, the scheduler starts firing enabled routines."""
    _set_autonomy(req.enabled)
    return {"autonomy_enabled": _autonomy_enabled()}


@app.post("/routines")
async def add_routine(req: RoutineRequest):
    """Register a periodic routine. The scheduler submits the prompt as a goal every interval_hours."""
    name = req.name.strip()
    prompt = req.prompt.strip()
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="name and prompt are required.")
    r = routines.store.add(name, prompt, interval_hours=req.interval_hours,
                           first_delay_hours=req.first_delay_hours)
    return r.__dict__


@app.delete("/routines/{rid}")
async def delete_routine(rid: str):
    if not routines.store.remove(rid):
        raise HTTPException(status_code=404, detail="unknown routine")
    return {"removed": rid}


@app.post("/routines/{rid}/toggle")
async def toggle_routine(rid: str):
    r = routines.store.toggle(rid)
    if not r:
        raise HTTPException(status_code=404, detail="unknown routine")
    return {"id": rid, "enabled": r.enabled}


@app.post("/routines/{rid}/run")
async def run_routine_now(rid: str):
    """Run a routine once, right now (and reschedule the next regular run to one interval later)."""
    r = routines.store.get(rid)
    if not r:
        raise HTTPException(status_code=404, detail="unknown routine")
    result = await _fire_routine(r)   # manual run works regardless of the autonomy switch
    return {"routine": rid, "result": result, "next_run": routines.store.get(rid).next_run}


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
