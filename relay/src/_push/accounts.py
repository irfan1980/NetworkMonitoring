from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from _lib.atomic_write import atomic_write_json
from _lib.auth import timing_safe_equal_str
from _push.schedule_window import WindowError, parse_schedule

logger = logging.getLogger("relay.push.accounts")

PLATFORMS = ("android", "ios", "web")


class AccountsFileError(Exception):
    pass


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

    def __init__(self, path: Path | str, clock: Callable[[], str] = _now_iso):
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._accounts: list[dict] = []
        self._mtime: Optional[tuple] = None
        self._broken = False
        self._warned_broken = False

    @property
    def path(self) -> Path:
        return self._path

    def set_path(self, path: Path | str) -> None:
        with self._lock:
            if Path(path) != self._path:
                self._path = Path(path)
                self._mtime = None
                self._accounts = []

    def _stat(self) -> Optional[tuple]:
        try:
            info = self._path.stat()
            return info.st_mtime_ns, info.st_size
        except OSError:
            return None

    def _read_file(self) -> list[dict]:
        for attempt in range(5):
            try:
                text = self._path.read_text(encoding="utf-8")
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)  # another writer is mid-replace
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("push accounts file must contain a JSON list")
        return [a for a in (_normalise_account(x) for x in raw) if a]

    def refresh(self) -> None:
        with self._lock:
            mtime = self._stat()
            if mtime == self._mtime and not self._broken:
                return
            if mtime is None:
                self._accounts, self._mtime, self._broken = [], None, False
                return
            try:
                self._accounts = self._read_file()
                self._broken = False
                self._warned_broken = False
            except (OSError, ValueError) as exc:
                self._broken = True
                if not self._warned_broken:
                    logger.warning("push accounts file unreadable (%s); keeping the last good copy: %s", self._path, exc)
                    self._warned_broken = True
            self._mtime = mtime

    def accounts(self) -> list[dict]:
        with self._lock:
            self.refresh()
            return [dict(a, device_codes=[dict(d) for d in a["device_codes"]]) for a in self._accounts]

    def find_by_secret(self, secret: str) -> Optional[dict]:
        with self._lock:
            self.refresh()
            found = None
            for account in self._accounts:
                if timing_safe_equal_str(secret or "", account["account_secret"]):
                    found = account
            return None if found is None else dict(found, device_codes=[dict(d) for d in found["device_codes"]])

    def account_for_token(self, token: str) -> Optional[dict]:
        with self._lock:
            self.refresh()
            for account in self._accounts:
                if any(d["token"] == token for d in account["device_codes"]):
                    return dict(account, device_codes=[dict(d) for d in account["device_codes"]])
            return None

    def _mutate(self, change: Callable[[list[dict]], bool]) -> bool:
        with self._lock:
            if self._stat() is not None:
                try:
                    self._accounts = self._read_file()
                    self._broken = False
                except (OSError, ValueError) as exc:
                    raise AccountsFileError(f"push accounts file is unreadable: {exc}") from exc
            changed = change(self._accounts)
            if changed:
                atomic_write_json(self._path.parent, self._path.name, data=self._accounts)
            self._mtime = self._stat()
            return changed

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
