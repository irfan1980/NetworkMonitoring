from __future__ import annotations

from enum import Enum


class ReadStatus(str, Enum):
    OK = "ok"
    DOWN = "down"
    BLOCKED = "blocked"
    UNREAD = "unread"

    def __str__(self) -> str:
        return self.value


EMITTED_STATUSES = frozenset({ReadStatus.OK, ReadStatus.DOWN, ReadStatus.BLOCKED})
