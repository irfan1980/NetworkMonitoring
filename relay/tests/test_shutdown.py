from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil
import pytest

from . import _hang_mysql

RELAY_DIR = Path(__file__).resolve().parent.parent
EXIT_CAP_SECONDS = 10
# A liveness `ping -w 2000` started by an executor thread just before exit is a bounded helper, not a leak; it ends by itself.
PING_GRACE_SECONDS = 4
AUTH = {"web_client_id": "w", "web_client_secret": "ws", "app_client_id": "a", "app_client_secret": "as"}


def _is_alive(pid: int) -> bool:
    # a process can exit between any two psutil calls; psutil reports that as NoSuchProcess, but on Windows the raw
    # ProcessLookupError from the native call can escape, and both mean "already gone"
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, ProcessLookupError):
        return False


def _wait_gone(pids: list[int], grace: float) -> list[int]:
    deadline = time.time() + grace
    while True:
        alive = [p for p in pids if _is_alive(p)]
        if not alive or time.time() >= deadline:
            return alive
        time.sleep(0.2)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(port: int, proc: subprocess.Popen, timeout: float = 40) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"relay exited early with {proc.returncode}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read()
            return
        except Exception:
            time.sleep(0.3)
    raise AssertionError("relay never became healthy")


def _login(port: int) -> str:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/auth/login", method="POST", headers={"content-type": "application/json"},
        data=json.dumps({"account_secret": "1111", "device_name": "pytest", "platform": "web"}).encode(),
    )
    return json.loads(urllib.request.urlopen(request, timeout=5).read())["token"]


def _open_ws(port: int, path: str):
    from websockets.sync.client import connect

    ws = connect(f"ws://127.0.0.1:{port}{path}?token={_login(port)}", open_timeout=5, legacy=True)
    ws.send(json.dumps({"type": "hello", "device": "d", "clientId": "c"}))
    ws.recv(timeout=10)
    return ws


@pytest.mark.skipif(sys.platform != "win32" and not hasattr(signal, "SIGINT"), reason="needs signals")
def test_ctrl_c_stops_the_relay_quickly_with_ws_clients_children_and_a_hung_db(tmp_path):
    db_port, relay_port = _free_port(), _free_port()
    db = _hang_mysql.start(db_port)
    config = {
        "settings": {"bind_host": "127.0.0.1", "port": relay_port, "ack_pin": "1234", "auth": AUTH, "node_poll_seconds": 1},
        "machines": [{
            "machine_id": 1, "machine_ip": "127.0.0.1", "machine_port": db_port, "machine_name": "m", "machine_type": "L",
            "machine_os": "Windows", "machine_scheduler": True,
            "nodes": [{
                "node_id": "n1", "node_name": "db", "node_type": "Database", "node_platform": "mysql", "server_id": 1001,
                "node_config": {"host": "127.0.0.1", "port": db_port, "user": "u", "password": "p", "database": "d", "used_buses": []},
            }],
        }],
        "scheduler": {"tick_seconds": 0.5, "health_probe_seconds": 1},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    accounts = tmp_path / "state" / "auth" / "push_accounts.json"
    accounts.parent.mkdir(parents=True)
    accounts.write_text(json.dumps([{"account_secret": "1111", "name": "Ops", "min_severity": 5, "device_codes": []}]))
    env = {
        **os.environ, "RELAY_CONFIG": str(config_path), "RELAY_STATE_DIR": str(tmp_path / "state"),
        "RELAY_LOG_DIR": str(tmp_path / "logs"), "PYTHONUNBUFFERED": "1",
    }
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    log = (tmp_path / "relay.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, "run.py"], cwd=RELAY_DIR, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
    )
    sockets = []
    try:
        _wait_http(relay_port, proc)
        sockets = [_open_ws(relay_port, "/ws"), _open_ws(relay_port, "/ws/machine/1")] if _machine_online(relay_port) else [_open_ws(relay_port, "/ws")]
        time.sleep(6)
        tree = psutil.Process(proc.pid).children(recursive=True)
        pids = [proc.pid] + [p.pid for p in tree]

        started = time.time()
        proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        try:
            proc.wait(timeout=EXIT_CAP_SECONDS)
        except subprocess.TimeoutExpired:
            pytest.fail(f"relay still running {EXIT_CAP_SECONDS}s after the signal\n{(tmp_path / 'relay.log').read_text()[-2000:]}")
        elapsed = time.time() - started

        orphans = _wait_gone(pids, PING_GRACE_SECONDS)
        detail = []
        for o in orphans:
            try:
                detail.append((o, psutil.Process(o).cmdline()[-3:]))
            except (psutil.Error, ProcessLookupError):
                detail.append((o, "gone"))
        assert not orphans, f"orphan processes after shutdown: {detail}"
        assert elapsed < EXIT_CAP_SECONDS
    finally:
        for ws in sockets:
            try:
                ws.close()
            except Exception:
                pass
        if proc.poll() is None:
            try:
                for child in psutil.Process(proc.pid).children(recursive=True):
                    try:
                        child.kill()
                    except (psutil.Error, ProcessLookupError):
                        pass
            except (psutil.Error, ProcessLookupError):
                pass
            proc.kill()
        log.close()
        db.close()


def _machine_online(port: int) -> bool:
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            ws = _open_ws(port, "/ws/machine/1")
            ws.close()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def test_db_lock_pid_check_never_calls_os_kill(monkeypatch):
    from . import _db_lock

    def boom(*_a, **_k):
        raise AssertionError("os.kill(pid, 0) is CTRL_C_EVENT on Windows and interrupts the whole console")

    monkeypatch.setattr(os, "kill", boom)
    assert _db_lock._pid_is_running(os.getpid()) is True
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        assert _db_lock._pid_is_running(other.pid) is True
    finally:
        other.kill()
        other.wait()
    assert _db_lock._pid_is_running(other.pid) is False
