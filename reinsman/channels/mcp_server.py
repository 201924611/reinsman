"""MCP channel — expose the harness to other agents as a Model Context Protocol server.

Runs as a stdio MCP server (the standard transport for Claude Code / Claude Desktop
connectors) and bridges tool calls to a running reinsman HTTP server. This makes the
harness usable *from inside* the tools people already work in: an assistant can hand
reinsman a long-running goal, keep chatting, and collect the result later.

Setup (Claude Code):
    claude mcp add reinsman -- reinsman-mcp
    # or: claude mcp add reinsman -- python -m reinsman.channels.mcp_server

Requires the `mcp` extra (`pip install reinsman[mcp]`) and a running server
(`reinsman`, default http://127.0.0.1:8848 — override with REINSMAN_URL).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("REINSMAN_URL", "http://127.0.0.1:8848").rstrip("/")

mcp = FastMCP(
    "reinsman",
    instructions=(
        "Bridge to a local reinsman server — a self-hosted agent harness. "
        "Use submit_goal for long-running autonomous work (it returns immediately "
        "with a task_id), then poll get_task for progress/result. Results include "
        "trace ids; every run is recorded and replayable on the server."
    ),
)


def _api(method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"content-type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        return {"error": f"reinsman server unreachable at {BASE_URL} ({e}). "
                         "Start it with `reinsman` (or `python -m reinsman`)."}


@mcp.tool()
def submit_goal(goal: str) -> dict:
    """Submit one goal to the reinsman harness for autonomous execution.

    Returns immediately with a task_id; the resident orchestrator plans, spawns
    sub-agents and verify loops as needed, and runs in the background. Poll
    get_task(task_id) for progress and the final result."""
    return _api("POST", "/goal", {"goal": goal})


@mcp.tool()
def get_task(task_id: str) -> dict:
    """Get a task's status ('queued'|'running'|'done'|'incomplete'|'error'|'cancelled'),
    final result text, and recent progress events."""
    data = _api("GET", f"/tasks/{task_id}")
    if "events" in data and isinstance(data["events"], list):
        data["events"] = data["events"][-10:]  # keep the payload small for the caller
    return data


@mcp.tool()
def list_tasks() -> list | dict:
    """List all tasks on the server (id, status, goal) — newest last."""
    data = _api("GET", "/tasks")
    if isinstance(data, list):
        return [{"id": t.get("id"), "status": t.get("status"),
                 "goal": (t.get("goal") or "")[:120]} for t in data]
    return data


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
