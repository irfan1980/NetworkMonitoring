from __future__ import annotations

import os
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import supervisor

PY = sys.executable

GRACEFUL = textwrap.dedent("""
    import signal, sys, time, pathlib
    marker = pathlib.Path(sys.argv[1])
    def bye(*a):
        marker.write_text("clean")
        sys.exit(0)
    for s in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, s):
            signal.signal(getattr(signal, s), bye)
    pathlib.Path(sys.argv[1] + ".started").write_text("1")
    while True:
        time.sleep(0.1)
""")

CRASHER = textwrap.dedent("""
    import sys, pathlib
    p = pathlib.Path(sys.argv[1])
    p.write_text(str(int(p.read_text() or 0) + 1) if p.exists() else "1")
    sys.exit(3)
""")


@pytest.fixture
def cfg(tmp_path):
    c = supervisor.Config({"SB_SITE_ROOT": str(tmp_path / "site"), "SB_HOME": str(tmp_path / "home")})
    c.relay_dir.mkdir(parents=True)
    c.gateway_dir.mkdir(parents=True)
    c.relay_config.parent.mkdir(parents=True)
    c.relay_config.write_text("{}")
    c.min_backoff, c.max_backoff, c.healthy_after = 0.1, 0.4, 60
    c.relay_stop_seconds = 5
    return c


def script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return p


TRACKED: list = []


def run_in_thread(sup):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("rc", sup.run()), daemon=True)
    TRACKED.append((sup, t))
    t.start()
    return t, box


@pytest.fixture(autouse=True)
def no_leftover_children():
    """A failing assertion must not leave stub children running: stop every supervisor and kill its children."""
    yield
    while TRACKED:
        sup, t = TRACKED.pop()
        sup.request_stop()
        t.join(10)
        for proc in (sup.relay_proc, sup.gateway_proc):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()


def wait_for(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_relay_restarts_with_backoff_when_it_dies(cfg, tmp_path):
    counter = tmp_path / "count"
    crasher = script(tmp_path, "crash.py", CRASHER)
    gw_marker = tmp_path / "gw"
    gw = script(tmp_path, "gw.py", GRACEFUL)
    sup = supervisor.Supervisor(cfg, gateway_cmd=[PY, str(gw), str(gw_marker)], relay_cmd=[PY, str(crasher), str(counter)],
                                prepare_gateway=lambda: True, prepare_relay=lambda: True)
    t, box = run_in_thread(sup)
    assert wait_for(lambda: sup.relay_starts >= 3)
    assert wait_for(lambda: counter.exists() and int(counter.read_text()) >= 3)
    sup.request_stop()
    t.join(15)
    assert box["rc"] == 0


def test_sigterm_style_stop_shuts_relay_and_gateway_cleanly(cfg, tmp_path):
    relay_marker, gw_marker = tmp_path / "relay", tmp_path / "gw"
    g = script(tmp_path, "g.py", GRACEFUL)
    sup = supervisor.Supervisor(cfg, gateway_cmd=[PY, str(g), str(gw_marker)], relay_cmd=[PY, str(g), str(relay_marker)],
                                prepare_gateway=lambda: True, prepare_relay=lambda: True)
    t, box = run_in_thread(sup)
    assert wait_for(lambda: Path(str(relay_marker) + ".started").exists() and Path(str(gw_marker) + ".started").exists())
    sup.request_stop()
    t.join(20)
    assert not t.is_alive() and box["rc"] == 0
    assert relay_marker.read_text() == "clean"
    assert gw_marker.read_text() == "clean"


def test_gateway_exit_stops_relay_and_returns_failure(cfg, tmp_path):
    relay_marker = tmp_path / "relay"
    g = script(tmp_path, "g.py", GRACEFUL)
    dies = script(tmp_path, "dies.py", "import time; time.sleep(1.5); raise SystemExit(7)")
    sup = supervisor.Supervisor(cfg, gateway_cmd=[PY, str(dies)], relay_cmd=[PY, str(g), str(relay_marker)],
                                prepare_gateway=lambda: True, prepare_relay=lambda: True)
    t, box = run_in_thread(sup)
    t.join(30)
    assert box["rc"] == 1
    assert relay_marker.read_text() == "clean"


def test_relay_starts_without_a_main_config_so_it_can_run_on_its_fallback(cfg, tmp_path, caplog):
    cfg.relay_config.unlink()
    g = script(tmp_path, "g.py", GRACEFUL)
    gw_marker, relay_marker = tmp_path / "gw", tmp_path / "r"
    sup = supervisor.Supervisor(cfg, gateway_cmd=[PY, str(g), str(gw_marker)], relay_cmd=[PY, str(g), str(relay_marker)],
                                prepare_gateway=lambda: True, prepare_relay=lambda: True)
    t, box = run_in_thread(sup)
    assert wait_for(lambda: Path(str(gw_marker) + ".started").exists())
    assert wait_for(lambda: Path(str(relay_marker) + ".started").exists())
    assert sup.relay_starts == 1
    assert sup.relay_env()["RELAY_CONFIG"] == str(cfg.relay_config)  # the relay decides: main file if present, else fallback
    assert "fallback" in caplog.text
    sup.request_stop()
    t.join(15)
    assert box["rc"] == 0


def test_gateway_bootstrap_failure_exits_nonzero(cfg, tmp_path):
    sup = supervisor.Supervisor(cfg, gateway_cmd=[PY, "-c", "pass"], prepare_gateway=lambda: False, prepare_relay=lambda: True)
    assert sup.run() == 1


def test_relay_env_carries_paths(cfg):
    env = supervisor.Supervisor(cfg).relay_env()
    assert env["RELAY_CONFIG"] == str(cfg.relay_config)
    assert env["RELAY_STATE_DIR"] == str(cfg.state_dir)
    assert env["RELAY_LOG_DIR"] == str(cfg.log_dir)


def test_ensure_venv_rebuilds_only_when_requirements_change(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("a==1\n")
    venv = tmp_path / "venv"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "venv" in cmd and cmd[1:3] == ["-m", "venv"]:
            py = supervisor.venv_python(venv)
            py.parent.mkdir(parents=True)
            py.write_text("")
        return type("R", (), {"returncode": 0, "stdout": ""})()

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)
    assert supervisor.ensure_venv(venv, req) and len(calls) == 2
    assert supervisor.ensure_venv(venv, req) and len(calls) == 2
    req.write_text("a==2\n")
    assert supervisor.ensure_venv(venv, req) and len(calls) == 4


def test_child_logs_are_size_bounded_with_few_rotated_files(tmp_path):
    out = tmp_path / "logs" / "child.log"
    code = "for i in range(3000): print('line %05d ' % i + 'x' * 80)"
    proc = supervisor.spawn([PY, "-c", code], tmp_path, dict(os.environ), out, max_bytes=20_000, backups=2)
    proc.wait(30)
    proc.pump.join(10)
    files = sorted(p.name for p in out.parent.iterdir())
    assert files == ["child.log", "child.log.1", "child.log.2"]
    assert all(p.stat().st_size <= 20_000 + 200 for p in out.parent.iterdir())
    assert "line 02999" in out.read_text()  # newest lines are kept
