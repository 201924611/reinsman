"""프롬프트 템플릿 엔진.

templates/ 폴더의 인용된 프롬프트 템플릿을 읽어, 중앙 에이전트가 준 값
(role / task / context)을 채워 완성된 시스템 프롬프트를 만든다.
"""
from __future__ import annotations

import config
from agent_loader import _parse

DEFAULT_TEMPLATE = "default"


def load_template(name: str) -> tuple[dict[str, str], str] | None:
    """템플릿 .md 를 (frontmatter, 본문) 으로 읽는다. 없으면 None."""
    path = config.TEMPLATES_DIR / f"{name}.md"
    if not path.exists():
        return None
    return _parse(path.read_text(encoding="utf-8"))


def list_templates() -> list[str]:
    if not config.TEMPLATES_DIR.exists():
        return []
    # README.md 는 템플릿이 아니므로 제외
    return sorted(p.stem for p in config.TEMPLATES_DIR.glob("*.md") if p.stem.lower() != "readme")


def render(name: str, role: str, task: str, context: str = "") -> tuple[dict[str, str], str]:
    """템플릿에 값을 채워 (frontmatter, 완성된 본문) 을 반환한다.
    알 수 없는 템플릿이면 default 로 폴백한다."""
    loaded = load_template(name) or load_template(DEFAULT_TEMPLATE)
    if loaded is None:
        # 템플릿 폴더가 비어있는 극단적 상황의 안전 폴백
        meta: dict[str, str] = {}
        body = "너는 {{role}}로서 호출된 하위 작업자다. 작업: {{task}} / 맥락: {{context}}"
    else:
        meta, body = loaded

    filled = (
        body.replace("{{role}}", role or "범용 작업자")
        .replace("{{task}}", task)
        .replace("{{context}}", context.strip() or "(추가 맥락 없음)")
    )
    return meta, filled
