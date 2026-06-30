"""배포(publish) 툴 — 완성한 산출물을 '발행'하는 단계.

중앙/하위 에이전트가 `publish` 툴을 호출하면:
  1. 산출물을 workspace/_ready_to_publish/<날짜>/<slug>/ 에 발행 대기 상태로 적재하고
     (manifest.json + 본문 파일) — 이 단계는 자격증명 없이도 '항상' 동작한다.
  2. .env 에 PUBLISH_WEBHOOK_URL 이 설정돼 있으면 그 webhook 으로 payload 를 POST 한다.
     (Make/Zapier/n8n 등 노코드 자동화에 연결하면 블로그·마켓·SNS 실제 자동 발행으로 이어짐)

즉, 자격증명이 없어도 '배포 직전까지' 자동으로 완료되고, 훅만 연결하면 실제 발행까지
사람 개입 없이 자동화된다. 에이전트는 이걸 호출만 하면 되고, 안 되는 부분을 만나
중간에 멈춰 사람에게 되묻지 않는다.
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

import config
from applog import get_logger

logger = get_logger()


def _slug(text: str) -> str:
    s = re.sub(r"[^\w가-힣 \-]", "", text or "").strip().replace(" ", "_")
    return (s or "untitled")[:80]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage(channel: str, title: str, body: str, meta: dict) -> Path:
    """발행 대기 폴더에 산출물 + manifest 적재 (항상 동작)."""
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
    """PUBLISH_WEBHOOK_URL 로 payload 를 POST. (stdlib만 사용)"""
    url = config.PUBLISH_WEBHOOK_URL
    if not url:
        return False, "PUBLISH_WEBHOOK_URL 미설정 — webhook 발행 건너뜀(대기 적재만 완료)"
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            code = resp.getcode()
            return (200 <= code < 300), f"webhook 응답 {code}"
    except Exception as e:  # noqa: BLE001
        return False, f"webhook 발행 실패: {e}"


@tool(
    "publish",
    "완성한 산출물(글/상품/페이지 등)을 발행한다. 자격증명이 없어도 발행 대기 폴더에 "
    "반드시 적재되며, PUBLISH_WEBHOOK_URL 이 설정돼 있으면 그 훅으로 실제 자동 발행까지 한다. "
    "channel: 발행 채널 라벨(예: blog/store/sns/youtube, 자유). "
    "title: 제목. body: 발행할 본문 텍스트(마크다운/HTML 등). "
    "tags: 쉼표 구분 태그(선택).",
    {"channel": str, "title": str, "body": str, "tags": str},
)
async def publish_tool(args: dict) -> dict:
    channel = (str(args.get("channel", "")).strip() or "blog")
    title = str(args.get("title", "")).strip() or "untitled"
    body = str(args.get("body", ""))
    tags = str(args.get("tags", ""))
    if not body.strip():
        return {"content": [{"type": "text", "text": "오류: body(발행 본문)가 비어 있습니다."}]}

    # 1) 항상: 발행 대기 폴더에 적재
    folder = await asyncio.to_thread(_stage, channel, title, body, {"tags": tags})
    rel = str(folder.relative_to(config.WORKSPACE_DIR)).replace("\\", "/")

    # 2) 옵션: webhook 자동 발행
    payload = {
        "channel": channel, "title": title, "tags": tags,
        "body": body, "published_at": _now_iso(),
    }
    ok, detail = await asyncio.to_thread(_post_webhook, payload)

    logger.info(f"[publish] channel={channel} title={title!r} staged={rel} webhook_ok={ok} ({detail})")
    status = "발행 완료(webhook)" if ok else "발행 대기 적재 완료"
    msg = (
        f"{status}\n"
        f"- 채널: {channel}\n"
        f"- 대기 폴더: workspace/{rel}\n"
        f"- webhook: {detail}\n"
        f"(webhook 미설정 시: .env 의 PUBLISH_WEBHOOK_URL 에 Make/Zapier/n8n 훅을 넣으면 "
        f"이후 자동 발행됨. 그 전까지는 대기 폴더의 산출물을 사람이 한 번 올리면 된다.)"
    )
    return {"content": [{"type": "text", "text": msg}]}


def build_publish_server():
    """publish 툴을 담은 in-process MCP 서버."""
    return create_sdk_mcp_server(name="publish", version="1.0.0", tools=[publish_tool])
