from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Optional

from _push.schedule_window import WindowError, is_active, parse_schedule
from _state.database import Database
from _state.importer import DOC_DOWNTIME

logger = logging.getLogger("relay.push.downtime")

NOTE_LIMIT = 500


def parse_window(raw, window_id: str) -> dict:
    schedule = parse_schedule(raw)
    machine = raw.get("machine")
    service = raw.get("service")
    note = raw.get("note", "")
    if not isinstance(machine, str) or not machine.strip():
        raise WindowError("machine must be a non-empty string")
    if service is not None and (not isinstance(service, str) or not service.strip()):
        raise WindowError("service must be a non-empty string or null")
    if not isinstance(note, str) or len(note) > NOTE_LIMIT:
        raise WindowError(f"note must be a string of at most {NOTE_LIMIT} characters")
    return {"id": window_id, "machine": machine.strip(), "service": service.strip() if service else None, **schedule, "note": note}


class DowntimeStore:

    def __init__(self, state_dir: Path | str):
        self._db = Database.for_dir(state_dir)
        self._lock = threading.RLock()
        self._windows: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = self._db.get_doc(DOC_DOWNTIME)
            if raw is not None:
                self._windows = [parse_window(w, str(w["id"])) for w in raw["windows"]]
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("downtime unreadable (%s); starting empty", exc)
            self._windows = []

    def _save(self) -> None:
        self._db.put_doc(DOC_DOWNTIME, {"windows": self._windows})

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(w, days=list(w["days"])) for w in self._windows]

    def create(self, raw: dict) -> dict:
        with self._lock:
            window = parse_window(raw, uuid.uuid4().hex[:12])
            self._windows.append(window)
            self._save()
            return dict(window)

    def update(self, window_id: str, raw: dict) -> Optional[dict]:
        with self._lock:
            index = next((i for i, w in enumerate(self._windows) if w["id"] == window_id), None)
            if index is None:
                return None
            window = parse_window(raw, window_id)
            self._windows[index] = window
            self._save()
            return dict(window)

    def delete(self, window_id: str) -> bool:
        with self._lock:
            kept = [w for w in self._windows if w["id"] != window_id]
            if len(kept) == len(self._windows):
                return False
            self._windows = kept
            self._save()
            return True

    def active_ids(self, machine: Optional[str], service: Optional[str], now_epoch: float) -> list[str]:
        if machine is None:
            return []
        with self._lock:
            windows = list(self._windows)
        return [
            w["id"] for w in windows
            if w["machine"] == machine and (w["service"] is None or w["service"] == service) and is_active(w, now_epoch)
        ]

    def is_down(self, machine: Optional[str], service: Optional[str], now_epoch: float) -> bool:
        return bool(self.active_ids(machine, service, now_epoch))
