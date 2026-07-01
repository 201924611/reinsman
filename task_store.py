"""Task (goal) state store.

Tracks each 'goal' the central agent receives as a single Task.
Persists state to a JSON file so it survives server restarts during 24h operation.
Guarded by a Lock to prevent concurrent thread/coroutine access.
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
    goal: str                       # The goal given by the user
    status: str = "queued"          # queued | running | done | incomplete | error | cancelled
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    result: str | None = None       # Final result summary
    error: str | None = None
    session_id: str | None = None   # SDK session ID (used for resuming)
    num_turns: int | None = None    # Turns consumed in the last run
    events: list[dict[str, Any]] = field(default_factory=list)  # Progress log (subagent calls, etc.)

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
                # Ignore a corrupted state file and start fresh
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
        # Also write to the log file outside the lock (records orchestration activity)
        from applog import log_event
        log_event(task_id, kind, message)


# Global singleton instance
store = TaskStore()
