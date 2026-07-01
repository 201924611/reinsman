"""publish tool — the step that 'publishes' a finished deliverable.

When a central/subagent calls the `publish` tool:
  1. It stages the deliverable under workspace/_ready_to_publish/<date>/<slug>/
     (manifest.json + content file) — this step 'always' works, even without credentials.
  2. If PUBLISH_WEBHOOK_URL is set in .env, it POSTs the payload to that webhook.
     (Wired to a no-code automation like Make/Zapier/n8n, this leads to real auto-publishing
     to a blog, marketplace, SNS, etc.)

In short, everything completes automatically 'right up to publishing' even without credentials,
and once the hook is connected, actual publishing is automated with no human in the loop. The agent
just calls this; it does not stop mid-way to ask a human whenever it hits something it can't do.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import tool, create_sdk_mcp_server

from agent_core import config

from agent_core.applog import get_logger

logger = get_logger()


def _slug(text: str) -> str:
    s = re.sub(r"[^\w가-힣 \-]", "", text or "").strip().replace(" ", "_")
    return (s or "untitled")[:80]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage(channel: str, title: str, body: str, meta: dict) -> Path:
    """Stage the deliverable + manifest into the pending-publish folder (always works)."""
    slug = _slug(title)
    folder = config.READY_DIR / _today() / f"{channel}__{slug}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "content.md").write_text(body, encoding="utf-8")
    manifest = {
        "id": uuid.uuid4().hex,
        "channel": channel,
        "title": title,
        "tags": meta.get("tags", ""),
        "staged_at": _now_iso(),
        "status": "staged",
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return folder


def _post_webhook(payload: dict) -> tuple[bool, str]:
    """POST the payload to PUBLISH_WEBHOOK_URL. (stdlib only)"""
    url = config.PUBLISH_WEBHOOK_URL
    if not url:
        return False, "PUBLISH_WEBHOOK_URL not set — skipping webhook publish (staging only)"
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            code = resp.getcode()
            return (200 <= code < 300), f"webhook responded {code}"
    except Exception as e:  # noqa: BLE001
        return False, f"webhook publish failed: {e}"


@tool(
    "publish",
    "Publish a finished deliverable (article/product/page, etc.). Even without credentials it is "
    "always staged into the pending-publish folder, and if PUBLISH_WEBHOOK_URL is set it goes all the "
    "way to real auto-publishing via that hook. "
    "channel: publishing channel label (e.g. blog/store/sns/youtube, free-form). "
    "title: the title. body: the body text to publish (Markdown/HTML, etc.). "
    "tags: comma-separated tags (optional).",
    {"channel": str, "title": str, "body": str, "tags": str},
)
async def publish_tool(args: dict) -> dict:
    channel = (str(args.get("channel", "")).strip() or "blog")
    title = str(args.get("title", "")).strip() or "untitled"
    body = str(args.get("body", ""))
    tags = str(args.get("tags", ""))
    if not body.strip():
        return {"content": [{"type": "text", "text": "Error: body (publish content) is empty."}]}

    # 1) Always: stage into the pending-publish folder
    folder = await asyncio.to_thread(_stage, channel, title, body, {"tags": tags})
    rel = str(folder.relative_to(config.WORKSPACE_DIR)).replace("\\", "/")

    # 2) Optional: webhook auto-publish
    payload = {
        "channel": channel, "title": title, "tags": tags,
        "body": body, "published_at": _now_iso(),
    }
    ok, detail = await asyncio.to_thread(_post_webhook, payload)

    logger.info(f"[publish] channel={channel} title={title!r} staged={rel} webhook_ok={ok} ({detail})")
    status = "Published (webhook)" if ok else "Staged for publishing"
    msg = (
        f"{status}\n"
        f"- channel: {channel}\n"
        f"- staging folder: workspace/{rel}\n"
        f"- webhook: {detail}\n"
        f"(If no webhook is set: put a Make/Zapier/n8n hook in PUBLISH_WEBHOOK_URL in .env and it will "
        f"auto-publish from then on. Until then, a human just needs to upload the staged deliverable once.)"
    )
    return {"content": [{"type": "text", "text": msg}]}


def build_publish_server():
    """In-process MCP server that hosts the publish tool."""
    return create_sdk_mcp_server(name="publish", version="1.0.0", tools=[publish_tool])
