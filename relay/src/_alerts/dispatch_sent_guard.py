from __future__ import annotations

import json
from pathlib import Path

from _lib.atomic_write import atomic_write_json
from _lib.paths import under_state_dir

DISPATCH_SENT_FILENAME = "dispatch_sent.json"

DEDUPED_CHANNELS = frozenset({"sms", "wa_message"})


def _key(channel: str, alert_id: str) -> str:
    return f"{channel}:{alert_id}"


class DispatchSentGuard:

    def __init__(self, state_dir: Path | str, retry_attempts: int = 5, backoff_ms_per_attempt: int = 10):
        self._state_dir = state_dir
        self._retry_attempts = retry_attempts
        self._backoff_ms_per_attempt = backoff_ms_per_attempt
        self._sent: set[str] = set()
        self._load()

    @classmethod
    def from_settings(cls, settings, state_dir: Path | str) -> "DispatchSentGuard":
        return cls(
            state_dir,
            retry_attempts=settings.atomic_write_retry_attempts,
            backoff_ms_per_attempt=settings.atomic_write_retry_backoff_ms_per_attempt,
        )

    def _load(self) -> None:
        path = under_state_dir(self._state_dir, DISPATCH_SENT_FILENAME)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._sent = set(raw.get("sent", []))

    def _persist(self) -> None:
        atomic_write_json(
            self._state_dir, DISPATCH_SENT_FILENAME, data={"sent": sorted(self._sent)},
            retry_attempts=self._retry_attempts, backoff_ms_per_attempt=self._backoff_ms_per_attempt,
        )

    def has_been_sent(self, channel: str, alert_id: str) -> bool:
        if channel not in DEDUPED_CHANNELS:
            return False
        return _key(channel, alert_id) in self._sent

    def mark_sent(self, channel: str, alert_id: str) -> None:
        if channel not in DEDUPED_CHANNELS:
            return
        key = _key(channel, alert_id)
        if key in self._sent:
            return
        self._sent.add(key)
        self._persist()
