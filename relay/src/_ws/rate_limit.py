from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _ClientWindow:
    attempts: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class AckRateLimiter:

    def __init__(self, attempts: int, window_seconds: int, lockout_seconds: int, now: callable = time.monotonic):
        self._attempts = attempts
        self._window_seconds = window_seconds
        self._lockout_seconds = lockout_seconds
        self._now = now
        self._clients: dict[str, _ClientWindow] = {}

    @classmethod
    def from_settings(cls, settings, now: callable = time.monotonic) -> "AckRateLimiter":
        return cls(
            attempts=settings.pin_rate_limit_attempts,
            window_seconds=settings.pin_rate_limit_window_seconds,
            lockout_seconds=settings.pin_rate_limit_lockout_seconds,
            now=now,
        )

    def allow(self, client_id: str) -> bool:
        now = self._now()
        win = self._clients.setdefault(client_id, _ClientWindow())

        if now < win.locked_until:
            return False

        win.attempts = [t for t in win.attempts if now - t <= self._window_seconds]
        win.attempts.append(now)

        if len(win.attempts) > self._attempts:
            win.locked_until = now + self._lockout_seconds
            win.attempts = []
            return False

        return True

    def is_locked(self, client_id: str) -> bool:
        win = self._clients.get(client_id)
        return win is not None and self._now() < win.locked_until

    def record_failure(self, client_id: str) -> None:
        now = self._now()
        win = self._clients.setdefault(client_id, _ClientWindow())
        win.attempts = [t for t in win.attempts if now - t <= self._window_seconds]
        win.attempts.append(now)
        if len(win.attempts) >= self._attempts:
            win.locked_until = now + self._lockout_seconds
            win.attempts = []
