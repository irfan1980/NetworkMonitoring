from __future__ import annotations

import asyncio
import re
import socket
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from _metrics.readability import ReadStatus


_BARE_ZERO_OFFSET = re.compile(r"([+-]00(:?00)?)$")
_HAS_REAL_OFFSET = re.compile(r"[+-]\d{2}:?\d{2}$")


def to_iso_utc(value) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        iso = dt.isoformat()
        return iso.replace("+00:00", "Z")

    if isinstance(value, str):
        s = value.strip()
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        if s.endswith("Z"):
            return s
        collapsed = _BARE_ZERO_OFFSET.sub("Z", s)
        if collapsed != s:
            return collapsed
        if _HAS_REAL_OFFSET.search(s):
            return s
        return s + "Z"

    raise TypeError(f"to_iso_utc: unsupported type {type(value)!r}")


def normalize_numeric(value):
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def normalize_row(row: dict) -> dict:
    return {k: normalize_numeric(v) for k, v in row.items()}


class _TcpConnectTimeout(OSError):
    pass


async def tcp_check(host: str, port: int, timeout_ms: int) -> ReadStatus:
    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, _blocking_tcp_connect, host, port, timeout_ms / 1000),
            timeout=timeout_ms / 1000 + 0.5,
        )
    except _TcpConnectTimeout:
        return ReadStatus.DOWN
    except asyncio.TimeoutError:
        return ReadStatus.UNREAD
    except OSError:
        return ReadStatus.DOWN
    return ReadStatus.OK


def _blocking_tcp_connect(host: str, port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    try:
        addr_infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise exc

    last_exc: Optional[BaseException] = None
    for family, socktype, proto, _canonname, sockaddr in addr_infos:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _TcpConnectTimeout(
                f"connect to {host}:{port} did not complete within {timeout_s}s "
                f"across {len(addr_infos)} resolved address(es)"
            ) from last_exc
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(remaining)
        try:
            sock.connect(sockaddr)
            return
        except socket.timeout as exc:
            last_exc = exc
        except OSError as exc:
            last_exc = exc
        finally:
            sock.close()

    if isinstance(last_exc, socket.timeout):
        raise _TcpConnectTimeout(str(last_exc)) from last_exc
    if last_exc is not None:
        raise last_exc
    raise _TcpConnectTimeout(f"no addresses resolved for {host}:{port}")


async def icmp_check(host: str, timeout_ms: int) -> ReadStatus:
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _blocking_icmp_check, host, timeout_ms),
            timeout=timeout_ms / 1000 + 1.0,
        )
    except asyncio.TimeoutError:
        return ReadStatus.UNREAD
    except Exception:
        return ReadStatus.UNREAD


def _blocking_icmp_check(host: str, timeout_ms: int) -> ReadStatus:
    import platform as _platform_mod
    import subprocess

    is_windows = _platform_mod.system().lower() == "windows"
    count_flag = "-n" if is_windows else "-c"
    timeout_flag = "-w" if is_windows else "-W"
    timeout_value = str(timeout_ms) if is_windows else str(max(1, -(-timeout_ms // 1000)))
    command = ["ping", count_flag, "1", timeout_flag, timeout_value, host]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=(timeout_ms / 1000) + 1.0,
        )
    except subprocess.TimeoutExpired:
        return ReadStatus.DOWN
    except (FileNotFoundError, PermissionError, OSError):
        return ReadStatus.UNREAD
    return ReadStatus.OK if result.returncode == 0 else ReadStatus.DOWN


async def run_blocking(fn, *args, timeout_ms: int):
    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(loop.run_in_executor(None, fn, *args), timeout=timeout_ms / 1000)


class MonotonicAuthProbeClock:

    def __init__(self, interval_ms: int):
        self.interval_ms = interval_ms
        self.last_probe_monotonic: Optional[float] = None
        self.last_ok: bool = True

    def is_due(self) -> bool:
        if self.last_probe_monotonic is None:
            return True
        return (time.monotonic() - self.last_probe_monotonic) * 1000 >= self.interval_ms

    def record(self, ok: bool) -> None:
        self.last_probe_monotonic = time.monotonic()
        self.last_ok = ok
