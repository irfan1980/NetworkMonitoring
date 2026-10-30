from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

LOCK_PATH = Path(__file__).resolve().parent.parent / ".pytest-db.lock"
STALE_AFTER_SECONDS = 3600


class DbLockHeld(RuntimeError):
    pass


def _pid_is_running(pid: int) -> bool:
    if pid == os.getpid():
        return True
    # os.kill(pid, 0) on Windows is CTRL_C_EVENT and interrupts every process on the console
    return psutil.pid_exists(pid)


def _read_lock() -> dict | None:
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _is_stale(info: dict) -> bool:
    pid = info.get("pid")
    if not isinstance(pid, int) or not _pid_is_running(pid):
        return True
    try:
        started = datetime.fromisoformat(info["started"])
    except (KeyError, ValueError):
        return True
    age = (datetime.now(timezone.utc) - started).total_seconds()
    return age > STALE_AFTER_SECONDS


def acquire() -> None:
    existing = _read_lock()
    if existing is not None and not _is_stale(existing):
        raise DbLockHeld(
            f"Another pytest run against the real lab databases is already in progress "
            f"(PID {existing.get('pid')!r} on {existing.get('host')!r}, started "
            f"{existing.get('started')!r}). Running two invocations concurrently double-"
            f"seeds fixture rows and produces phantom failures (see relay/README.md "
            f"'Real-database test lock'). Wait for it to finish, or if you are certain "
            f"that PID is no longer running a pytest process, delete "
            f"{LOCK_PATH} by hand and retry."
        )
    info = {
        "pid": os.getpid(),
        "started": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
    }
    LOCK_PATH.write_text(json.dumps(info), encoding="utf-8")


def release() -> None:
    current = _read_lock()
    if current is not None and current.get("pid") == os.getpid():
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
