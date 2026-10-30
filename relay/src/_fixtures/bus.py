from __future__ import annotations

import subprocess

import httpx

from . import env
from .elevation import wait_until

_BUSES_DIR = env.CONFIG_MAKER_DIR / "buses"
_HEALTH_URLS = {
    1: "http://localhost:5300/health",
    2: "http://localhost:5301/health",
}


def health_url(bus: int) -> str:
    return _HEALTH_URLS[bus]


def is_healthy(bus: int) -> bool:
    env.require_live_lab()
    try:
        resp = httpx.get(health_url(bus), timeout=3.0)
    except httpx.HTTPError:
        return False
    return resp.status_code == 200 and resp.json().get("status") == "healthy"


def outage_start(bus: int, timeout_seconds: float = 30.0) -> None:
    env.require_live_lab()
    _run_cmd(bus, f"emulator_{bus}.outage-start.cmd")
    wait_until(
        lambda: not is_healthy(bus),
        timeout_seconds=timeout_seconds,
        interval_seconds=1.0,
        description=f"bus {bus} to actually go down at {health_url(bus)}",
    )


def outage_end(bus: int, timeout_seconds: float = 180.0) -> None:
    env.require_live_lab()
    _run_cmd(bus, f"emulator_{bus}.outage-end.cmd")
    wait_until(
        lambda: is_healthy(bus),
        timeout_seconds=timeout_seconds,
        interval_seconds=1.0,
        description=f"bus {bus} health at {health_url(bus)}",
    )


def _run_cmd(bus: int, script_name: str) -> subprocess.CompletedProcess:
    env.require_live_lab()
    script = _BUSES_DIR / script_name
    if not script.is_file():
        raise FileNotFoundError(f"bus {bus} script not found: {script}")
    result = subprocess.run(
        [str(script)],
        cwd=str(_BUSES_DIR),
        shell=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"bus {bus} script {script_name} exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()}"
        )
    return result
