"""Prompt template engine.

Reads the cited prompt templates from the templates/ folder and fills in the values
(role / task / context) provided by the central agent to build a complete system prompt.
"""
from __future__ import annotations

import config
from agent_loader import _parse

DEFAULT_TEMPLATE = "default"


def load_template(name: str) -> tuple[dict[str, str], str] | None:
    """Read a template .md as (frontmatter, body). Returns None if absent."""
    path = config.TEMPLATES_DIR / f"{name}.md"
    if not path.exists():
        return None
    return _parse(path.read_text(encoding="utf-8"))


def list_templates() -> list[str]:
    if not config.TEMPLATES_DIR.exists():
        return []
    # Exclude README.md since it is not a template
    return sorted(p.stem for p in config.TEMPLATES_DIR.glob("*.md") if p.stem.lower() != "readme")


def render(name: str, role: str, task: str, context: str = "") -> tuple[dict[str, str], str]:
    """Fill in the template values and return (frontmatter, completed body).
    Falls back to default for an unknown template."""
    loaded = load_template(name) or load_template(DEFAULT_TEMPLATE)
    if loaded is None:
        # Safe fallback for the extreme case where the templates folder is empty
        meta: dict[str, str] = {}
        body = "You are a subordinate worker invoked as {{role}}. Task: {{task}} / Context: {{context}}"
    else:
        meta, body = loaded

    filled = (
        body.replace("{{role}}", role or "general-purpose worker")
        .replace("{{task}}", task)
        .replace("{{context}}", context.strip() or "(no additional context)")
    )
    return meta, filled
