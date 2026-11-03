from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from _lib.config_fallback import validate_config_file
from _state.database import DB_FILENAME
from main import create_app

RELAY_DIR = Path(__file__).resolve().parent.parent
EXAMPLE = RELAY_DIR / "config" / "relay_config.example.json"
SCHEDULE = {
    "id": 3, "name": "Nightly", "machine_id": 1, "script": "a.ps1", "args": [], "enabled": True,
    "trigger": {"kind": "weekly", "time": "08:00", "days_of_week": [1, 3]}, "controls": {},
}
SESSION = {
    "hash": "h" * 64, "account": "Ops", "account_key": "k" * 16, "device_name": "Pixel", "platform": "android",
    "created_at": 1.0, "last_used_at": 2.0, "expires_at": 3.0, "push_token": None, "device_id": None,
}


def seed_old_state(state: Path) -> None:
    (state / "scheduler").mkdir(parents=True)
    (state / "auth").mkdir(parents=True)
    (state / "scheduler" / "schedules.json").write_text(json.dumps({"next_id": 9, "schedules": [SCHEDULE]}))
    (state / "auth" / "sessions.json").write_text(json.dumps({"sessions": [SESSION]}))
    (state / "auth" / "push_accounts.json").write_text(json.dumps([{"account_secret": "real-account-secret", "name": "Real", "min_severity": 5}]))


def main_config(port: int = 8085) -> dict:
    cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    cfg["settings"]["port"] = port
    cfg["settings"]["ack_pin"] = "1234"
    cfg["machines"][0]["machine_scheduler"] = True
    return cfg


def fallback_app(tmp_path: Path):
    throwaway = tmp_path / "throwaway"
    return create_app(EXAMPLE, state_dir=throwaway, fallback_config=True), throwaway


def test_the_fallback_app_reports_itself_and_nobody_can_log_in(tmp_path):
    app, _ = fallback_app(tmp_path)
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["configMode"] == "fallback" and "fallback" in body["configNote"]
    login = client.post("/api/auth/login", json={"account_secret": "anything", "platform": "web", "device_name": "x"})
    assert login.status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/schedules").status_code == 401


def test_a_normal_app_reports_main_mode(tmp_path, write_config, config_dict):
    app = create_app(write_config(config_dict), state_dir=tmp_path / "state")
    body = TestClient(app).get("/health").json()
    assert body["configMode"] == "main" and "configNote" not in body


def test_the_fallback_does_nothing_that_could_reach_out_or_save(tmp_path):
    app, throwaway = fallback_app(tmp_path)
    config = app.state.config_store.get()
    assert config.settings.push.enabled is False
    assert config.servers == [] and config.service_busses == [] and config.settings.dispatch.recipients.sms == []
    assert [(m.machine_ip, m.nodes, m.machine_scheduler) for m in config.machines] == [("127.0.0.1", [], False)]
    assert app.state.state_backup_loop is None
    assert app.state.state_dir == throwaway


def test_fallback_start_must_not_consume_the_one_time_state_import(tmp_path):
    real = tmp_path / "real_state"
    app, throwaway = fallback_app(tmp_path)
    del app
    assert not real.exists() and not (real / DB_FILENAME).exists()

    # the user now drops old state files and the main config
    seed_old_state(real)
    cfg_path = tmp_path / "relay_config.json"
    cfg_path.write_text(json.dumps(main_config()))
    assert validate_config_file(cfg_path) is None

    app = create_app(cfg_path, state_dir=real)
    client = TestClient(app)
    assert client.get("/health").json()["configMode"] == "main"
    assert [s.id for s in app.state.schedule_store.list()] == [3]
    assert app.state.sessions.count() == 1
    assert [a["name"] for a in app.state.accounts.accounts()] == ["Real"]
    assert client.post("/api/auth/login", json={"account_secret": "real-account-secret", "platform": "web", "device_name": "x"}).status_code == 200
    assert (real / DB_FILENAME).exists()


def test_the_fallback_leaves_an_existing_real_state_dir_completely_alone(tmp_path):
    real = tmp_path / "real_state"
    seed_old_state(real)
    before = {p.relative_to(real).as_posix(): p.read_bytes() for p in real.rglob("*") if p.is_file()}
    app, _ = fallback_app(tmp_path)
    assert app.state.accounts.count() == 0
    del app
    after = {p.relative_to(real).as_posix(): p.read_bytes() for p in real.rglob("*") if p.is_file()}
    assert before == after and not (real / DB_FILENAME).exists()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def health(port: int):
    try:
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read())
    except Exception:
        return None


def wait_for(fn, timeout=40.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.2)
    return None


def test_run_py_starts_on_the_fallback_and_switches_by_itself_when_the_main_config_appears(tmp_path):
    fallback_port, main_port = free_port(), free_port()
    fallback_cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    fallback_cfg["settings"]["port"] = fallback_port
    fallback_file = tmp_path / "fallback.json"
    fallback_file.write_text(json.dumps(fallback_cfg))
    main_file = tmp_path / "config" / "relay_config.json"
    state = tmp_path / "state"
    seed_old_state(state)
    env = {
        **os.environ, "RELAY_CONFIG": str(main_file), "RELAY_FALLBACK_CONFIG": str(fallback_file), "RELAY_STATE_DIR": str(state),
        "RELAY_LOG_DIR": str(tmp_path / "logs"), "PYTHONUNBUFFERED": "1",
    }
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    out = (tmp_path / "out.txt").open("wb")
    proc = subprocess.Popen([sys.executable, "run.py"], cwd=RELAY_DIR, env=env, stdout=out, stderr=subprocess.STDOUT, creationflags=flags)
    try:
        body = wait_for(lambda: health(fallback_port))
        assert body and body["configMode"] == "fallback", (tmp_path / "out.txt").read_bytes()[-1500:]
        assert not (state / DB_FILENAME).exists()

        main_file.parent.mkdir()
        main_file.write_text("{half uploaded")
        time.sleep(5)
        assert health(fallback_port)["configMode"] == "fallback" and health(main_port) is None
        assert not (state / DB_FILENAME).exists()

        main_file.write_text(json.dumps(main_config(main_port)))
        body = wait_for(lambda: health(main_port))
        assert body and body["configMode"] == "main"
        assert proc.poll() is None
        assert wait_for(lambda: health(fallback_port) is None, 10)
        assert (state / DB_FILENAME).exists()

        text = (tmp_path / "out.txt").read_bytes().decode("utf-8", "replace")
        assert "RUNNING ON THE FALLBACK CONFIG" in text
        assert text.count("staying on the fallback config") == 1
    finally:
        if proc.poll() is None:
            proc.send_signal(__import__("signal").CTRL_BREAK_EVENT if sys.platform == "win32" else __import__("signal").SIGINT)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        out.close()


def test_run_py_refuses_an_existing_invalid_main_config_instead_of_using_the_fallback(tmp_path):
    main_file = tmp_path / "relay_config.json"
    main_file.write_text("{broken")
    env = {**os.environ, "RELAY_CONFIG": str(main_file), "RELAY_STATE_DIR": str(tmp_path / "state"), "RELAY_LOG_DIR": str(tmp_path / "logs")}
    result = subprocess.run([sys.executable, "run.py"], cwd=RELAY_DIR, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1 and "refusing to start" in result.stderr + result.stdout
    assert not (tmp_path / "state" / DB_FILENAME).exists()
