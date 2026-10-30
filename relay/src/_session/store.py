from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from _lib.atomic_write import atomic_write_json
from _push.accounts import AccountsFileError, account_key

logger = logging.getLogger("relay.session")

SESSIONS_FILE = ("auth", "sessions.json")
TOUCH_INTERVAL_SECONDS = 60
MAX_SESSIONS = 2000
NO_EXPIRY_SECONDS = 10 * 365 * 86400


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SessionRecord:
    hash: str
    account: str
    account_key: str
    device_name: str
    platform: str
    created_at: float
    last_used_at: float
    expires_at: float
    push_token: Optional[str] = None
    device_id: Optional[str] = None


@dataclass
class Session:
    record: SessionRecord
    account: dict

    @property
    def expires_at_iso(self) -> str:
        return _iso(self.record.expires_at)


class SessionStore:

    def __init__(
        self,
        state_dir: Path | str,
        accounts_getter: Callable[[], object],
        clock: Callable[[], float] = time.time,
    ):
        self._state_dir = Path(state_dir)
        self._accounts = accounts_getter
        self._clock = clock
        self._lock = threading.RLock()
        self._records: dict[str, SessionRecord] = {}
        self._load()

    def _load(self) -> None:
        path = self._state_dir.joinpath(*SESSIONS_FILE)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw.get("sessions", []):
                record = SessionRecord(**item)
                self._records[record.hash] = record
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("sessions file unreadable (%s); starting with no sessions", exc)
            self._records = {}

    def _save(self) -> None:
        atomic_write_json(self._state_dir, *SESSIONS_FILE, data={"sessions": [asdict(r) for r in self._records.values()]})

    def _drop(self, record: SessionRecord, keep_token: Optional[str] = None) -> None:
        self._records.pop(record.hash, None)
        if record.push_token and record.push_token != keep_token:
            try:
                self._accounts().unregister(record.push_token)
            except (AccountsFileError, OSError) as exc:
                logger.warning("could not unregister a dropped session's push token: %s", exc)

    def _account_for(self, key: str) -> Optional[dict]:
        for account in self._accounts().accounts():
            if account_key(account["account_secret"]) == key:
                return account
        return None

    def create(self, account: dict, device_name: str, platform: str, device_id: Optional[str] = None) -> tuple[str, Session]:
        with self._lock:
            now = self._clock()
            token = secrets.token_urlsafe(32)
            key = account_key(account["account_secret"])
            if device_id:
                for existing in [r for r in self._records.values() if r.account_key == key and r.device_id == device_id]:
                    self._drop(existing)
            while len(self._records) >= MAX_SESSIONS:
                self._drop(min(self._records.values(), key=lambda r: r.last_used_at))
            record = SessionRecord(
                hash=hash_token(token), account=account["name"], account_key=key, device_name=device_name, platform=platform,
                created_at=now, last_used_at=now, expires_at=now + NO_EXPIRY_SECONDS, device_id=device_id,
            )
            self._records[record.hash] = record
            self._save()
            return token, Session(record, account)

    def verify(self, token: Optional[str]) -> tuple[Optional[Session], str]:
        if not token:
            return None, "missing"
        with self._lock:
            record = self._records.get(hash_token(token))
            if record is None:
                return None, "unknown"
            now = self._clock()
            account = self._account_for(record.account_key)
            if account is None:
                del self._records[record.hash]
                self._save()
                return None, "account_removed"
            if now - record.last_used_at >= TOUCH_INTERVAL_SECONDS:
                record.last_used_at = now
                record.expires_at = now + NO_EXPIRY_SECONDS
                record.account = account["name"]
                self._save()
            return Session(record, account), "ok"

    def revoke(self, session: Session) -> None:
        with self._lock:
            if self._records.pop(session.record.hash, None) is not None:
                self._save()

    def set_push_token(self, session: Session, push_token: Optional[str]) -> None:
        with self._lock:
            record = self._records.get(session.record.hash)
            if record is not None and record.push_token != push_token:
                record.push_token = push_token
                self._save()

    def revoke_for_tokens(self, tokens: set[str]) -> int:
        with self._lock:
            doomed = [r for r in self._records.values() if r.push_token in tokens]
            for record in doomed:
                del self._records[record.hash]
            if doomed:
                self._save()
            return len(doomed)

    def count(self) -> int:
        with self._lock:
            return len(self._records)
