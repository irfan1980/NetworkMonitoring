from __future__ import annotations

import ctypes
import subprocess
import time

from . import env


class ElevationRequiredError(Exception):
    pass


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def require_elevated(reason: str) -> None:
    if not is_elevated():
        raise ElevationRequiredError(
            f"{reason} requires an elevated (Run as Administrator) process — "
            "current process is not elevated. See relay/README.md 'Elevated fixture "
            "subset' for how to run this scenario."
        )


def _run_powershell(command: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    env.require_live_lab()
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def stop_service(service_name: str) -> None:
    require_elevated(f"Stop-Service {service_name}")
    result = _run_powershell(f"Stop-Service -Name '{service_name}' -Force")
    if result.returncode != 0:
        raise RuntimeError(f"Stop-Service {service_name} failed: {result.stderr}")


def start_service(service_name: str) -> None:
    require_elevated(f"Start-Service {service_name}")
    result = _run_powershell(f"Start-Service -Name '{service_name}'")
    if result.returncode != 0:
        raise RuntimeError(f"Start-Service {service_name} failed: {result.stderr}")


def wait_until(predicate, timeout_seconds: float, interval_seconds: float = 1.0, description: str = "condition") -> None:
    deadline = time.monotonic() + timeout_seconds
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(interval_seconds)
    detail = f" (last error: {last_exc})" if last_exc else ""
    raise TimeoutError(f"timed out after {timeout_seconds}s waiting for {description}{detail}")


def is_process_running(cmdline_substring: str) -> bool:
    import psutil

    needle = cmdline_substring.lower()
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        joined = " ".join(cmdline).lower()
        if needle in joined:
            return True
    return False
