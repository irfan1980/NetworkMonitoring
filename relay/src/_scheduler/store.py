from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from _scheduler.model import Schedule, ScheduleInput
from _state.database import Database
from _state.importer import META_NEXT_SCHEDULE_ID

logger = logging.getLogger("relay.scheduler.store")


class ScheduleStore:

    def __init__(self, state_dir: Path | str):
        self._db = Database.for_dir(state_dir)
        self._schedules: dict[int, Schedule] = {}
        self._next_id = 1
        self._listeners: list[Callable[[], None]] = []
        self._load()

    def reload(self) -> None:
        self._schedules = {}
        self._next_id = 1
        self._load()

    def _load(self) -> None:
        try:
            schedules = [Schedule.model_validate_json(body) for (body,) in self._db.query("SELECT body FROM schedules ORDER BY id")]
            row = self._db.query("SELECT value FROM meta WHERE key=?", (META_NEXT_SCHEDULE_ID,))
            declared = int(row[0][0]) if row else 1
        except (ValidationError, ValueError) as exc:
            raise RuntimeError(f"schedules in {self._db.path} are unreadable: {exc}") from exc
        self._schedules = {s.id: s for s in schedules}
        self._next_id = max(declared, max(self._schedules, default=0) + 1)

    def _save(self) -> None:
        rows = [(s.id, json.dumps(s.model_dump(mode="json"))) for s in self.list()]
        next_id = self._next_id

        def apply(conn) -> None:
            conn.execute("DELETE FROM schedules")
            conn.executemany("INSERT INTO schedules(id, body) VALUES (?, ?)", rows)
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (META_NEXT_SCHEDULE_ID, str(next_id)))

        self._db.write(apply)
        for listener in list(self._listeners):
            listener()

    def on_change(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def list(self) -> list[Schedule]:
        return sorted(self._schedules.values(), key=lambda s: s.id)

    def get(self, schedule_id: int) -> Optional[Schedule]:
        return self._schedules.get(schedule_id)

    def create(self, data: ScheduleInput) -> Schedule:
        schedule = Schedule(id=self._next_id, **data.model_dump())
        self._schedules[schedule.id] = schedule
        self._next_id += 1
        self._save()
        return schedule

    def replace(self, schedule_id: int, data: ScheduleInput) -> Optional[Schedule]:
        if schedule_id not in self._schedules:
            return None
        schedule = Schedule(id=schedule_id, **data.model_dump())
        self._schedules[schedule_id] = schedule
        self._save()
        return schedule

    def set_enabled(self, schedule_id: int, enabled: bool) -> Optional[Schedule]:
        current = self._schedules.get(schedule_id)
        if current is None:
            return None
        updated = current.model_copy(update={"enabled": enabled})
        self._schedules[schedule_id] = updated
        self._save()
        return updated

    def delete(self, schedule_id: int) -> bool:
        if self._schedules.pop(schedule_id, None) is None:
            return False
        self._save()
        return True
