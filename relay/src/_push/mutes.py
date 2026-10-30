from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from _lib.atomic_write import atomic_write_json

logger = logging.getLogger("relay.push.mutes")

MUTES_FILE = ("push", "mutes.json")


class MuteStore:

    def __init__(self, state_dir: Path | str):
        self._state_dir = Path(state_dir)
        self._lock = threading.RLock()
        self._mutes: dict[str, set[str]] = {}
        self._load()

    def _load(self) -> None:
        path = self._state_dir.joinpath(*MUTES_FILE)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._mutes = {str(k): set(map(str, v)) for k, v in raw.items()}
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            logger.warning("push mutes file unreadable (%s); starting empty", exc)

    def _save(self) -> None:
        atomic_write_json(
            self._state_dir, *MUTES_FILE,
            data={alert: sorted(keys) for alert, keys in self._mutes.items() if keys},
        )

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
