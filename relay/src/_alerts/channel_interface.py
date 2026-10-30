from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from _contract.schema import DispatchSettings


@dataclass(frozen=True)
class NotConfigured:

    reason: str


class ChannelModule(Protocol):
    CHANNEL: str

    def is_configured(self, settings: "DispatchSettings") -> Optional[NotConfigured]:
        ...

    def recipients_of(self, settings: "DispatchSettings") -> list[str]:
        ...

    def send(self, alert_text: str, recipients: list[str], settings: "DispatchSettings") -> None:
        ...
