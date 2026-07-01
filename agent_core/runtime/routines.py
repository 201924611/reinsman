"""Periodic routine registry — the list of things the main orchestrator does on a schedule.

Design: in agent-core, one goal = one independent orchestrator + its subtree.
So the pattern of "on each cycle, spin up an agent per task, hand it a plan, and let
that agent create and run subagents" is implemented directly by "maintaining a list of
routines (= plans) and submitting each one as a goal on every cycle." The main process
exists only as a thin dispatcher (scheduler).

Persisted to state/routines.json.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta

from agent_core import config



def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class Routine:
    id: str
    name: str
    prompt: str                       # Plan to hand to the agent (= goal)
    interval_hours: float = 24.0      # Cycle length (hours). Default 24h
    enabled: bool = True
    created_at: str = field(default_factory=lambda: _iso(_now()))
    next_run: str | None = None       # Next scheduled run (ISO)
    last_run: str | None = None
    runs: int = 0
    last_task_id: str | None = None


class RoutineStore:
    def __init__(self) -> None:
        self._path = config.STATE_DIR / "routines.json"
        self._lock = threading.Lock()
        self._items: dict[str, Routine] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for rid, d in raw.items():
                    self._items[rid] = Routine(**d)
            except Exception:  # noqa: BLE001
                self._items = {}

    def _flush(self) -> None:
        data = {rid: asdict(r) for rid, r in self._items.items()}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ---- CRUD ----
    def add(self, name: str, prompt: str, interval_hours: float = 24.0,
            first_delay_hours: float | None = None) -> Routine:
        with self._lock:
            rid = "rt-" + uuid.uuid4().hex[:8]
            delay = interval_hours if first_delay_hours is None else first_delay_hours
            r = Routine(id=rid, name=name, prompt=prompt, interval_hours=interval_hours,
                        next_run=_iso(_now() + timedelta(hours=delay)))
            self._items[rid] = r
            self._flush()
            return r

    def get(self, rid: str) -> Routine | None:
        with self._lock:
            return self._items.get(rid)

    def list(self) -> list[Routine]:
        with self._lock:
            return sorted(self._items.values(), key=lambda r: r.created_at)

    def remove(self, rid: str) -> bool:
        with self._lock:
            ok = self._items.pop(rid, None) is not None
            if ok:
                self._flush()
            return ok

    def toggle(self, rid: str, enabled: bool | None = None) -> Routine | None:
        with self._lock:
            r = self._items.get(rid)
            if not r:
                return None
            r.enabled = (not r.enabled) if enabled is None else enabled
            self._flush()
            return r

    def update(self, rid: str, **changes) -> Routine | None:
        with self._lock:
            r = self._items.get(rid)
            if not r:
                return None
            for k, v in changes.items():
                if hasattr(r, k) and v is not None:
                    setattr(r, k, v)
            self._flush()
            return r

    # ---- Scheduling ----
    def due(self, now: datetime | None = None) -> list[Routine]:
        """Routines that should run now (enabled + next_run has arrived)."""
        now = now or _now()
        out = []
        with self._lock:
            for r in self._items.values():
                if not r.enabled or not r.next_run:
                    continue
                try:
                    if datetime.fromisoformat(r.next_run) <= now:
                        out.append(r)
                except Exception:  # noqa: BLE001
                    continue
        return out

    def mark_ran(self, rid: str, task_id: str, now: datetime | None = None) -> None:
        """Call right after submitting a run — updates last_run and advances next_run to the next cycle (prevents duplicate firing)."""
        now = now or _now()
        with self._lock:
            r = self._items.get(rid)
            if not r:
                return
            r.last_run = _iso(now)
            r.runs += 1
            r.last_task_id = task_id
            r.next_run = _iso(now + timedelta(hours=r.interval_hours))
            self._flush()


# Global singleton instance
store = RoutineStore()
