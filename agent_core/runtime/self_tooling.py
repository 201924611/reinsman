"""Self-tooling — the agent builds, verifies and reuses tools it is missing.

Flow (fully autonomous, no per-tool human approval):
  request_tool -> a "tool-smith" sub-agent writes a single-file @tool + a _selftest()
  -> three automated gates (static safety scan + self-test + load check)
  -> on pass, persisted to tools/generated/<name>.py -> loaded into every later
     spawn/goal, so capability compounds across tasks and sessions.

What replaces human approval (keeps it from harming itself):
  - static safety scan: blocks delete / process / network / secret-access / eval
  - self-test: the tool's own _selftest() must pass in a separate process + timeout
  - load check: it must actually load as an MCP tool (broken tools are discarded)
  - kill switch (state/STOP) + one-time arm (state/self_tooling.json) + audit log
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from agent_core import config

GENERATED_DIR = config.ROOT / "tools" / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = config.STATE_DIR / "self_tooling.json"
STOP_FILE = config.STATE_DIR / "STOP"

_DEFAULT = {"armed": False, "audit": []}


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return dict(_DEFAULT)


def _save(d: dict) -> None:
    STATE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def is_armed() -> bool:
    """Is self-tooling on? The kill switch (STOP) forces False."""
    if STOP_FILE.exists():
        return False
    return bool(_load().get("armed"))


def set_armed(on: bool) -> None:
    d = _load()
    d["armed"] = bool(on)
    _save(d)


def record(entry: dict) -> None:
    d = _load()
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    d.setdefault("audit", []).append(entry)
    _save(d)


# -- Gate 1: static safety scan --------------------------------------------
# A generated tool touching any of these categories is rejected automatically.
# Safe default = pure compute / string / data (no side effects).
_FORBIDDEN = [
    (r"\bshutil\.rmtree\b", "recursive delete"),
    (r"\bos\.(remove|unlink|rmdir)\b|\.unlink\s*\(", "file delete"),
    (r"rm\s+-rf|\bdel\s+/|Remove-Item", "shell bulk delete"),
    (r"\.env\b|password|secret|credential|api[_-]?key|token", "secret/credential access"),
    (r"\bsocket\b|requests\.(post|put|patch|delete)|urllib\.request|httpx|aiohttp", "network egress"),
    (r"\beval\s*\(|\bexec\s*\(|__import__\s*\(|compile\s*\(", "dynamic code execution"),
    (r"\bsubprocess\b|os\.system|os\.popen|Popen", "arbitrary process execution"),
    (r"\bwinreg\b|HKEY_|\bctypes\b", "low-level OS access"),
]


def static_scan(code: str) -> list[str]:
    """Return the list of dangerous categories hit. Empty = clean."""
    hits = []
    for pat, why in _FORBIDDEN:
        if re.search(pat, code, re.IGNORECASE):
            hits.append(why)
    return sorted(set(hits))


# -- Gate 2: self-test (separate process + timeout) ------------------------
def run_selftest(module_path: Path, timeout: int = 20) -> tuple[bool, str]:
    """Run the generated module's _selftest() in an isolated process. Pass only on True."""
    script = (
        "import importlib.util,sys\n"
        f"spec=importlib.util.spec_from_file_location('gen',r'{module_path}')\n"
        "m=importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "assert getattr(m,'TOOLS',None), 'TOOLS list missing or empty'\n"
        "sys.exit(0 if bool(m._selftest()) else 1)\n"
    )
    try:
        p = subprocess.run([sys.executable, "-c", script], capture_output=True,
                           text=True, timeout=timeout)
        return (p.returncode == 0, ((p.stdout or "") + (p.stderr or ""))[-500:])
    except subprocess.TimeoutExpired:
        return (False, f"self-test timeout ({timeout}s) — suspected hang/loop")
    except Exception as e:  # noqa: BLE001
        return (False, f"self-test run error: {e}")


def extract_code(text: str) -> str:
    """Pull the python code out of the tool-smith's return (strip markdown fences)."""
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def slug(name: str) -> str:
    s = re.sub(r"\W+", "_", (name or "").strip()).strip("_").lower()
    return s or "tool"
