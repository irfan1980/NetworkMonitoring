from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


def format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


@dataclass
class _KeyState:
    down_since: Optional[float] = None
    last_logged: Optional[float] = None


class TransitionLogger:

    def __init__(self, remind_after_seconds: float = 300.0, now: Callable[[], float] = time.monotonic):
        self.remind_after_seconds = remind_after_seconds
        self._now = now
        self._states: dict[str, _KeyState] = {}

    def ok(self, logger: logging.Logger, key: str, label: str) -> None:
        state = self._states.pop(key, None)
        if state is not None and state.down_since is not None:
            elapsed = self._now() - state.down_since
            logger.info("%s reachable again after %s", label, format_duration(elapsed))

    def down(
        self,
        logger: logging.Logger,
        key: str,
        message: str,
        level: int = logging.WARNING,
    ) -> None:
        now = self._now()
        state = self._states.get(key)
        if state is None:
            state = _KeyState(down_since=now, last_logged=now)
            self._states[key] = state
            logger.log(level, "%s", message)
            return

        if now - (state.last_logged or now) >= self.remind_after_seconds:
            elapsed = now - (state.down_since or now)
            state.last_logged = now
            logger.log(level, "%s (still down after %s)", message, format_duration(elapsed))
