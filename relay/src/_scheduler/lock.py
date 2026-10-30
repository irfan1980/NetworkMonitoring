from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import psutil

from _scheduler.utc import iso, now_utc

CREATE_TIME_TOLERANCE_SECONDS = 1.0


def _self_fingerprint() -> dict:
    me = psutil.Process()
    try:
        cmdline = " ".join(me.cmdline())[:300]
    except psutil.Error:
        cmdline = ""
    return {"pid": os.getpid(), "create_time": me.create_time(), "cmdline": cmdline}


def _holder_alive(info: dict) -> bool:
    pid = info.get("pid")
    if not isinstance(pid, int) or pid == os.getpid():
        return False
    try:
        proc = psutil.Process(pid)
        started = proc.create_time()
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return False
    except psutil.Error:
        return False
    recorded = info.get("create_time")
    if recorded is None:
        return True  # lock written before create_time was recorded
    return abs(started - float(recorded)) <= CREATE_TIME_TOLERANCE_SECONDS


class InstanceLock:

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.token = uuid.uuid4().hex
        self.held = False

    def _read(self) -> Optional[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def acquire(self) -> bool:
        if self.held:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = self._read()
                if info is None:
                    time.sleep(0.05)  # a writer may be mid-write
                    info = self._read()
                if info and info.get("token") == self.token:
                    self.held = True
                    return True
                if info and _holder_alive(info):
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({**_self_fingerprint(), "token": self.token, "started_at": iso(now_utc())}, f)
            self.held = True
            return True
        return False

    def release(self) -> None:
        if not self.held:
            return
        info = self._read()
        if info and info.get("token") == self.token:
            try:
                self.path.unlink()
            except OSError:
                pass
        self.held = False
