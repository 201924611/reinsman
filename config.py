"""중앙 설정 — .env 파일과 환경변수에서 값을 읽어온다."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트
ROOT = Path(__file__).resolve().parent

# .env 로드 (있으면)
load_dotenv(ROOT / ".env")

# 디렉터리
WORKSPACE_DIR = ROOT / "workspace"          # 에이전트가 파일 작업을 하는 공간
LOGS_DIR = ROOT / "logs"                    # 로그
STATE_DIR = ROOT / "state"                  # 작업 상태 영속화(JSON)
TEMPLATES_DIR = ROOT / "templates"          # 인용한 프롬프트 템플릿
RUNTIME_AGENTS_DIR = ROOT / "runtime_agents"  # 런타임에 생성되는 서브에이전트 md (종료 시 삭제)

for _d in (WORKSPACE_DIR, LOGS_DIR, STATE_DIR, TEMPLATES_DIR, RUNTIME_AGENTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 인증 / 모델
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-opus-4-8")
SUBAGENT_MODEL = os.getenv("SUBAGENT_MODEL", "claude-sonnet-4-6")

# 서버
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8848"))

# 안전장치 / 동시성
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
MAX_TURNS = int(os.getenv("MAX_TURNS", "40"))
# 턴 한도로 미달 시 자동 이어하기 최대 횟수 (0이면 자동 이어하기 끔)
MAX_AUTO_RESUMES = int(os.getenv("MAX_AUTO_RESUMES", "3"))
PERMISSION_MODE = os.getenv("PERMISSION_MODE", "bypassPermissions")

# 지식 저장소(knowledge/) git 동기화 — 저장 시 자동 커밋/푸시 여부
KB_GIT_SYNC = os.getenv("KB_GIT_SYNC", "false").lower() == "true"
KB_GIT_PUSH = os.getenv("KB_GIT_PUSH", "false").lower() == "true"  # 커밋 후 push (인증 필요)

# 이어하기(resume)에 쓰는 프롬프트.
# RESUME_PROMPT.txt 파일을 자유롭게 편집하면 그 내용이 쓰인다(없으면 아래 기본값).
RESUME_PROMPT_FILE = ROOT / "RESUME_PROMPT.txt"
_DEFAULT_RESUME_PROMPT = (
    "knowledge/와 workspace/에 이미 수집해둔 데이터가 있다. "
    "그걸 먼저 확인하고, 안 끝난 부분부터 이어서 완성해줘."
)


def get_resume_prompt() -> str:
    """이어하기 프롬프트를 RESUME_PROMPT.txt에서 읽는다(없거나 비면 기본값)."""
    try:
        if RESUME_PROMPT_FILE.exists():
            txt = RESUME_PROMPT_FILE.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_RESUME_PROMPT


# 배포(publish) — 완성 산출물을 내보내는 채널 설정
# 산출물은 항상 workspace/_ready_to_publish/ 에 발행 대기 상태로 적재된다(언제나 동작).
READY_DIR = WORKSPACE_DIR / "_ready_to_publish"
# 자동 발행 훅: 이 URL이 설정되면 publish 시 그 webhook으로 POST 한다.
# (Make/Zapier/n8n 등 노코드 자동화에 연결하면 블로그·마켓·SNS로 실제 자동 발행 가능)
PUBLISH_WEBHOOK_URL = os.getenv("PUBLISH_WEBHOOK_URL", "").strip()

# 정산 수취 계좌(.env에만 보관 — git 추적 안 됨). 에이전트 프롬프트에 런타임 주입된다.
PAYOUT_ACCOUNT = os.getenv("PAYOUT_ACCOUNT", "").strip()

# ── 채널(메신저) 연동 — 하네스 'Channels' 기둥 ──
# 텔레그램 브리지(channels/telegram_bridge.py)가 쓰는 값.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# 허용 발신자 chat_id 화이트리스트(쉼표 구분). 비우면 전체 허용(개발용) — 운영 시 반드시 설정.
TELEGRAM_ALLOWED_CHAT_IDS = [
    s.strip() for s in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if s.strip()
]
# 브리지가 호출할 agent-core API 베이스 URL(기본 = 로컬 서버).
AGENT_CORE_URL = os.getenv("AGENT_CORE_URL", f"http://{HOST}:{PORT}")
# 대화(채널) 영속 저장 파일.
CONVERSATIONS_FILE = STATE_DIR / "conversations.json"

# 노션 MCP — 내부 인티그레이션 토큰(.env에만 보관, git 미추적).
# 값이 있으면 오케스트레이터/서브에이전트에 notion 도구가 노출된다(없으면 비활성).
# 토큰 발급: notion.so/my-integrations → Internal Integration → 대상 페이지에 Connection 추가.
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")
