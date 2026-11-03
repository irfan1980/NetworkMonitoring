from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from _push.accounts import AccountsFileError, account_key
from _state.database import Database

logger = logging.getLogger("relay.session")

COLUMNS = ("hash", "account", "account_key", "device_name", "platform", "created_at", "last_used_at", "expires_at", "push_token", "device_id")
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
        self._db = Database.for_dir(state_dir)
        self._accounts = accounts_getter
        self._clock = clock
        self._lock = threading.RLock()
        self._records: dict[str, SessionRecord] = {}
        self._load()

    def _load(self) -> None:
        for row in self._db.query(f"SELECT {', '.join(COLUMNS)} FROM sessions"):
            record = SessionRecord(**dict(zip(COLUMNS, row)))
            self._records[record.hash] = record

    def _upsert(self, record: SessionRecord, durable: bool = True) -> None:
        values = tuple(getattr(record, c) for c in COLUMNS)
        sql = f"INSERT OR REPLACE INTO sessions({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})"
        if durable:
            self._db.write(lambda conn: conn.execute(sql, values))
        else:
            self._db.submit(("session", record.hash), lambda conn: conn.execute(sql, values))

    def _create_rows(self, dropped: list[str], record: SessionRecord) -> None:
        values = tuple(getattr(record, c) for c in COLUMNS)
        sql = f"INSERT OR REPLACE INTO sessions({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})"

        def apply(conn) -> None:
            conn.executemany("DELETE FROM sessions WHERE hash=?", [(h,) for h in dropped])
            conn.execute(sql, values)

        self._db.write(apply)

    def _delete(self, hashes: list[str]) -> None:
        if hashes:
            self._db.write(lambda conn: conn.executemany("DELETE FROM sessions WHERE hash=?", [(h,) for h in hashes]))

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
            dropped: list[str] = []
            if device_id:
                for existing in [r for r in self._records.values() if r.account_key == key and r.device_id == device_id]:
                    self._drop(existing)
                    dropped.append(existing.hash)
            while len(self._records) >= MAX_SESSIONS:
                oldest = min(self._records.values(), key=lambda r: r.last_used_at)
                self._drop(oldest)
                dropped.append(oldest.hash)
            record = SessionRecord(
                hash=hash_token(token), account=account["name"], account_key=key, device_name=device_name, platform=platform,
                created_at=now, last_used_at=now, expires_at=now + NO_EXPIRY_SECONDS, device_id=device_id,
            )
            self._records[record.hash] = record
            self._create_rows(dropped, record)
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
                self._delete([record.hash])
                return None, "account_removed"
            if now - record.last_used_at >= TOUCH_INTERVAL_SECONDS:
                record.last_used_at = now
                record.expires_at = now + NO_EXPIRY_SECONDS
                record.account = account["name"]
                self._upsert(record, durable=False)
            return Session(record, account), "ok"

    def revoke(self, session: Session) -> None:
        with self._lock:
            if self._records.pop(session.record.hash, None) is not None:
                self._delete([session.record.hash])

    def set_push_token(self, session: Session, push_token: Optional[str]) -> None:
        with self._lock:
            record = self._records.get(session.record.hash)
            if record is not None and record.push_token != push_token:
                record.push_token = push_token
                self._upsert(record)

    def revoke_for_tokens(self, tokens: set[str]) -> int:
        with self._lock:
            doomed = [r for r in self._records.values() if r.push_token in tokens]
            for record in doomed:
                del self._records[record.hash]
            self._delete([r.hash for r in doomed])
            return len(doomed)

    def count(self) -> int:
        with self._lock:
            return len(self._records)
