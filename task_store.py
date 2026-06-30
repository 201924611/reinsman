"""작업(목적) 상태 저장소.

중앙 에이전트가 받는 각 '목적'을 하나의 Task로 추적한다.
24h 구동 중 서버가 재시작돼도 상태가 남도록 JSON 파일에 영속화한다.
스레드/코루틴 동시 접근을 막기 위해 Lock으로 보호한다.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    id: str
    goal: str                       # 사용자가 준 목적
    status: str = "queued"          # queued | running | done | incomplete | error | cancelled
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    result: str | None = None       # 최종 결과 요약
    error: str | None = None
    session_id: str | None = None   # SDK 세션 ID (이어하기 resume 용)
    num_turns: int | None = None    # 마지막 실행에서 소비한 턴 수
    events: list[dict[str, Any]] = field(default_factory=list)  # 진행 로그(서브에이전트 호출 등)

    def log(self, kind: str, message: str, **extra: Any) -> None:
        self.events.append({"ts": _now(), "kind": kind, "message": message, **extra})
        self.updated_at = _now()


class TaskStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config.STATE_DIR / "tasks.json")
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for tid, d in raw.items():
                    self._tasks[tid] = Task(**d)
            except Exception:
                # 손상된 상태 파일은 무시하고 새로 시작
                self._tasks = {}

    def _flush(self) -> None:
        data = {tid: asdict(t) for tid, t in self._tasks.items()}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ---- public API ----
    def create(self, task_id: str, goal: str) -> Task:
        with self._lock:
            t = Task(id=task_id, goal=goal)
            self._tasks[task_id] = t
            self._flush()
            return t

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def update(self, task_id: str, **changes: Any) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            for k, v in changes.items():
                setattr(t, k, v)
            t.updated_at = _now()
            self._flush()

    def append_event(self, task_id: str, kind: str, message: str, **extra: Any) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            t.log(kind, message, **extra)
            self._flush()
        # 락 밖에서 로그 파일에도 남긴다 (오케스트레이션 동작 기록)
        from applog import log_event
        log_event(task_id, kind, message)


# 전역 단일 인스턴스
store = TaskStore()
