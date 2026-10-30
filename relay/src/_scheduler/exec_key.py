from __future__ import annotations

import hashlib
import time
from typing import Optional

BRACKET_SECONDS = 900


def bracket(now: Optional[float] = None) -> int:
    return int(time.time() if now is None else now) // BRACKET_SECONDS


def exec_key(machine_id: int, machine_ip: str, now: Optional[float] = None) -> str:
    return hashlib.sha512(f"{int(machine_id)}_{bracket(now)}_{machine_ip}".encode("utf-8")).hexdigest()
