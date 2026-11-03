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
    from _state.database import Database
    raw = json.loads(Database.for_dir(tmp_path).query("SELECT body FROM schedules")[0][0])
    assert raw["controls"]["active_from"] == "2026-02-28T22:00:00Z"


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


def test_corrupt_legacy_schedules_file_stops_startup_and_is_left_alone(tmp_path):
    import pytest

    from _state.importer import StateImportError

    path = tmp_path / "scheduler" / "schedules.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateImportError, match="schedules.json"):
        ScheduleStore(tmp_path)
    assert path.read_text(encoding="utf-8") == "{not json"


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


# ---- age-based autoclean (settings.scheduler.log_max_age_days) ----

def aged(days: float, outcome="success", **extra) -> dict:
    stamp = iso(now_utc() - timedelta(days=days))
    return {**rec(outcome, **extra), "started_at": stamp, "ended_at": stamp}


def seqs(log, schedule_id):
    return [seq for (seq,) in log._db.query("SELECT seq FROM schedule_runs WHERE schedule_id=? ORDER BY seq", (schedule_id,))]


def test_the_age_limit_defaults_to_30_days_and_is_validated():
    assert SchedulerSettings().log_max_age_days == 30
    for bad in (0, -1, 4000):
        with pytest.raises(Exception):
            SchedulerSettings(log_max_age_days=bad)


def test_runs_older_than_the_limit_are_deleted_and_the_boundary_is_exact(tmp_path):
    log = make_log(tmp_path)
    for days in (45, 31, 30.5, 29.5, 5, 0):
        log.append(1, aged(days))
    assert log.purge_expired(30, pause=0) == 3
    assert seqs(log, 1) == [4, 5, 6]


def test_the_newest_run_of_every_schedule_survives_even_when_ancient(tmp_path):
    log = make_log(tmp_path)
    for days in (200, 100):
        log.append(1, aged(days))
    log.append(2, aged(90))
    assert log.purge_expired(30, pause=0) == 1
    assert seqs(log, 1) == [2] and seqs(log, 2) == [1]


def test_unfinished_or_odd_rows_and_in_progress_runs_are_kept(tmp_path):
    log = make_log(tmp_path)
    old = iso(now_utc() - timedelta(days=90))
    rows = [
        {**aged(90), "outcome": None},                    # unfinished: no outcome
        {**aged(90), "ended_at": None},                   # no end time
        {**aged(90), "ended_at": "not a date"},           # odd end time
        {**aged(90), "ended_at": 12345},                  # wrong type
        {**aged(90), "run_id": "in-flight"},              # still running
        {**aged(90)},                                     # the only one that qualifies
        aged(0),                                          # newest row
    ]
    for row in rows:
        log.append(1, row)
    assert log.purge_expired(30, protected_run_ids={"in-flight"}, pause=0) == 1
    assert seqs(log, 1) == [1, 2, 3, 4, 5, 7]
    assert old  # silence unused


def test_an_unreadable_body_is_kept(tmp_path):
    log = make_log(tmp_path)
    for seq, body in ((1, "{broken"), (2, json.dumps({**aged(90), "seq": 2})), (3, json.dumps({**aged(0), "seq": 3}))):
        log._db.write(lambda c, seq=seq, body=body: c.execute("INSERT INTO schedule_runs(schedule_id, seq, body) VALUES (1, ?, ?)", (seq, body)))
    assert log.purge_expired(30, pause=0) == 1
    assert seqs(log, 1) == [1, 3]


def test_a_large_first_cleanup_is_deleted_in_batches(tmp_path, monkeypatch):
    log = make_log(tmp_path, log_keep_runs=100000, log_keep_days=3650)
    for n in range(250):
        log.append(1, aged(60 + n % 5))
    log.append(1, aged(0))
    commits = []
    real = log._db.write
    monkeypatch.setattr(log._db, "write", lambda fn: (commits.append(1), real(fn))[1])
    assert log.purge_expired(30, batch=100, pause=0) == 250
    assert len(commits) == 3 and seqs(log, 1) == [251]


def test_sequence_numbers_stay_monotonic_after_rows_disappear(tmp_path):
    log = make_log(tmp_path)
    for days in (90, 80, 0):
        log.append(1, aged(days))
    log.purge_expired(30, pause=0)
    log.append(1, aged(0))
    assert seqs(log, 1) == [3, 4]
    restarted = make_log(tmp_path)
    restarted.append(1, aged(0))
    assert seqs(restarted, 1) == [3, 4, 5]


def test_history_paging_and_state_still_work_after_a_purge(tmp_path):
    log = make_log(tmp_path)
    for n, days in enumerate((90, 80, 70, 2, 1, 0)):
        log.append(1, aged(days, "failed" if n < 3 else "success"))
    log.purge_expired(30, pause=0)
    page, more = log.read(1, limit=2)
    assert [r["seq"] for r in page] == [6, 5] and more == 5
    page, more = log.read(1, limit=2, before=more)
    assert [r["seq"] for r in page] == [4] and more is None
    assert log.state(1).last_outcome == "success"


def test_the_count_limit_still_trims_and_the_two_limits_combine(tmp_path):
    log = make_log(tmp_path, log_keep_runs=3, log_keep_days=1)
    for days in (50, 40, 3, 2, 1.5, 0.2, 0.1):
        log.append(1, aged(days))
    # count limit (append time): beyond the newest 3, only rows older than 1 day go; age limit (daily): older than 30 days go
    assert seqs(log, 1) == [5, 6, 7]
    log2 = make_log(tmp_path / "b", log_keep_runs=1000, log_keep_days=90)
    for days in (50, 40, 3, 2, 0):
        log2.append(1, aged(days))
    assert seqs(log2, 1) == [1, 2, 3, 4, 5]  # the count limit alone keeps all of them
    assert log2.purge_expired(30, pause=0) == 2 and seqs(log2, 1) == [3, 4, 5]


def test_purge_failure_never_stops_the_scheduler_loop_task(tmp_path, monkeypatch, caplog):
    import logging

    from _scheduler.loop import SchedulerLoop

    loop = SchedulerLoop.__new__(SchedulerLoop)
    loop._runlog = make_log(tmp_path)
    loop.active = {}

    class Cfg:
        log_max_age_days = 30

    loop._config_store = type("S", (), {"get": lambda self: type("C", (), {"scheduler": Cfg()})()})()
    monkeypatch.setattr(loop._runlog, "purge_expired", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    with caplog.at_level(logging.WARNING, logger="relay.scheduler.loop"):
        loop._purge_history()
    assert "run history cleanup failed" in caplog.text


async def test_the_loop_purges_at_start_and_then_only_once_a_day(tmp_path):
    import asyncio

    from _scheduler import loop as loop_mod
    from _scheduler.loop import SchedulerLoop

    log = make_log(tmp_path)
    for days in (90, 0):
        log.append(1, aged(days))
    sched = SchedulerLoop.__new__(SchedulerLoop)
    sched._runlog, sched.active, sched.enabled, sched._next_purge, sched._purge_tasks = log, {}, True, 0.0, set()
    sched._config_store = type("S", (), {"get": lambda self: type("C", (), {"scheduler": SchedulerSettings()})()})()
    sched._maybe_purge()
    await asyncio.gather(*list(sched._purge_tasks))
    assert seqs(log, 1) == [2]
    log.append(1, aged(90))
    sched._maybe_purge()  # not due again for a day
    assert not sched._purge_tasks and seqs(log, 1) == [2, 3]
    sched.enabled = False
    sched._next_purge = 0.0
    sched._maybe_purge()
    assert not sched._purge_tasks and loop_mod.PURGE_INTERVAL_SECONDS == 86400
