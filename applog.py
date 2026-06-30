"""오케스트레이션 로깅.

모든 오케스트레이션 동작(목적 수신/사고/하위에이전트 생성/결과/완료/에러)을
로그 파일에 남긴다.
- 중앙 로그: logs/orchestration.log (회전 로그, 모든 작업 통합)
- 작업별 로그: logs/task-<task_id>.log (해당 목적 하나의 전체 흐름)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import config

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("agentcore")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = RotatingFileHandler(
        config.LOGS_DIR / "orchestration.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    _logger = logger
    return logger


def log_event(task_id: str, kind: str, message: str) -> None:
    """하나의 오케스트레이션 이벤트를 중앙 로그 + 작업별 로그에 기록한다."""
    get_logger().info(f"[{task_id}] {kind}: {message}")
    try:
        ts = datetime.now(timezone.utc).isoformat()
        path = config.LOGS_DIR / f"task-{task_id}.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} [{kind}] {message}\n")
    except Exception:
        # 로깅 실패가 작업을 막아선 안 된다
        pass
