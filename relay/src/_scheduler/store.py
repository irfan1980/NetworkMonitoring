from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from _lib.atomic_write import atomic_write_json
from _scheduler.model import Schedule, ScheduleInput

logger = logging.getLogger("relay.scheduler.store")

SCHEDULES_FILE = ("scheduler", "schedules.json")


class ScheduleStore:

    def __init__(self, state_dir: Path | str):
        self._state_dir = Path(state_dir)
        self._schedules: dict[int, Schedule] = {}
        self._next_id = 1
        self._listeners: list[Callable[[], None]] = []
        self._load()

    def reload(self) -> None:
        self._schedules = {}
        self._next_id = 1
        self._load()

    def _path(self) -> Path:
        return self._state_dir.joinpath(*SCHEDULES_FILE)

    def _load(self) -> None:
        path = self._path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            schedules = [Schedule.model_validate(item) for item in raw.get("schedules", [])]
        except (OSError, json.JSONDecodeError, ValidationError, AttributeError) as exc:
            backup = path.with_suffix(".corrupt")
            logger.error("schedules file unreadable (%s); moved to %s and starting empty", exc, backup)
            try:
                path.replace(backup)
            except OSError:
                pass
            return
        self._schedules = {s.id: s for s in schedules}
        self._next_id = max(int(raw.get("next_id", 1)), max(self._schedules, default=0) + 1)

    def _save(self) -> None:
        atomic_write_json(
            self._state_dir,
            *SCHEDULES_FILE,
            data={
                "next_id": self._next_id,
                "schedules": [s.model_dump(mode="json") for s in self.list()],
            },
        )
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
