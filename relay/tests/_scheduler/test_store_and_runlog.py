from __future__ import annotations

import json
from datetime import timedelta

import pytest

from _contract.schema import SchedulerSettings
from _scheduler.runlog import RunLog, cap_tail
from _scheduler.store import ScheduleStore
from _scheduler.utc import iso, now_utc

from .conftest import schedule_input


def test_store_round_trip_keeps_every_field(tmp_path):
    store = ScheduleStore(tmp_path)
    created = store.create(schedule_input(
        args=["a", "b"], fail_threshold=4,
        trigger={"kind": "weekly", "time": "08:00", "days_of_week": [1, 3]},
        controls={"timeout_s": 30, "retries": 2, "active_from": "2026-03-01T00:00:00+02:00", "no_overlap": False},
    ))
    reloaded = ScheduleStore(tmp_path).get(created.id)
    assert reloaded == created
    assert reloaded.controls.active_from.isoformat() == "2026-02-28T22:00:00+00:00"
    raw = json.loads((tmp_path / "scheduler" / "schedules.json").read_text())
    assert raw["schedules"][0]["controls"]["active_from"] == "2026-02-28T22:00:00Z"


def test_ids_are_sequential_and_never_reused(tmp_path):
    store = ScheduleStore(tmp_path)
    a, b = store.create(schedule_input()), store.create(schedule_input())
    assert (a.id, b.id) == (1, 2)
    store.delete(b.id)
    assert ScheduleStore(tmp_path).create(schedule_input()).id == 3


def test_replace_enable_disable_delete(tmp_path):
    store = ScheduleStore(tmp_path)
    s = store.create(schedule_input())
    assert store.replace(s.id, schedule_input(name="renamed")).name == "renamed"
    assert store.set_enabled(s.id, False).enabled is False
    assert ScheduleStore(tmp_path).get(s.id).enabled is False
    assert store.delete(s.id) and not store.delete(s.id)
    assert store.replace(99, schedule_input()) is None


def test_store_notifies_listeners_on_change(tmp_path):
    store = ScheduleStore(tmp_path)
    seen = []
    store.on_change(lambda: seen.append(1))
    s = store.create(schedule_input())
    store.set_enabled(s.id, False)
    assert len(seen) == 2


def test_corrupt_store_file_is_set_aside(tmp_path):
    path = tmp_path / "scheduler" / "schedules.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    store = ScheduleStore(tmp_path)
    assert store.list() == []
    assert path.with_suffix(".corrupt").exists()


def make_log(tmp_path, **settings) -> RunLog:
    cfg = SchedulerSettings(**settings)
    return RunLog(tmp_path, lambda: cfg)


def rec(outcome, started=None, **extra) -> dict:
    stamp = iso(started or now_utc())
    return {"run_id": "r", "trigger": "scheduled", "started_at": stamp, "ended_at": stamp, "attempts": 1,
            "exit_code": 0 if outcome == "success" else 1, "outcome": outcome, "reason": extra.pop("reason", None),
            "stdout_tail": "", "stderr_tail": "", **extra}


def test_success_resets_failures_and_failures_count(tmp_path):
    log = make_log(tmp_path)
    for outcome in ("failed", "timeout", "script_not_found", "lost"):
        log.append(1, rec(outcome))
    assert log.state(1).consecutive_failures == 4
    state = log.append(1, rec("success"))
    assert state.consecutive_failures == 0
    assert state.color(3) == "green"


def test_skipped_and_missed_do_not_count_or_change_colour(tmp_path):
    log = make_log(tmp_path)
    log.append(1, rec("success"))
    log.append(1, rec("skipped_locked"))
    log.append(1, rec("missed", reason="machine offline"))
    state = log.state(1)
    assert state.consecutive_failures == 0 and state.color(3) == "green"
    assert state.last_outcome == "missed"

    log.append(1, rec("failed"))
    log.append(1, rec("failed"))
    log.append(1, rec("skipped_locked"))
    log.append(1, rec("missed"))
    assert log.state(1).consecutive_failures == 2
    assert log.state(1).color(2) == "red"


def test_colour_is_grey_until_a_counted_run_exists(tmp_path):
    log = make_log(tmp_path)
    assert log.state(1).color(3) == "grey"
    log.append(1, rec("missed"))
    assert log.state(1).color(3) == "grey"


def test_red_needs_the_threshold(tmp_path):
    log = make_log(tmp_path)
    log.append(1, rec("failed"))
    assert log.state(1).color(2) == "green"
    log.append(1, rec("failed"))
    assert log.state(1).color(2) == "red"


def test_state_is_rebuilt_from_the_file(tmp_path):
    log = make_log(tmp_path)
    log.append(1, rec("success"))
    log.append(1, rec("failed", reason="exit code 1"))
    again = make_log(tmp_path)
    state = again.state(1)
    assert (state.consecutive_failures, state.last_outcome, state.last_reason) == (1, "failed", "exit code 1")
    assert again.append(1, rec("failed"))  # sequence continues after reload
    assert [r["seq"] for r in again.read(1)[0]] == [3, 2, 1]


def test_paging_is_newest_first(tmp_path):
    log = make_log(tmp_path)
    for _ in range(5):
        log.append(1, rec("success"))
    page, nxt = log.read(1, limit=2)
    assert [r["seq"] for r in page] == [5, 4] and nxt == 4
    page, nxt = log.read(1, limit=2, before=nxt)
    assert [r["seq"] for r in page] == [3, 2] and nxt == 2
    page, nxt = log.read(1, limit=2, before=nxt)
    assert [r["seq"] for r in page] == [1] and nxt is None


def test_retention_keeps_whichever_is_larger(tmp_path):
    old = now_utc() - timedelta(days=200)
    log = make_log(tmp_path, log_keep_runs=3, log_keep_days=90)
    for _ in range(4):
        log.append(1, rec("success", started=old))
    assert len(log.read(1, limit=100)[0]) == 3

    log = make_log(tmp_path / "b", log_keep_runs=3, log_keep_days=90)
    for _ in range(2):
        log.append(1, rec("success", started=old))
    for _ in range(5):
        log.append(1, rec("success"))
    kept = log.read(1, limit=100)[0]
    assert len(kept) == 5 and all(r["started_at"] >= iso(now_utc() - timedelta(days=1)) for r in kept)

    log = make_log(tmp_path / "c", log_keep_runs=3, log_keep_days=90)
    for _ in range(6):
        log.append(1, rec("success"))
    assert len(log.read(1, limit=100)[0]) == 6


def test_output_tail_is_capped_keeping_the_end(tmp_path):
    log = make_log(tmp_path, log_output_tail_kb=1)
    log.append(1, rec("failed", stdout_tail="a" * 5000 + "END", stderr_tail="x"))
    row = log.read(1)[0][0]
    assert len(row["stdout_tail"]) == 1024 and row["stdout_tail"].endswith("END")
    assert row["stderr_tail"] == "x"


def test_cap_tail_never_splits_a_character():
    assert cap_tail("é" * 600, 1).encode("utf-8")
    assert cap_tail(None, 1) == ""


def test_delete_removes_the_log(tmp_path):
    log = make_log(tmp_path)
    log.append(1, rec("failed"))
    log.delete(1)
    assert log.read(1) == ([], None) and log.state(1).consecutive_failures == 0
