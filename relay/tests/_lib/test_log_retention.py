from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from _contract.schema import Settings
from _lib import file_logging as fl
from _lib.file_logging import FileLogging, prune_old_logs

TODAY = date(2026, 11, 2)


def seed(folder: Path, days_old: int, kinds=("info", "error")) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for kind in kinds:
        p = folder / f"{TODAY - timedelta(days=days_old):%Y-%m-%d}_{kind}_log.log"
        p.write_text("x")
        paths.append(p)
    return paths


def test_setting_defaults_to_30_and_is_validated():
    base = {"auth": {}, "ack_pin": "1"}
    assert Settings(**base).log_retention_days == 30
    assert Settings(**base, log_retention_days=7).log_retention_days == 7
    for bad in (0, -1, 4000):
        with pytest.raises(Exception):
            Settings(**base, log_retention_days=bad)


def test_boundary_30_days_kept_31_deleted_both_kinds(tmp_path):
    keep = seed(tmp_path, 30) + seed(tmp_path, 1)
    drop = seed(tmp_path, 31) + seed(tmp_path, 400)
    removed = prune_old_logs(tmp_path, TODAY, 30)
    assert sorted(removed) == sorted(drop)
    assert all(p.exists() for p in keep) and not any(p.exists() for p in drop)


def test_todays_and_future_files_are_never_deleted_even_with_retention_one(tmp_path):
    today_files = seed(tmp_path, 0)
    future = seed(tmp_path, -5)
    prune_old_logs(tmp_path, TODAY, 1)
    assert all(p.exists() for p in today_files + future)


def test_only_the_exact_daily_pattern_is_touched(tmp_path):
    names = [
        "supervisor.log", "supervisor.log.1", "gateway.log", "gateway.log.2", "relay-console.log", "relay-console.log.3",
        "2020-01-01_info_log.log.bak", "2020-01-01_debug_log.log", "2020-1-1_info_log.log", "x2020-01-01_info_log.log",
        "2020-01-01_INFO_log.log", "2020-13-45_info_log.log", "2020-02-30_error_log.log", "notes.txt", "2020-01-01_info_log.log.1",
    ]
    for n in names:
        (tmp_path / n).write_text("mine")
    (tmp_path / "2020-01-01_info_log.log.d").mkdir()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "2020-01-01_info_log.log").write_text("mine")
    old = datetime(2020, 1, 1).timestamp()
    for n in names:
        os.utime(tmp_path / n, (old, old))
    assert prune_old_logs(tmp_path, TODAY, 30) == []
    for n in names:
        assert (tmp_path / n).read_text() == "mine", n
    assert (tmp_path / "sub" / "2020-01-01_info_log.log").exists()


def test_a_missing_folder_is_not_an_error(tmp_path):
    assert prune_old_logs(tmp_path / "nope", TODAY, 30) == []


def test_a_failed_delete_logs_a_warning_and_the_rest_still_go(tmp_path, monkeypatch, caplog):
    locked, other = seed(tmp_path, 40, kinds=("info",))[0], seed(tmp_path, 41, kinds=("info",))[0]
    real = Path.unlink

    def unlink(self, *a, **k):
        if self == locked:
            raise PermissionError("file is in use")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    with caplog.at_level(logging.WARNING, logger="relay.logs"):
        removed = prune_old_logs(tmp_path, TODAY, 30)
    assert removed == [other] and locked.exists()
    assert "could not delete old log" in caplog.text


class Clock:
    def __init__(self, day):
        self.now = datetime(day.year, day.month, day.day, 12)

    def __call__(self):
        return self.now


def test_startup_pass_and_rollover_pass_prune_with_the_configured_days(tmp_path):
    clock = Clock(TODAY)
    logs = FileLogging(tmp_path, clock)
    seen = {"days": 5}
    logs.set_retention_source(lambda: seen["days"])
    old6, old4 = seed(tmp_path, 6), seed(tmp_path, 4)
    logs.prune()
    assert not any(p.exists() for p in old6) and all(p.exists() for p in old4)

    rec = logging.LogRecord("t", logging.INFO, "", 0, "hello", (), None)
    logs.info.emit(rec)
    assert (tmp_path / f"{TODAY:%Y-%m-%d}_info_log.log").exists()
    clock.now += timedelta(days=1)
    seen["days"] = 4
    logs.info.emit(rec)
    assert not any(p.exists() for p in old4)  # 5 days old tomorrow, past the new 4-day setting
    assert (tmp_path / f"{TODAY + timedelta(days=1):%Y-%m-%d}_info_log.log").exists()
    logs.info.close()
    logs.error.close()


def test_pruning_failure_never_stops_logging(tmp_path, monkeypatch):
    clock = Clock(TODAY)
    logs = FileLogging(tmp_path, clock)
    monkeypatch.setattr(fl, "prune_old_logs", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    rec = logging.LogRecord("t", logging.INFO, "", 0, "one", (), None)
    logs.info.emit(rec)
    clock.now += timedelta(days=1)
    logs.info.emit(rec)
    text = (tmp_path / f"{TODAY + timedelta(days=1):%Y-%m-%d}_info_log.log").read_text()
    assert "one" in text
    logs.info.close()
    logs.error.close()
