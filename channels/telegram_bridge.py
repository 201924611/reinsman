"""텔레그램 ↔ agent-core 브리지 (P1 PoC).

하네스 진화 로드맵의 1단계: '채팅 프런트를 직접 만들지 않고 메신저를 재활용'(OpenClaw 인사이트).
텔레그램으로 메시지를 보내면 → 그 텍스트를 goal로 agent-core HTTP API에 제출 →
완료되면 결과 요약을 같은 대화로 회신한다. chat_id 단위로 conversation을 영속화해
'다른 창(다른 기기)에서도 이어지기'의 토대를 만든다.

설계 메모:
- 엔진과 분리(HTTP API만 사용) → 서버 내부를 안 건드리는 PoC. P2에서 conversation을
  서버의 task/session에 1:1로 묶어 진짜 세션 resume로 승격한다.
- 안전 게이트 씨앗: TELEGRAM_ALLOWED_CHAT_IDS 화이트리스트로 발신자를 제한(미설정 시 개발용 전체허용 + 경고).
- 의존성 0 (urllib만). 토큰 없이 `--dry-run`으로 로직을 검증할 수 있다.

실행:
    python channels/telegram_bridge.py              # 실제 구동(.env에 TELEGRAM_BOT_TOKEN 필요)
    python channels/telegram_bridge.py --dry-run    # 토큰/서버 없이 로직 셀프테스트
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

try:  # Windows 콘솔(cp949)에서도 한글/이모지 출력이 깨지지 않게
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

log = get_logger()
CONV_FILE = config.STATE_DIR / "conversations.json"


# ───────────────────────── 대화 영속 저장 ─────────────────────────
def _load_convs() -> dict[str, Any]:
    try:
        return json.loads(CONV_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_convs(d: dict[str, Any]) -> None:
    CONV_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def get_conversation(chat_id: str) -> dict[str, Any]:
    """chat_id별 대화 상태(없으면 생성). 'history'에 최근 교환을 누적해 맥락 이어가기에 쓴다."""
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
    c["history"] = c["history"][-20:]  # 최근 20개만 보존
    _save_convs(convs)


def build_goal(chat_id: str, text: str) -> str:
    """이전 대화 맥락을 앞에 붙여 goal을 구성(맥락 이어가기). P2에서 세션 resume로 대체."""
    c = get_conversation(chat_id)
    hist = c.get("history", [])
    if not hist:
        return text
    ctx = "\n".join(f"- {h['role']}: {h['text'][:200]}" for h in hist[-6:])
    return f"[이전 대화 맥락]\n{ctx}\n\n[이번 요청]\n{text}"


# ───────────────────────── 외부 의존 추상화(테스트 주입용) ─────────────────────────
class Telegram(Protocol):
    def get_updates(self, offset: int) -> list[dict[str, Any]]: ...
    def send(self, chat_id: str, text: str) -> None: ...


class AgentCore(Protocol):
    def submit_goal(self, goal: str) -> str: ...
    def wait_task(self, task_id: str, timeout: float) -> dict[str, Any]: ...


# ── 실제 구현 ──
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
        # 텔레그램 메시지 길이 제한(4096) 보호
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
    """task 결과에서 사람에게 보낼 한 덩어리 요약을 뽑는다(방어적)."""
    status = task.get("status", "?")
    for key in ("result", "summary", "output"):
        if task.get(key):
            return f"[{status}] {str(task[key])[:1500]}"
    events = task.get("events") or []
    for e in reversed(events):
        if e.get("kind") in ("done", "result", "think") and e.get("message"):
            return f"[{status}] {str(e['message'])[:1500]}"
    return f"[{status}] (요약할 내용을 찾지 못했습니다)"


# ───────────────────────── 브리지 본체 ─────────────────────────
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
        """한 건 처리: 권한확인→goal제출→대기→회신. 회신 텍스트를 반환(테스트 편의)."""
        if not self._authorized(chat_id):
            reply = "⛔ 허용되지 않은 발신자입니다. 운영자에게 chat_id 등록을 요청하세요."
            self.tg.send(chat_id, reply)
            return reply
        update_conversation(chat_id, add_user=text)
        goal = build_goal(chat_id, text)
        try:
            task_id = self.core.submit_goal(goal)
        except Exception as e:  # noqa: BLE001
            reply = f"⚠️ goal 제출 실패: {e}"
            self.tg.send(chat_id, reply)
            return reply
        update_conversation(chat_id, last_task_id=task_id)
        self.tg.send(chat_id, f"🦞 접수했습니다 (task {task_id}). 작업 중…")
        task = self.core.wait_task(task_id, self.task_timeout)
        reply = summarize_result(task)
        update_conversation(chat_id, add_agent=reply)
        self.tg.send(chat_id, reply)
        return reply

    def run(self) -> None:
        log.info("[telegram] 브리지 시작 — 폴링 대기")
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
                log.warning(f"[telegram] 네트워크 오류, 재시도: {e}")
                time.sleep(5)
            except Exception as e:  # noqa: BLE001
                log.warning(f"[telegram] 루프 오류: {e}")
                time.sleep(3)


# ───────────────────────── 드라이런 셀프테스트 ─────────────────────────
def _dry_run() -> int:
    """토큰/서버 없이 브리지 로직을 검증한다. 가짜 텔레그램·가짜 agent-core를 주입."""
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
            return {"status": "done", "events": [{"kind": "done", "message": "완료: 결과 요약입니다."}]}

    # 임시 대화파일로 격리
    global CONV_FILE
    orig = CONV_FILE
    CONV_FILE = config.STATE_DIR / "conversations.selftest.json"
    if CONV_FILE.exists():
        CONV_FILE.unlink()
    ok = True
    try:
        core = FakeCore()
        br = Bridge(FakeTelegram(), core, allowed=["123"])

        # 1) 권한 없는 발신자는 차단
        r = br.handle_message("999", "안녕")
        assert "허용되지 않은" in r, "화이트리스트 차단 실패"

        # 2) 허용 발신자: 접수 메시지 + 결과 회신 2건
        sent.clear()
        r = br.handle_message("123", "오늘 일정 정리해줘")
        assert any("접수" in t for _, t in sent), "접수 안내 누락"
        assert "완료" in r, "결과 회신 누락"

        # 3) 대화 영속 + 맥락 이어가기
        conv = get_conversation("123")
        assert conv["last_task_id"] == "task-test-001", "task_id 영속 실패"
        assert len(conv["history"]) >= 2, "history 누적 실패"
        r2 = br.handle_message("123", "그럼 내일은?")
        assert "[이전 대화 맥락]" in core.last_goal, "맥락 이어가기 실패(이전 대화 미주입)"

        print("✅ DRY-RUN PASS — 권한게이트·접수회신·결과요약·대화영속·맥락이어가기 모두 정상")
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
        print("TELEGRAM_BOT_TOKEN 이 .env에 없습니다. @BotFather로 봇을 만들고 토큰을 넣으세요.")
        print("(토큰 없이 로직만 보려면: python channels/telegram_bridge.py --dry-run)")
        return 2
    if not config.TELEGRAM_ALLOWED_CHAT_IDS:
        log.warning("[telegram] ⚠️ ALLOWED_CHAT_IDS 미설정 — 누구나 에이전트를 구동할 수 있습니다(개발용).")
    br = Bridge(
        RealTelegram(token),
        RealAgentCore(config.AGENT_CORE_URL),
        allowed=config.TELEGRAM_ALLOWED_CHAT_IDS,
    )
    br.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
