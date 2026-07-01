"""Telegram <-> agent-core bridge (P1 PoC).

Stage 1 of the harness evolution roadmap: 'reuse a messenger instead of building a chat front-end
from scratch' (the OpenClaw insight). Send a message via Telegram -> submit that text as a goal to the
agent-core HTTP API -> when it finishes, reply with a result summary in the same conversation.
Conversations are persisted per chat_id, laying the groundwork for 'continuing from another
window (another device)'.

Design notes:
- Separate from the engine (uses only the HTTP API) -> a PoC that doesn't touch server internals. In P2,
  a conversation is tied 1:1 to a server task/session and promoted to true session resume.
- Safety-gate seed: the TELEGRAM_ALLOWED_CHAT_IDS whitelist restricts senders (if unset, allow everyone
  for development + warn).
- Zero dependencies (urllib only). Logic can be validated without a token via `--dry-run`.

Run:
    python channels/telegram_bridge.py              # real run (requires TELEGRAM_BOT_TOKEN in .env)
    python channels/telegram_bridge.py --dry-run    # self-test the logic without a token/server
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from applog import get_logger  # noqa: E402

try:  # keep Unicode/emoji output from breaking even on a Windows console (cp949)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

log = get_logger()
CONV_FILE = config.STATE_DIR / "conversations.json"


# ───────────────────────── conversation persistence ─────────────────────────
def _load_convs() -> dict[str, Any]:
    try:
        return json.loads(CONV_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_convs(d: dict[str, Any]) -> None:
    CONV_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def get_conversation(chat_id: str) -> dict[str, Any]:
    """Per-chat_id conversation state (created if missing). 'history' accumulates recent exchanges for context continuity."""
    convs = _load_convs()
    if chat_id not in convs:
        convs[chat_id] = {
            "conversation_id": f"tg-{chat_id}",
            "last_task_id": None,
            "history": [],   # [{"role":"user"/"agent","text":...}]
        }
        _save_convs(convs)
    return convs[chat_id]


def update_conversation(chat_id: str, *, last_task_id: str | None = None,
                        add_user: str | None = None, add_agent: str | None = None) -> None:
    convs = _load_convs()
    c = convs.setdefault(chat_id, {"conversation_id": f"tg-{chat_id}", "last_task_id": None, "history": []})
    if last_task_id is not None:
        c["last_task_id"] = last_task_id
    if add_user is not None:
        c["history"].append({"role": "user", "text": add_user})
    if add_agent is not None:
        c["history"].append({"role": "agent", "text": add_agent})
    c["history"] = c["history"][-20:]  # keep only the last 20
    _save_convs(convs)


def build_goal(chat_id: str, text: str) -> str:
    """Build the goal by prepending prior conversation context (context continuity). Replaced by session resume in P2."""
    c = get_conversation(chat_id)
    hist = c.get("history", [])
    if not hist:
        return text
    ctx = "\n".join(f"- {h['role']}: {h['text'][:200]}" for h in hist[-6:])
    return f"[Prior conversation context]\n{ctx}\n\n[Current request]\n{text}"


# ───────────────────────── external-dependency abstractions (for test injection) ─────────────────────────
class Telegram(Protocol):
    def get_updates(self, offset: int) -> list[dict[str, Any]]: ...
    def send(self, chat_id: str, text: str) -> None: ...


class AgentCore(Protocol):
    def submit_goal(self, goal: str) -> str: ...
    def wait_task(self, task_id: str, timeout: float) -> dict[str, Any]: ...


# ── real implementations ──
class RealTelegram:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/{method}", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.loads(r.read().decode("utf-8")).get("result")

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        return self._call("getUpdates", {"offset": offset, "timeout": 50}) or []

    def send(self, chat_id: str, text: str) -> None:
        # guard against Telegram's message length limit (4096)
        self._call("sendMessage", {"chat_id": chat_id, "text": text[:4000]})


class RealAgentCore:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def submit_goal(self, goal: str) -> str:
        payload = json.dumps({"goal": goal, "variant": "channel-telegram"}).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/goal", data=payload,
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8")).get("task_id")

    def wait_task(self, task_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            with urllib.request.urlopen(f"{self.base}/tasks/{task_id}", timeout=15) as r:
                last = json.loads(r.read().decode("utf-8"))
            if last.get("status") != "running":
                return last
            time.sleep(5)
        last["status"] = last.get("status", "timeout")
        return last


def summarize_result(task: dict[str, Any]) -> str:
    """Extract a single human-facing summary chunk from the task result (defensively)."""
    status = task.get("status", "?")
    for key in ("result", "summary", "output"):
        if task.get(key):
            return f"[{status}] {str(task[key])[:1500]}"
    events = task.get("events") or []
    for e in reversed(events):
        if e.get("kind") in ("done", "result", "think") and e.get("message"):
            return f"[{status}] {str(e['message'])[:1500]}"
    return f"[{status}] (no content found to summarize)"


# ───────────────────────── the bridge itself ─────────────────────────
class Bridge:
    def __init__(self, tg: Telegram, core: AgentCore, allowed: list[str] | None,
                 task_timeout: float = 1800.0):
        self.tg = tg
        self.core = core
        self.allowed = allowed or []
        self.task_timeout = task_timeout

    def _authorized(self, chat_id: str) -> bool:
        return not self.allowed or chat_id in self.allowed

    def handle_message(self, chat_id: str, text: str) -> str:
        """Handle one message: authorize -> submit goal -> wait -> reply. Returns the reply text (for test convenience)."""
        if not self._authorized(chat_id):
            reply = "⛔ Sender not authorized. Ask the operator to register your chat_id."
            self.tg.send(chat_id, reply)
            return reply
        update_conversation(chat_id, add_user=text)
        goal = build_goal(chat_id, text)
        try:
            task_id = self.core.submit_goal(goal)
        except Exception as e:  # noqa: BLE001
            reply = f"⚠️ Failed to submit goal: {e}"
            self.tg.send(chat_id, reply)
            return reply
        update_conversation(chat_id, last_task_id=task_id)
        self.tg.send(chat_id, f"🦞 Received (task {task_id}). Working…")
        task = self.core.wait_task(task_id, self.task_timeout)
        reply = summarize_result(task)
        update_conversation(chat_id, add_agent=reply)
        self.tg.send(chat_id, reply)
        return reply

    def run(self) -> None:
        log.info("[telegram] bridge started — waiting on polling")
        offset = 0
        while True:
            try:
                for upd in self.tg.get_updates(offset):
                    offset = max(offset, upd.get("update_id", 0) + 1)
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat_id = str((msg.get("chat") or {}).get("id", ""))
                    if text and chat_id:
                        log.info(f"[telegram] {chat_id}: {text[:80]}")
                        self.handle_message(chat_id, text)
            except urllib.error.URLError as e:
                log.warning(f"[telegram] network error, retrying: {e}")
                time.sleep(5)
            except Exception as e:  # noqa: BLE001
                log.warning(f"[telegram] loop error: {e}")
                time.sleep(3)


# ───────────────────────── dry-run self-test ─────────────────────────
def _dry_run() -> int:
    """Validate the bridge logic without a token/server. Injects a fake Telegram and fake agent-core."""
    sent: list[tuple[str, str]] = []

    class FakeTelegram:
        def get_updates(self, offset: int): return []
        def send(self, chat_id: str, text: str): sent.append((chat_id, text))

    class FakeCore:
        def __init__(self): self.last_goal = None
        def submit_goal(self, goal: str):
            self.last_goal = goal
            return "task-test-001"
        def wait_task(self, task_id: str, timeout: float):
            return {"status": "done", "events": [{"kind": "done", "message": "Done: this is the result summary."}]}

    # isolate with a temporary conversation file
    global CONV_FILE
    orig = CONV_FILE
    CONV_FILE = config.STATE_DIR / "conversations.selftest.json"
    if CONV_FILE.exists():
        CONV_FILE.unlink()
    ok = True
    try:
        core = FakeCore()
        br = Bridge(FakeTelegram(), core, allowed=["123"])

        # 1) an unauthorized sender is blocked
        r = br.handle_message("999", "hello")
        assert "not authorized" in r, "whitelist block failed"

        # 2) allowed sender: acknowledgement message + result reply (2 messages)
        sent.clear()
        r = br.handle_message("123", "organize today's schedule")
        assert any("Received" in t for _, t in sent), "acknowledgement missing"
        assert "Done" in r, "result reply missing"

        # 3) conversation persistence + context continuity
        conv = get_conversation("123")
        assert conv["last_task_id"] == "task-test-001", "task_id persistence failed"
        assert len(conv["history"]) >= 2, "history accumulation failed"
        r2 = br.handle_message("123", "then what about tomorrow?")
        assert "[Prior conversation context]" in core.last_goal, "context continuity failed (prior conversation not injected)"

        print("✅ DRY-RUN PASS — auth gate, acknowledgement reply, result summary, conversation persistence, and context continuity all OK")
    except AssertionError as e:
        ok = False
        print(f"❌ DRY-RUN FAIL: {e}")
    finally:
        if CONV_FILE.exists():
            CONV_FILE.unlink()
        CONV_FILE = orig
    return 0 if ok else 1


def main() -> int:
    if "--dry-run" in sys.argv:
        return _dry_run()
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        print("TELEGRAM_BOT_TOKEN is not in .env. Create a bot with @BotFather and add the token.")
        print("(To see just the logic without a token: python channels/telegram_bridge.py --dry-run)")
        return 2
    if not config.TELEGRAM_ALLOWED_CHAT_IDS:
        log.warning("[telegram] ⚠️ ALLOWED_CHAT_IDS not set — anyone can run the agent (for development).")
    br = Bridge(
        RealTelegram(token),
        RealAgentCore(config.AGENT_CORE_URL),
        allowed=config.TELEGRAM_ALLOWED_CHAT_IDS,
    )
    br.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
