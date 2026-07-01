"""Execution tracing — token usage · tool-call chains · grouping by session.

Each task has a single Trace containing multiple Spans (one agent run each).
- The orchestrator span(s) plus N subagent spans, grouped by parent/session_id.
- Each span: role/kind/model/template, tokens (in·out·cache), cost, turns, duration, list of tool calls.

Saved to traces/<task_id>.json, read by the /trace endpoint and the HTML viewer.
So tracing never blocks the actual work on failure, every write is wrapped in a try at the call site.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

TRACES_DIR = config.ROOT / "traces"
TRACES_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _brief(name: str, inp: dict) -> str:
    """Summarize a tool's input into a short one-liner."""
    try:
        if name.endswith("spawn_agent"):
            return f"role={inp.get('role','')} / template={inp.get('template','')}"
        if name.endswith("save_knowledge"):
            return f"title={inp.get('title','')}"
        if name in ("Bash", "BashOutput"):
            return str(inp.get("command", ""))[:80]
        if name in ("Read", "Write", "Edit"):
            return str(inp.get("file_path", ""))[:80]
        if name.endswith("publish"):
            return f"{inp.get('channel','')}:{inp.get('title','')}"[:80]
        return json.dumps(inp, ensure_ascii=False)[:80]
    except Exception:  # noqa: BLE001
        return ""


@dataclass
class ToolCall:
    ts: str
    name: str
    brief: str


@dataclass
class Span:
    span_id: str
    role: str                      # "orchestrator" | subagent role
    kind: str                      # "orchestrator" | "subagent"
    model: str | None = None
    template: str | None = None
    parent: str | None = None      # parent span_id
    session_id: str | None = None
    started_at: str = field(default_factory=_now)
    ended_at: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    status: str = "running"        # running | ok | error
    tools: list[dict] = field(default_factory=list)


@dataclass
class Trace:
    task_id: str
    goal: str = ""
    variant: str = "default"       # structure/experiment label (for A/B comparison)
    created_at: str = field(default_factory=_now)
    spans: list[dict] = field(default_factory=list)


def _extract_tokens(usage: dict | None) -> dict[str, int]:
    """Defensively extract the four token counts from an SDK usage dict."""
    u = usage or {}
    def g(*keys):
        for k in keys:
            if k in u and isinstance(u[k], (int, float)):
                return int(u[k])
        return 0
    return {
        "input_tokens": g("input_tokens", "inputTokens"),
        "output_tokens": g("output_tokens", "outputTokens"),
        "cache_read_tokens": g("cache_read_input_tokens", "cacheReadInputTokens", "cache_read_tokens"),
        "cache_creation_tokens": g("cache_creation_input_tokens", "cacheCreationInputTokens", "cache_creation_tokens"),
    }


class TraceStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._traces: dict[str, Trace] = {}

    def _path(self, task_id: str) -> Path:
        return TRACES_DIR / f"{task_id}.json"

    def _flush(self, task_id: str) -> None:
        t = self._traces.get(task_id)
        if not t:
            return
        tmp = self._path(task_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(t), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path(task_id))

    def start_trace(self, task_id: str, goal: str, variant: str = "default") -> None:
        with self._lock:
            if task_id in self._traces:
                return
            # On server restart/resume, if a trace file already exists, append to it (avoid overwriting).
            p = self._path(task_id)
            if p.exists():
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    self._traces[task_id] = Trace(
                        task_id=task_id, goal=d.get("goal", goal),
                        variant=d.get("variant", variant),
                        created_at=d.get("created_at", _now()),
                        spans=d.get("spans", []),
                    )
                    return
                except Exception:  # noqa: BLE001
                    pass
            self._traces[task_id] = Trace(task_id=task_id, goal=goal, variant=variant)
            self._flush(task_id)

    def start_span(self, task_id: str, role: str, kind: str,
                   model: str | None = None, template: str | None = None,
                   parent: str | None = None) -> str:
        span_id = kind[:4] + "-" + uuid.uuid4().hex[:8]
        with self._lock:
            t = self._traces.get(task_id)
            if not t:
                t = self._traces[task_id] = Trace(task_id=task_id)
            t.spans.append(asdict(Span(
                span_id=span_id, role=role, kind=kind, model=model,
                template=template, parent=parent,
            )))
            self._flush(task_id)
        return span_id

    def _find(self, t: Trace, span_id: str) -> dict | None:
        for s in t.spans:
            if s["span_id"] == span_id:
                return s
        return None

    def add_tool(self, task_id: str, span_id: str, name: str, inp: dict) -> None:
        with self._lock:
            t = self._traces.get(task_id)
            if not t:
                return
            s = self._find(t, span_id)
            if s is None:
                return
            s["tools"].append(asdict(ToolCall(ts=_now(), name=name, brief=_brief(name, inp))))
            self._flush(task_id)

    def end_span(self, task_id: str, span_id: str, *, status: str = "ok",
                 session_id: str | None = None, usage: dict | None = None,
                 cost_usd: float | None = None, num_turns: int | None = None,
                 duration_ms: int | None = None) -> None:
        with self._lock:
            t = self._traces.get(task_id)
            if not t:
                return
            s = self._find(t, span_id)
            if s is None:
                return
            tok = _extract_tokens(usage)
            s.update({
                "ended_at": _now(), "status": status, "session_id": session_id,
                "cost_usd": cost_usd, "num_turns": num_turns, "duration_ms": duration_ms,
                **tok,
            })
            self._flush(task_id)

    # ---- query / aggregation ----
    def get(self, task_id: str) -> dict | None:
        p = self._path(task_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in TRACES_DIR.glob("*.json"))

    @staticmethod
    def totals(trace: dict) -> dict[str, Any]:
        """Aggregate tokens/tools/sessions across the whole trace (multi-group summary)."""
        spans = trace.get("spans", [])
        sessions = {s.get("session_id") for s in spans if s.get("session_id")}
        cost_vals = [s.get("cost_usd") for s in spans if s.get("cost_usd")]
        return {
            "spans": len(spans),
            "subagents": sum(1 for s in spans if s.get("kind") == "subagent"),
            "sessions": len(sessions),
            "input_tokens": sum(s.get("input_tokens", 0) for s in spans),
            "output_tokens": sum(s.get("output_tokens", 0) for s in spans),
            "cache_read_tokens": sum(s.get("cache_read_tokens", 0) for s in spans),
            "cache_creation_tokens": sum(s.get("cache_creation_tokens", 0) for s in spans),
            "tool_calls": sum(len(s.get("tools", [])) for s in spans),
            "num_turns": sum((s.get("num_turns") or 0) for s in spans),
            "cost_usd": (round(sum(cost_vals), 4) if cost_vals else None),
        }


store = TraceStore()
