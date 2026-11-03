from __future__ import annotations

import logging
import threading
from pathlib import Path

from _state.database import Database
from _state.importer import DOC_MUTES

logger = logging.getLogger("relay.push.mutes")


class MuteStore:

    def __init__(self, state_dir: Path | str):
        self._db = Database.for_dir(state_dir)
        self._lock = threading.RLock()
        self._mutes: dict[str, set[str]] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = self._db.get_doc(DOC_MUTES)
            if raw is not None:
                self._mutes = {str(k): set(map(str, v)) for k, v in raw.items()}
        except (ValueError, AttributeError, TypeError) as exc:
            logger.warning("push mutes unreadable (%s); starting empty", exc)

    def _save(self) -> None:
        self._db.put_doc(DOC_MUTES, {alert: sorted(keys) for alert, keys in self._mutes.items() if keys})

    def mute(self, alert_id: str, key: str) -> None:
        with self._lock:
            self._mutes.setdefault(alert_id, set()).add(key)
            self._save()

    def unmute(self, alert_id: str, key: str) -> None:
        with self._lock:
            keys = self._mutes.get(alert_id)
            if keys and key in keys:
                keys.discard(key)
                if not keys:
                    del self._mutes[alert_id]
                self._save()

    def is_muted(self, alert_id: str, key: str) -> bool:
        with self._lock:
            return key in self._mutes.get(alert_id, ())

    def muted_alerts(self, key: str) -> list[str]:
        with self._lock:
            return sorted(a for a, keys in self._mutes.items() if key in keys)

    def retain_only(self, active_alert_ids: set[str]) -> None:
        with self._lock:
            stale = [a for a in self._mutes if a not in active_alert_ids]
            if stale:
                for alert_id in stale:
                    del self._mutes[alert_id]
                self._save()
