from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PollStats:

    def __init__(self, window_samples: int = 720):
        self.window_samples = window_samples
        self._durations_ms: deque[float] = deque(maxlen=window_samples)
        self._last_cycle_ts: Optional[str] = None

    def record(self, duration_ms: float) -> None:
        self._durations_ms.append(duration_ms)
        self._last_cycle_ts = _utc_now_iso()

    def snapshot(self) -> dict:
        n = len(self._durations_ms)
        if n == 0:
            return {
                "samples": 0,
                "window_samples": self.window_samples,
                "avg_ms": None,
                "min_ms": None,
                "max_ms": None,
                "last_cycle_ts": None,
            }
        values = list(self._durations_ms)
        return {
            "samples": n,
            "window_samples": self.window_samples,
            "avg_ms": sum(values) / n,
            "min_ms": min(values),
            "max_ms": max(values),
            "last_cycle_ts": self._last_cycle_ts,
        }
