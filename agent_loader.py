"""에이전트 정의 로더.

agents/ 폴더의 *.md 파일을 읽어 에이전트 정의(AgentDef)로 변환한다.
각 파일은 다음 형식이다:

    ---
    name: researcher
    description: ...
    model: claude-sonnet-4-6      # (선택) 없으면 SUBAGENT_MODEL 사용
    allowed_tools: a, b, c        # (선택) 쉼표 구분. 없으면 제한 안 함
    ---
    <본문 = 시스템 프롬프트>

이렇게 하면 코드를 고치지 않고 .md 파일만 추가/수정해서
새 에이전트를 만들거나 기존 에이전트의 역할을 바꿀 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import config

AGENTS_DIR = config.ROOT / "agents"


@dataclass
class AgentDef:
    name: str
    description: str = ""
    model: str | None = None              # None이면 호출부에서 기본 모델로 대체
    allowed_tools: list[str] = field(default_factory=list)  # 비면 제한 안 함
    system_prompt: str = ""


def _parse(text: str) -> tuple[dict[str, str], str]:
    """frontmatter(dict)와 본문(str)을 분리한다. yaml 의존성 없는 최소 파서."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        # frontmatter 없으면 전체를 본문으로 취급
        return {}, text.strip()

    meta: dict[str, str] = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
        i += 1
    body = "\n".join(lines[i + 1:]).strip()
    return meta, body


def load_agent_file(path: Path) -> AgentDef | None:
    """주어진 경로의 .md 파일을 에이전트 정의로 읽는다. 없으면 None."""
    if not path.exists():
        return None
    meta, body = _parse(path.read_text(encoding="utf-8"))

    model = meta.get("model") or None
    if model in ("", "default"):
        model = None

    tools_raw = meta.get("allowed_tools", "")
    allowed_tools = [t.strip() for t in tools_raw.split(",") if t.strip()]

    return AgentDef(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        model=model,
        allowed_tools=allowed_tools,
        system_prompt=body,
    )


def load_agent(name: str) -> AgentDef | None:
    """이름(agents/<name>.md)으로 에이전트 정의를 읽는다. 없으면 None."""
    return load_agent_file(AGENTS_DIR / f"{name}.md")


def list_agents() -> list[str]:
    """agents/ 폴더에 정의된 에이전트 이름 목록."""
    if not AGENTS_DIR.exists():
        return []
    return sorted(p.stem for p in AGENTS_DIR.glob("*.md"))
