"""Central configuration — reads values from the .env file and environment variables."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv


def _seed(root: Path, bundle: Path) -> None:
    """First-run seed for a packaged (.exe) build: copy read-only assets from the bundle
    into the writable data home so knowledge/self-improve/routines can persist and be edited."""
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("templates", "agents", "knowledge"):
        src, dst = bundle / sub, root / sub
        if src.exists() and not dst.exists():
            try:
                shutil.copytree(src, dst)
            except Exception:  # noqa: BLE001
                pass
    envf = root / ".env"
    if not envf.exists() and (bundle / ".env.example").exists():
        try:
            shutil.copy(bundle / ".env.example", envf)
        except Exception:  # noqa: BLE001
            pass


# Project root = the writable data home.
#  - From source: the repo root (this file is <root>/agent_core/config.py).
#  - Frozen (.exe): AGENT_CORE_HOME or ~/.agent-core, seeded from the bundled assets.
if getattr(sys, "frozen", False):
    ROOT = Path(os.environ.get("AGENT_CORE_HOME") or (Path.home() / ".agent-core")).resolve()
    _seed(ROOT, Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)))
else:
    ROOT = Path(__file__).resolve().parent.parent

# Load .env (if present)
load_dotenv(ROOT / ".env")

# Directories
WORKSPACE_DIR = ROOT / "workspace"          # Space where agents perform file work
LOGS_DIR = ROOT / "logs"                    # Logs
STATE_DIR = ROOT / "state"                  # Persisted task state (JSON)
TEMPLATES_DIR = ROOT / "templates"          # Cited prompt templates
RUNTIME_AGENTS_DIR = ROOT / "runtime_agents"  # Subagent .md files created at runtime (deleted on exit)

for _d in (WORKSPACE_DIR, LOGS_DIR, STATE_DIR, TEMPLATES_DIR, RUNTIME_AGENTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Authentication / models
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-opus-4-8")
SUBAGENT_MODEL = os.getenv("SUBAGENT_MODEL", "claude-sonnet-4-6")

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8848"))

# Safeguards / concurrency
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
MAX_TURNS = int(os.getenv("MAX_TURNS", "40"))
# Max number of automatic resumes when the turn limit is hit before completion (0 disables auto-resume)
MAX_AUTO_RESUMES = int(os.getenv("MAX_AUTO_RESUMES", "3"))
PERMISSION_MODE = os.getenv("PERMISSION_MODE", "bypassPermissions")

# Knowledge store (knowledge/) git sync — whether to auto commit/push on save
KB_GIT_SYNC = os.getenv("KB_GIT_SYNC", "false").lower() == "true"
KB_GIT_PUSH = os.getenv("KB_GIT_PUSH", "false").lower() == "true"  # push after commit (requires auth)

# Prompt used when resuming a task.
# Freely edit the RESUME_PROMPT.txt file and its contents will be used (falls back to the default below).
RESUME_PROMPT_FILE = ROOT / "RESUME_PROMPT.txt"
_DEFAULT_RESUME_PROMPT = (
    "There is already data collected under knowledge/ and workspace/. "
    "Check that first, then pick up from where you left off and finish it."
)


def get_resume_prompt() -> str:
    """Read the resume prompt from RESUME_PROMPT.txt (falls back to the default if missing or empty)."""
    try:
        if RESUME_PROMPT_FILE.exists():
            txt = RESUME_PROMPT_FILE.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_RESUME_PROMPT


# Publishing — channel settings for shipping finished deliverables
# Deliverables are always staged in workspace/_ready_to_publish/ awaiting release (works unconditionally).
READY_DIR = WORKSPACE_DIR / "_ready_to_publish"
# Auto-publish hook: if this URL is set, publish will POST to that webhook.
# (Wire it up to a no-code automation like Make/Zapier/n8n to actually auto-publish to a blog/marketplace/social media)
PUBLISH_WEBHOOK_URL = os.getenv("PUBLISH_WEBHOOK_URL", "").strip()

# Payout receiving account (stored only in .env — not git-tracked). Injected into the agent prompt at runtime.
PAYOUT_ACCOUNT = os.getenv("PAYOUT_ACCOUNT", "").strip()

# ── Channel (messenger) integration — the harness 'Channels' pillar ──
# Values used by the Telegram bridge (channels/telegram_bridge.py).
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# Whitelist of allowed sender chat_ids (comma-separated). Empty means allow all (dev only) — always set this in production.
TELEGRAM_ALLOWED_CHAT_IDS = [
    s.strip() for s in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if s.strip()
]
# Base URL of the agent-core API the bridge will call (default = local server).
AGENT_CORE_URL = os.getenv("AGENT_CORE_URL", f"http://{HOST}:{PORT}")
# Persistent store file for conversations (channels).
CONVERSATIONS_FILE = STATE_DIR / "conversations.json"

# Notion MCP — internal integration token (stored only in .env, not git-tracked).
# If set, the notion tools are exposed to the orchestrator/subagents (disabled otherwise).
# Issue a token at: notion.so/my-integrations → Internal Integration → add the Connection to the target page.
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")
