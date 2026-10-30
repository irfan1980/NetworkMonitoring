from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _contract.schema import Settings


def _default_now_ms() -> float:
    return time.monotonic() * 1000.0


@dataclass
class ObjectConfirmState:
    object_id: str
    confirmed: ReadStatus = ReadStatus.OK
    has_real_baseline: bool = False
    pending_status: Optional[ReadStatus] = None
    pending_streak: int = 0
    pending_since_ms: Optional[float] = None
    unread_since_ms: Optional[float] = None


class DownConfirm:

    def __init__(
        self,
        down_confirm_soft_min_ms: int = 15000,
        unread_min_ms: int = 15000,
        now_ms: Callable[[], float] = _default_now_ms,
    ):
        self.down_confirm_soft_min_ms = down_confirm_soft_min_ms
        self.unread_min_ms = unread_min_ms
        self._now_ms = now_ms
        self._states: dict[str, ObjectConfirmState] = {}

    @classmethod
    def from_settings(cls, settings: "Settings", now_ms: Callable[[], float] = _default_now_ms) -> "DownConfirm":
        return cls(
            down_confirm_soft_min_ms=settings.down_confirm_soft_min_ms,
            unread_min_ms=settings.unread_min_ms,
            now_ms=now_ms,
        )

    def confirmed_status(self, object_id: str) -> ReadStatus:
        state = self._states.get(object_id)
        return state.confirmed if state else ReadStatus.OK

    def unread_streak_ms(self, object_id: str) -> float:
        state = self._states.get(object_id)
        if state is None or state.unread_since_ms is None:
            return 0.0
        return self._now_ms() - state.unread_since_ms

    def is_stable_unread(self, object_id: str) -> bool:
        return self.unread_streak_ms(object_id) >= self.unread_min_ms

    def is_currently_unread(self, object_id: str) -> bool:
        state = self._states.get(object_id)
        return state is not None and state.unread_since_ms is not None

    def observe(self, object_id: str, status: ReadStatus) -> ReadStatus:
        if status == ReadStatus.BLOCKED:
            raise ValueError("DownConfirm.observe() never takes BLOCKED — that is assigned by Group 4's attribution layer, not a raw poll observation")

        state = self._states.setdefault(object_id, ObjectConfirmState(object_id))
        now = self._now_ms()

        if status == ReadStatus.UNREAD:
            if state.unread_since_ms is None:
                state.unread_since_ms = now
            return state.confirmed

        first_observation = not state.has_real_baseline
        state.has_real_baseline = True
        state.unread_since_ms = None

        if status == state.confirmed:
            state.pending_status = None
            state.pending_streak = 0
            state.pending_since_ms = None
            return state.confirmed

        if state.pending_status != status:
            state.pending_status = status
            state.pending_streak = 1
            state.pending_since_ms = now
        else:
            state.pending_streak += 1

        if status == ReadStatus.DOWN and first_observation:
            state.confirmed = ReadStatus.DOWN
            state.pending_status = None
            state.pending_streak = 0
            state.pending_since_ms = None
            return state.confirmed

        pending_since = state.pending_since_ms if state.pending_since_ms is not None else now
        elapsed_ms = now - pending_since
        if state.pending_streak >= 2 and elapsed_ms >= self.down_confirm_soft_min_ms:
            state.confirmed = status
            state.pending_status = None
            state.pending_streak = 0
            state.pending_since_ms = None

        return state.confirmed
