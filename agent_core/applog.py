"""Orchestration logging.

Records every orchestration action (goal received / reasoning / subagent creation /
result / completion / error) to log files.
- Central log: logs/orchestration.log (rotating log, all tasks combined)
- Per-task log: logs/task-<task_id>.log (the full flow of a single goal)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from agent_core import config


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
    """Record a single orchestration event to the central log and the per-task log."""
    get_logger().info(f"[{task_id}] {kind}: {message}")
    try:
        ts = datetime.now(timezone.utc).isoformat()
        path = config.LOGS_DIR / f"task-{task_id}.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} [{kind}] {message}\n")
    except Exception:
        # A logging failure must never block the task
        pass
