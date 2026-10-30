from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from _lib.atomic_write import atomic_write_json

logger = logging.getLogger("relay.alerts.ack")

ACKS_FILE = ("push", "acks.json")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AckStore:

    def __init__(self, state_dir: Path | str, clock: Callable[[], float] = time.time):
        self._state_dir = Path(state_dir)
        self._clock = clock
        self._lock = threading.RLock()
        self._acks: dict[str, dict] = {}
        self._gone_since: dict[str, float] = {}
        self.version = 0
        self._load()

    def _load(self) -> None:
        path = self._state_dir.joinpath(*ACKS_FILE)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._acks = {
                str(k): {"acknowledged_at": float(v["acknowledged_at"]), "acknowledged_by": str(v["acknowledged_by"])}
                for k, v in raw["acks"].items()
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("alert acks file unreadable (%s); starting empty", exc)

    def _save(self) -> None:
        atomic_write_json(self._state_dir, *ACKS_FILE, data={"acks": self._acks})

    def acknowledge(self, alert_id: str, by: str) -> dict:
        with self._lock:
            if alert_id not in self._acks:
                self._acks[alert_id] = {"acknowledged_at": self._clock(), "acknowledged_by": by}
                self.version += 1
                self._save()
            return dict(self._acks[alert_id])

    def is_acked(self, alert_id: str) -> bool:
        with self._lock:
            return alert_id in self._acks

    # an ack survives a gap shorter than hold_seconds so a flapping alert is not re-announced as new
    def retain(self, active_ids: set[str], hold_seconds: float) -> None:
        with self._lock:
            now = self._clock()
            changed = False
            for alert_id in list(self._acks):
                if alert_id in active_ids:
                    self._gone_since.pop(alert_id, None)
                elif now - self._gone_since.setdefault(alert_id, now) >= hold_seconds:
                    del self._acks[alert_id]
                    self._gone_since.pop(alert_id, None)
                    changed = True
            if changed:
                self.version += 1
                self._save()

    def apply(self, alerts: list[dict]) -> list[dict]:
        with self._lock:
            for alert in alerts:
                record = self._acks.get(alert["alertId"])
                if record is not None:
                    alert["acknowledged"] = True
                    alert["acknowledged_by"] = record["acknowledged_by"]
                    alert["acknowledged_at"] = _iso(record["acknowledged_at"])
        return alerts


def apply_acks(app_state, alerts: list[dict]) -> list[dict]:
    store: Optional[AckStore] = getattr(app_state, "alert_acks", None)
    return alerts if store is None else store.apply(alerts)
