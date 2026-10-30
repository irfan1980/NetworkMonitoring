from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from _lib.atomic_write import atomic_write_json
from _lib.paths import under_state_dir

ALERT_STATE_FILENAME = "alert_state.json"


def _default_now() -> float:
    return time.time()


@dataclass
class AlertRecord:
    alert_id: str
    kind: str
    severity: int
    raised_at: float
    last_repeat_at: float
    repeat_count: int = 0
    escalated: bool = False
    from_status: str = "unknown"
    active: bool = True
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "kind": self.kind,
            "severity": self.severity,
            "raised_at": self.raised_at,
            "last_repeat_at": self.last_repeat_at,
            "repeat_count": self.repeat_count,
            "escalated": self.escalated,
            "from_status": self.from_status,
            "active": self.active,
            "acknowledged": self.acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AlertRecord":
        return cls(**d)


class AlertStateStore:

    def __init__(
        self,
        state_dir: Path | str,
        now: Callable[[], float] = _default_now,
        retry_attempts: int = 5,
        backoff_ms_per_attempt: int = 10,
    ):
        self._state_dir = state_dir
        self._now = now
        self._retry_attempts = retry_attempts
        self._backoff_ms_per_attempt = backoff_ms_per_attempt
        self._alerts: dict[str, AlertRecord] = {}
        self._load()

    @classmethod
    def from_settings(cls, settings, state_dir: Path | str, now: Callable[[], float] = _default_now) -> "AlertStateStore":
        return cls(
            state_dir,
            now=now,
            retry_attempts=settings.atomic_write_retry_attempts,
            backoff_ms_per_attempt=settings.atomic_write_retry_backoff_ms_per_attempt,
        )

    def _load(self) -> None:
        path = under_state_dir(self._state_dir, ALERT_STATE_FILENAME)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for entry in raw.get("alerts", []):
            try:
                record = AlertRecord.from_dict(entry)
            except TypeError:
                continue
            self._alerts[record.alert_id] = record

    def _persist(self) -> None:
        atomic_write_json(
            self._state_dir, ALERT_STATE_FILENAME,
            data={"alerts": [a.to_dict() for a in self._alerts.values()]},
            retry_attempts=self._retry_attempts,
            backoff_ms_per_attempt=self._backoff_ms_per_attempt,
        )

    def raise_or_repeat(
        self,
        alert_id: str,
        kind: str,
        severity: int,
        from_status: str,
        *,
        repeat_minutes_sev1_2: int,
        repeat_hours_sev3_5: int,
        escalate_after_repeats: int,
    ) -> tuple[AlertRecord, bool, bool]:
        now = self._now()
        existing = self._alerts.get(alert_id)
        if existing is None or not existing.active:
            record = AlertRecord(
                alert_id=alert_id, kind=kind, severity=severity,
                raised_at=now, last_repeat_at=now, repeat_count=0,
                escalated=False, from_status=from_status, active=True,
            )
            self._alerts[alert_id] = record
            self._persist()
            return record, True, True

        interval_seconds = (repeat_minutes_sev1_2 * 60) if severity <= 2 else (repeat_hours_sev3_5 * 3600)
        elapsed = now - existing.last_repeat_at
        should_dispatch = elapsed >= interval_seconds
        if should_dispatch:
            existing.last_repeat_at = now
            existing.repeat_count += 1
            if existing.repeat_count >= escalate_after_repeats:
                existing.escalated = True
            self._persist()
        return existing, False, should_dispatch

    def resolve(self, alert_id: str) -> Optional[AlertRecord]:
        record = self._alerts.get(alert_id)
        if record is None or not record.active:
            return None
        record.active = False
        self._persist()
        return record

    def acknowledge(self, alert_id: str, acknowledged_by: str) -> Optional[AlertRecord]:
        record = self._alerts.get(alert_id)
        if record is None or not record.active:
            return None
        record.acknowledged = True
        record.acknowledged_by = acknowledged_by
        record.acknowledged_at = self._now()
        self._persist()
        return record

    def is_acknowledged(self, alert_id: str) -> bool:
        record = self._alerts.get(alert_id)
        return record is not None and record.acknowledged

    def is_active(self, alert_id: str) -> bool:
        record = self._alerts.get(alert_id)
        return record is not None and record.active

    def get(self, alert_id: str) -> Optional[AlertRecord]:
        return self._alerts.get(alert_id)

    def active_alert_ids(self) -> set[str]:
        return {a.alert_id for a in self._alerts.values() if a.active}


@dataclass(frozen=True)
class BusIncidentCooldown:
    alert_id: str
    since: float


def bus_incident_cooldown_elapsed(
    cooldown: Optional[BusIncidentCooldown],
    alert_id: str,
    now: float,
    cooldown_minutes: int,
) -> bool:
    if cooldown is None or cooldown.alert_id != alert_id:
        return True
    return (now - cooldown.since) >= (cooldown_minutes * 60)
