from __future__ import annotations

import io
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from _lib.file_logging import DatedFileHandler, FileLogging, LogStream, dated_log_path
from _lib.log_redact import RedactWsSecretFilter
from _lib.status_line import SEPARATOR, StatusLine, build_status, safe_text, stopped_text

from . import _hang_mysql

RELAY_DIR = Path(__file__).resolve().parent.parent
if str(RELAY_DIR) not in sys.path:
    sys.path.insert(0, str(RELAY_DIR))
import run  # noqa: E402

BREAK = signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _record(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord("relay.test", level, __file__, 1, message, None, None)


def test_flag_parsing_and_unknown_args_are_rejected_with_usage(capsys):
    assert run.parse_args([]).no_log is False
    assert run.parse_args(["--no-log"]).no_log is True
    with pytest.raises(SystemExit) as exc:
        run.parse_args(["--bogus"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "usage: run.py" in err and "--no-log" in err and "unrecognized arguments: --bogus" in err


def test_help_text_mentions_the_config_override(capsys):
    with pytest.raises(SystemExit):
        run.parse_args(["--help"])
    out = capsys.readouterr().out
    assert "RELAY_CONFIG" in out and "docstring" not in out


def test_dated_names_and_level_split(tmp_path):
    clock = Clock(datetime(2026, 10, 7, 23, 0, 0))
    info = DatedFileHandler(tmp_path, "info", logging.INFO, clock)
    error = DatedFileHandler(tmp_path, "error", logging.WARNING, clock)
    for level, text in ((logging.DEBUG, "dbg"), (logging.INFO, "inf"), (logging.WARNING, "wrn"), (logging.ERROR, "err")):
        for handler in (info, error):
            if level >= handler.level:
                handler.handle(_record(level, text))
    info.close()
    error.close()
    info_text = (tmp_path / "2026-10-07_info_log.log").read_text(encoding="utf-8")
    error_text = (tmp_path / "2026-10-07_error_log.log").read_text(encoding="utf-8")
    assert "dbg" not in info_text and all(w in info_text for w in ("inf", "wrn", "err"))
    assert "inf" not in error_text and "wrn" in error_text and "err" in error_text
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} WARNING relay\.test: wrn", error_text.splitlines()[0])


def test_rolls_to_a_new_file_when_the_date_changes(tmp_path):
    clock = Clock(datetime(2026, 10, 7, 23, 59, 58))
    handler = DatedFileHandler(tmp_path, "info", logging.INFO, clock)
    handler.handle(_record(logging.INFO, "before midnight"))
    clock.now = datetime(2026, 10, 8, 0, 0, 1)
    handler.handle(_record(logging.INFO, "after midnight"))
    handler.close()
    first = (tmp_path / "2026-10-07_info_log.log").read_text(encoding="utf-8")
    assert "before midnight" in first and "after midnight" not in first
    assert "after midnight" in (tmp_path / "2026-10-08_info_log.log").read_text(encoding="utf-8")
    assert dated_log_path(tmp_path, "error", datetime(2026, 1, 2).date()).name == "2026-01-02_error_log.log"


def test_files_are_utf8_and_secrets_are_redacted(tmp_path):
    handler = DatedFileHandler(tmp_path, "info", logging.INFO, Clock(datetime(2026, 10, 7)))
    handler.addFilter(RedactWsSecretFilter())
    handler.handle(_record(logging.INFO, "ws?clientId=a&clientSecret=hunter2 \u2192 ok"))
    handler.close()
    text = (tmp_path / "2026-10-07_info_log.log").read_bytes().decode("utf-8")
    assert "hunter2" not in text and "***REDACTED***" in text and "\u2192" in text


def test_file_logging_routes_queue_records_to_both_files_by_level(tmp_path):
    fl = FileLogging(tmp_path, clock=Clock(datetime(2026, 10, 7)))
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_httpx = logging.getLogger("httpx").level
    try:
        fl.install()
        logging.getLogger("relay.t").info("hello info")
        logging.getLogger("relay.t").warning("hello warn clientSecret=topsecret")
        logging.getLogger("httpx").info("httpx noise")
    finally:
        fl.stop()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)
        logging.getLogger("httpx").setLevel(saved_httpx)
        logging.captureWarnings(False)
    info = (tmp_path / "2026-10-07_info_log.log").read_text(encoding="utf-8")
    error = (tmp_path / "2026-10-07_error_log.log").read_text(encoding="utf-8")
    assert "hello info" in info and "hello warn" in info and "hello info" not in error and "hello warn" in error
    assert "topsecret" not in info + error and "httpx noise" not in info


def test_log_stream_turns_stray_prints_into_log_records(caplog):
    stream = LogStream("stray")
    with caplog.at_level(logging.INFO, logger="stray"):
        stream.write("line one\nline ")
        stream.write("two\n")
        stream.flush()
    assert [r.getMessage() for r in caplog.records] == ["line one", "line two"]


def test_status_line_rewrites_in_place_and_only_on_change():
    out = io.StringIO()
    line = StatusLine(out, width=lambda: 200)
    assert line.update("Relay starting") is True
    assert line.update("Relay starting") is False
    assert line.update("Relay ok") is True
    assert out.getvalue() == "\rRelay starting\rRelay ok      "
    line.finish("Relay stopped (12:00:00Z)")
    assert out.getvalue().endswith("\rRelay stopped (12:00:00Z)\n")
    assert out.getvalue().count("\n") == 1


def test_status_line_clear_and_truncation_and_ascii_fallback():
    out = io.StringIO()
    line = StatusLine(out, width=lambda: 20)
    line.update("x" * 60)
    assert len(out.getvalue().lstrip("\r")) <= 19
    line.clear()
    assert out.getvalue().endswith("\r")
    assert safe_text(f"a{SEPARATOR}b\u2026", "ascii") == "a | b..."
    assert safe_text(f"a{SEPARATOR}b", "utf-8") == f"a{SEPARATOR}b"


def _state(*, latest, online=(True, False), alerts=(1, 0, 2, 0), schedules=(1, 2), running=1, enabled=True, tick=None, level="good"):
    machines = [SimpleNamespace(machine_id=i + 1) for i in range(len(online))]
    runtimes = {m.machine_id: SimpleNamespace(status="online" if on else "offline") for m, on in zip(machines, online)}
    sched = [SimpleNamespace(id=i) for i in schedules]
    names = ("machine_alert_incidents", "pipeline_alert_incidents", "schedule_incidents", "scheduler_endpoint_incidents")
    state = SimpleNamespace(
        config_store=SimpleNamespace(get=lambda: SimpleNamespace(machines=machines, settings=SimpleNamespace(port=8085))),
        machine_registry=SimpleNamespace(get=lambda mid: runtimes.get(mid)),
        estate_loop=SimpleNamespace(latest=None if latest is None else {"summary": {"level": level}}, last_tick_at=tick),
        schedule_store=SimpleNamespace(list=lambda: sched),
        scheduler_loop=SimpleNamespace(is_running=lambda sid: sid <= running, enabled=enabled, lock_attempted=True),
    )
    for name, count in zip(names, alerts):
        setattr(state, name, [None] * count)
    return state


def test_status_text_states_and_counts():
    tick = datetime(2026, 10, 7, 12, 34, 56, tzinfo=timezone.utc)
    assert build_status(_state(latest=None)).startswith("Relay starting")
    healthy = build_status(_state(latest=True, tick=tick))
    assert healthy == SEPARATOR.join([
        "Relay healthy and running", ":8085", "machines 1/2 online", "alerts 3", "schedules 2 (1 running)", "last cycle 12:34:56Z",
    ])
    assert "DEGRADED (summary problem)" in build_status(_state(latest=True, level="problem", tick=tick))
    assert "DEGRADED (scheduler off" in build_status(_state(latest=True, enabled=False, tick=tick))
    assert build_status(_state(latest=True), stopping=True).startswith("Relay stopping")
    assert "last cycle -" in build_status(_state(latest=True, tick=None))


def test_stopped_text_has_the_time():
    assert stopped_text(datetime(2026, 10, 7, 9, 8, 7, tzinfo=timezone.utc)) == "Relay stopped (09:08:07Z)"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


AUTH = {"web_client_id": "w", "web_client_secret": "ws", "app_client_id": "a", "app_client_secret": "as"}


def _config(tmp_path, port, db_port):
    config = {
        "settings": {"bind_host": "127.0.0.1", "port": port, "ack_pin": "1234", "auth": AUTH, "node_poll_seconds": 1},
        "machines": [{
            "machine_id": 1, "machine_ip": "127.0.0.1", "machine_port": db_port, "machine_name": "m", "machine_type": "L",
            "machine_os": "Windows",
            "nodes": [{
                "node_id": "n1", "node_name": "db", "node_type": "Database", "node_platform": "mysql", "server_id": 1001,
                "node_config": {"host": "127.0.0.1", "port": db_port, "user": "u", "password": "p", "database": "d", "used_buses": []},
            }],
        }],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return path


def _start(tmp_path, config_path, *args):
    env = {**os.environ, "RELAY_CONFIG": str(config_path), "RELAY_STATE_DIR": str(tmp_path / "state"),
           "RELAY_LOG_DIR": str(tmp_path / "logs"), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    out, err = open(tmp_path / "stdout.txt", "wb"), open(tmp_path / "stderr.txt", "wb")
    proc = subprocess.Popen([sys.executable, "run.py", *args], cwd=RELAY_DIR, env=env, stdout=out, stderr=err, creationflags=flags)
    return proc, out, err


def _read(tmp_path, name):
    return (tmp_path / name).read_bytes().decode("utf-8")


def _wait_http(port, proc, timeout=40):
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


def test_no_log_run_keeps_the_terminal_to_the_status_line_and_logs_children_to_the_files(tmp_path):
    db = _hang_mysql.start(_free_port())
    port = _free_port()
    config_path = _config(tmp_path, port, db.getsockname()[1])
    proc, out, err = _start(tmp_path, config_path, "--no-log")
    try:
        _wait_http(port, proc)
        deadline = time.time() + 60
        while time.time() < deadline and not re.search(r"healthy and running|DEGRADED", _read(tmp_path, "stdout.txt")):
            time.sleep(0.5)
        time.sleep(1.5)
        stopping = time.time()
        proc.send_signal(BREAK)
        proc.wait(timeout=15)
        assert time.time() - stopping < 5
    finally:
        if proc.poll() is None:
            proc.kill()
        out.close()
        err.close()
        db.close()

    assert proc.returncode == 0
    stdout, stderr = _read(tmp_path, "stdout.txt"), _read(tmp_path, "stderr.txt")
    assert stderr == ""
    pieces = [p.strip() for p in re.split(r"[\r\n]", stdout) if p.strip()]
    assert pieces and all(p.startswith("Relay ") for p in pieces), pieces
    assert pieces[-1].startswith("Relay stopped (") and stdout.endswith("\n")
    assert not re.search(r"\b(INFO|WARNING|ERROR)\b|GET /health", stdout)
    assert any("healthy and running" in p or "DEGRADED" in p for p in pieces)

    day = datetime.now().strftime("%Y-%m-%d")
    info = (tmp_path / "logs" / f"{day}_info_log.log").read_text(encoding="utf-8")
    assert "Started server process" in info and "GET /health" in info
    child = re.search(r"relay\.machines\.child: machine 1 child started \(pid=(\d+)\)", info)
    assert child and int(child.group(1)) != proc.pid


def test_without_the_flag_logs_go_to_the_console_and_the_dated_files(tmp_path):
    db = _hang_mysql.start(_free_port())
    port = _free_port()
    proc, out, err = _start(tmp_path, _config(tmp_path, port, db.getsockname()[1]))
    try:
        _wait_http(port, proc)
        proc.send_signal(BREAK)
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
        out.close()
        err.close()
        db.close()
    console = _read(tmp_path, "stdout.txt") + _read(tmp_path, "stderr.txt")
    assert "INFO" in console and "Shutting down" in console
    infos = list((tmp_path / "logs").glob("*_info_log.log"))
    assert len(infos) == 1
    text = infos[0].read_text(encoding="utf-8")
    assert "INFO uvicorn.error" in text and "/health" in text


def test_no_log_fatal_config_error_still_reaches_stderr_and_the_error_log(tmp_path):
    bad = tmp_path / "config.json"
    bad.write_text("{not json")
    proc, out, err = _start(tmp_path, bad, "--no-log")
    try:
        proc.wait(timeout=30)
    finally:
        out.close()
        err.close()
    assert proc.returncode == 1
    assert "refusing to start" in _read(tmp_path, "stderr.txt")
    day = datetime.now().strftime("%Y-%m-%d")
    assert "refusing to start" in (tmp_path / "logs" / f"{day}_error_log.log").read_text(encoding="utf-8")
    assert "Traceback" not in _read(tmp_path, "stdout.txt")


def test_no_log_port_in_use_is_reported_on_stderr_and_logged(tmp_path):
    db = _hang_mysql.start(_free_port())
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    port = taken.getsockname()[1]
    proc, out, err = _start(tmp_path, _config(tmp_path, port, db.getsockname()[1]), "--no-log")
    try:
        proc.wait(timeout=30)
    finally:
        out.close()
        err.close()
        taken.close()
        db.close()
    assert proc.returncode == 1
    assert "failed to start" in _read(tmp_path, "stderr.txt")
    day = datetime.now().strftime("%Y-%m-%d")
    error_log = (tmp_path / "logs" / f"{day}_error_log.log").read_text(encoding="utf-8")
    assert re.search(r"ERROR .*(10048|98|address)", error_log, re.I)


def test_unknown_argument_exits_2_with_usage_and_starts_nothing():
    proc = subprocess.run([sys.executable, "run.py", "--nope"], cwd=RELAY_DIR, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2 and "usage: run.py" in proc.stderr and "--nope" in proc.stderr
