from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AlertKey:
    kind: str
    root_cause_id: str

    def alert_id(self) -> str:
        return f"{self.kind}:{self.root_cause_id}"


class OpenIncidentIndex:

    def __init__(self) -> None:
        self._open: dict[str, dict] = {}

    def is_open(self, key: AlertKey) -> bool:
        return key.alert_id() in self._open

    def get(self, key: AlertKey) -> Optional[dict]:
        return self._open.get(key.alert_id())

    def claim(self, key: AlertKey, record: dict) -> None:
        self._open[key.alert_id()] = record

    def release(self, key: AlertKey) -> Optional[dict]:
        return self._open.pop(key.alert_id(), None)

    def open_ids(self) -> set[str]:
        return set(self._open.keys())

    def items(self) -> list[tuple[str, dict]]:
        return list(self._open.items())

    def __len__(self) -> int:
        return len(self._open)
