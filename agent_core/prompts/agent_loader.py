"""Agent definition loader.

Reads *.md files from the agents/ folder and converts them into agent definitions (AgentDef).
Each file has the following format:

    ---
    name: researcher
    description: ...
    model: claude-sonnet-4-6      # (optional) falls back to SUBAGENT_MODEL if absent
    allowed_tools: a, b, c        # (optional) comma-separated. No restriction if absent
    ---
    <body = system prompt>

This way you can add/edit .md files to create new agents or change an existing
agent's role without touching any code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_core import config


AGENTS_DIR = config.ROOT / "agents"


@dataclass
class AgentDef:
    name: str
    description: str = ""
    model: str | None = None              # If None, the caller substitutes the default model
    allowed_tools: list[str] = field(default_factory=list)  # No restriction if empty
    system_prompt: str = ""


def _parse(text: str) -> tuple[dict[str, str], str]:
    """Split into frontmatter (dict) and body (str). Minimal parser with no yaml dependency."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        # No frontmatter: treat the whole text as the body
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
    """Read the .md file at the given path as an agent definition. Returns None if absent."""
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
    """Read an agent definition by name (agents/<name>.md). Returns None if absent."""
    return load_agent_file(AGENTS_DIR / f"{name}.md")


def list_agents() -> list[str]:
    """List of agent names defined in the agents/ folder."""
    if not AGENTS_DIR.exists():
        return []
    return sorted(p.stem for p in AGENTS_DIR.glob("*.md"))
