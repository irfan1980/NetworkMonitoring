from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from _lib.auth import timing_safe_equal_str
from _ws.rate_limit import AckRateLimiter

logger = logging.getLogger("relay.ops.silence")
# silences are in-memory only: deliberately not persisted via atomic_write_json

PIN_REJECTED_REASON = "invalid_pin"
PIN_RATE_LIMITED_REASON = "rate_limited"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Silence:
    id: int
    reason: str
    created_at: str
    expires_at: str
    created_by: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "reason": self.reason,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "created_by": self.created_by,
        }


class PinRejected(Exception):
    pass


class PinRateLimited(Exception):
    pass


class SilenceStore:

    def __init__(
        self,
        pin_rate_limiter: AckRateLimiter,
        now: Callable[[], datetime] = _now_utc,
    ):
        self._pin_rate_limiter = pin_rate_limiter
        self._now = now
        self._next_id = 1
        self._silences: dict[int, Silence] = {}

    def verify_pin(self, client_id: str, supplied_pin: Optional[str], configured_pin: str) -> None:
        if self._pin_rate_limiter.is_locked(client_id):
            logger.warning("ops/silence PIN attempt rate-limited for client_id=%s", client_id)
            raise PinRateLimited(PIN_RATE_LIMITED_REASON)
        if not supplied_pin or not timing_safe_equal_str(supplied_pin, configured_pin):
            self._pin_rate_limiter.record_failure(client_id)
            logger.warning("ops/silence PIN attempt rejected for client_id=%s", client_id)
            raise PinRejected(PIN_REJECTED_REASON)

    def create(self, reason: str, minutes: float, created_by: str) -> Silence:
        now = self._now()
        silence = Silence(
            id=self._next_id,
            reason=reason,
            created_at=_iso(now),
            expires_at=_iso(now + timedelta(minutes=minutes)),
            created_by=created_by,
        )
        self._silences[silence.id] = silence
        self._next_id += 1
        logger.info("ops/silence created id=%s reason=%r minutes=%s by=%s", silence.id, reason, minutes, created_by)
        return silence

    def list_active(self) -> list[Silence]:
        now = self._now()
        return sorted(
            (s for s in self._silences.values() if datetime.strptime(s.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) > now),
            key=lambda s: s.created_at,
        )

    def delete(self, silence_id: int, deleted_by: str) -> bool:
        removed = self._silences.pop(silence_id, None) is not None
        logger.info("ops/silence delete id=%s found=%s by=%s", silence_id, removed, deleted_by)
        return removed
