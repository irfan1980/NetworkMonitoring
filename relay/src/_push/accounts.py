from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from _lib.auth import timing_safe_equal_str
from _push.schedule_window import WindowError, parse_schedule
from _state.database import Database

logger = logging.getLogger("relay.push.accounts")

PLATFORMS = ("android", "ios", "web")


class AccountsFileError(Exception):
    """The accounts tables could not be read or written (the name is kept for the HTTP error code)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def account_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def _normalise_device(raw) -> Optional[dict]:
    if isinstance(raw, str):
        raw = {"token": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("token"), str) or not raw["token"]:
        return None
    return {
        "token": raw["token"],
        "platform": raw.get("platform") if raw.get("platform") in PLATFORMS else "android",
        "device_name": str(raw.get("device_name") or ""),
        "registered_at": raw.get("registered_at") or _now_iso(),
        "last_seen_at": raw.get("last_seen_at") or raw.get("registered_at") or _now_iso(),
        **({"device_id": raw["device_id"]} if isinstance(raw.get("device_id"), str) and raw["device_id"] else {}),
    }


def _normalise_account(raw) -> Optional[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("account_secret"), str) or not raw["account_secret"]:
        return None
    try:
        min_severity = int(raw.get("min_severity", 5))
    except (TypeError, ValueError):
        min_severity = 5
    devices = [d for d in (_normalise_device(x) for x in raw.get("device_codes") or []) if d]
    account = {
        "account_secret": raw["account_secret"],
        "name": str(raw.get("name") or "account"),
        "min_severity": min(5, max(1, min_severity)),
        "device_codes": devices,
    }
    if raw.get("push_window") is not None:
        try:
            account["push_window"] = parse_schedule(raw["push_window"])
        except WindowError:
            logger.warning("account %s: ignoring an invalid push_window", account["name"])
    return account


class AccountStore:
    """Accounts live in the state database: `user_accounts` (secret, name, min_severity; owned by the operator and
    editable with plain SQL), plus `account_devices` and `account_push_windows` which the relay writes at runtime.
    Every read goes to the database, so rows added or removed from outside (sqlite3 CLI) apply on the next call."""

    def __init__(self, db: Database, clock: Callable[[], str] = _now_iso):
        self._db = db
        self._clock = clock
        self._lock = threading.RLock()
        self._last_good: list[dict] = []
        self._warned = False

    @staticmethod
    def _read(conn: sqlite3.Connection) -> list[dict]:
        accounts: dict[str, dict] = {}
        for secret, name, severity in conn.execute("SELECT account_secret, name, min_severity FROM user_accounts ORDER BY rowid"):
            if not isinstance(secret, str) or not secret:
                continue
            try:
                min_severity = min(5, max(1, int(severity)))
            except (TypeError, ValueError):
                min_severity = 5
            accounts[secret] = {"account_secret": secret, "name": str(name or "account"), "min_severity": min_severity, "device_codes": []}
        for secret, token, platform, device_name, registered_at, last_seen_at, device_id in conn.execute(
            "SELECT account_secret, token, platform, device_name, registered_at, last_seen_at, device_id FROM account_devices ORDER BY rowid"
        ):
            account = accounts.get(secret)
            if account is None:
                continue
            device = {
                "token": token, "platform": platform if platform in PLATFORMS else "android", "device_name": device_name or "",
                "registered_at": registered_at, "last_seen_at": last_seen_at,
            }
            if device_id:
                device["device_id"] = device_id
            account["device_codes"].append(device)
        for secret, window in conn.execute("SELECT account_secret, window FROM account_push_windows"):
            account = accounts.get(secret)
            if account is None:
                continue
            try:
                account["push_window"] = parse_schedule(json.loads(window))
            except (ValueError, WindowError):
                logger.warning("account %s: ignoring an invalid push window", account["name"])
        return list(accounts.values())

    @staticmethod
    def write_children(conn: sqlite3.Connection, accounts: list[dict]) -> None:
        conn.execute("DELETE FROM account_devices")
        conn.execute("DELETE FROM account_push_windows")
        seen: set[str] = set()
        for account in accounts:
            for d in account["device_codes"]:
                if d["token"] in seen:
                    continue
                seen.add(d["token"])
                conn.execute(
                    "INSERT INTO account_devices(token, account_secret, platform, device_name, registered_at, last_seen_at, device_id)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (d["token"], account["account_secret"], d["platform"], d["device_name"], d["registered_at"], d["last_seen_at"], d.get("device_id")),
                )
            if account.get("push_window") is not None:
                conn.execute(
                    "INSERT INTO account_push_windows(account_secret, window) VALUES (?, ?)",
                    (account["account_secret"], json.dumps(account["push_window"])),
                )

    def _load(self) -> list[dict]:
        try:
            accounts = self._db.read(self._read)
            self._last_good, self._warned = accounts, False
            return accounts
        except sqlite3.Error as exc:
            if not self._warned:
                logger.warning("accounts could not be read (%s); using the last good copy", exc)
                self._warned = True
            return self._last_good

    def count(self) -> int:
        return len(self._load())

    def accounts(self) -> list[dict]:
        return [dict(a, device_codes=[dict(d) for d in a["device_codes"]]) for a in self._load()]

    def find_by_secret(self, secret: str) -> Optional[dict]:
        found = None
        for account in self._load():
            if timing_safe_equal_str(secret or "", account["account_secret"]):
                found = account
        return None if found is None else dict(found, device_codes=[dict(d) for d in found["device_codes"]])

    def account_for_token(self, token: str) -> Optional[dict]:
        for account in self._load():
            if any(d["token"] == token for d in account["device_codes"]):
                return dict(account, device_codes=[dict(d) for d in account["device_codes"]])
        return None

    def _mutate(self, change: Callable[[list[dict]], bool]) -> bool:
        outcome = {"changed": False}

        def apply(conn: sqlite3.Connection) -> None:
            accounts = self._read(conn)
            outcome["changed"] = change(accounts)
            if outcome["changed"]:
                self.write_children(conn, accounts)

        with self._lock:
            try:
                self._db.write(apply)
            except sqlite3.Error as exc:
                raise AccountsFileError(f"accounts could not be saved: {exc}") from exc
            return outcome["changed"]

    def register(self, secret: str, token: str, platform: str, device_name: str, device_id: Optional[str] = None) -> Optional[dict]:
        result: dict = {}

        def apply(accounts: list[dict]) -> bool:
            target = next((a for a in accounts if timing_safe_equal_str(secret or "", a["account_secret"])), None)
            if target is None:
                return False
            now = self._clock()
            if device_id:
                target["device_codes"] = [d for d in target["device_codes"] if d.get("device_id") != device_id or d["token"] == token]
            existing = None
            for account in accounts:
                for device in list(account["device_codes"]):
                    if device["token"] == token:
                        if account is target:
                            existing = device
                        else:
                            account["device_codes"].remove(device)
            if existing is not None:
                existing.update(platform=platform, device_name=device_name, last_seen_at=now, **({"device_id": device_id} if device_id else {}))
            else:
                target["device_codes"].append({
                    "token": token, "platform": platform, "device_name": device_name,
                    "registered_at": now, "last_seen_at": now, **({"device_id": device_id} if device_id else {}),
                })
            result["account"] = {"name": target["name"], "min_severity": target["min_severity"]}
            return True

        self._mutate(apply)
        return result.get("account")

    def unregister(self, token: str) -> bool:
        def apply(accounts: list[dict]) -> bool:
            removed = False
            for account in accounts:
                before = len(account["device_codes"])
                account["device_codes"] = [d for d in account["device_codes"] if d["token"] != token]
                removed = removed or len(account["device_codes"]) != before
            return removed

        return self._mutate(apply)

    def remove_tokens(self, tokens: set[str]) -> int:
        count = {"n": 0}

        def apply(accounts: list[dict]) -> bool:
            for account in accounts:
                before = len(account["device_codes"])
                account["device_codes"] = [d for d in account["device_codes"] if d["token"] not in tokens]
                count["n"] += before - len(account["device_codes"])
            return count["n"] > 0

        self._mutate(apply)
        return count["n"]

    def set_window(self, secret: str, window: Optional[dict]) -> bool:
        found = {"ok": False}

        def apply(accounts: list[dict]) -> bool:
            target = next((a for a in accounts if timing_safe_equal_str(secret or "", a["account_secret"])), None)
            if target is None:
                return False
            found["ok"] = True
            if window is None:
                return target.pop("push_window", None) is not None
            target["push_window"] = window
            return True

        self._mutate(apply)
        return found["ok"]
