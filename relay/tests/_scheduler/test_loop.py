from __future__ import annotations

import asyncio
import json
import logging

from _alerts.dedup import AlertKey
from _scheduler.inflight import InflightStore
from _scheduler.utc import iso

from .conftest import SCRIPT
from tests._state_helpers import read_doc


def raw_config_update(harness, **scheduler):
    path = harness.config_store.path
    data = json.loads(path.read_text())
    data["scheduler"].update(scheduler)
    path.write_text(json.dumps(data))
    harness.config_store.force_reload()


async def run_once(harness, trigger="manual", **overrides):
    schedule = harness.add(**overrides)
    harness.loop.start()
    dispatched = harness.loop.dispatch(schedule, trigger)
    await harness.settle()
    return schedule, dispatched, harness.runs(schedule.id)


async def test_successful_run_is_logged_with_output(harness):
    harness.endpoint.add_script(SCRIPT, polls=2, stdout="done")
    schedule, dispatched, runs = await run_once(harness)
    assert dispatched.status == "started"
    row = runs[0]
    assert (row["outcome"], row["exit_code"], row["attempts"], row["trigger"]) == ("success", 0, 1, "manual")
    assert row["run_id"] == dispatched.run_id and row["stdout_tail"] == "done"
    assert harness.runlog.state(schedule.id).color(3) == "green"


async def test_args_timeout_and_tail_cap_are_sent_to_the_endpoint(harness):
    _, dispatched, _ = await run_once(harness, args=["--x", "1"], controls={"timeout_s": 42, "retry_delay_s": 0})
    start = next(r for r in harness.endpoint.requests if r.url.path == "/runs")
    body = json.loads(start.content)
    assert body["args"] == ["--x", "1"] and body["timeout_s"] == 42 and body["output_tail_kb"] == 64
    assert body["run_id"] == f"{dispatched.run_id}-1"


async def test_default_timeout_comes_from_config_minutes(harness):
    raw_config_update(harness, default_timeout_minutes=2)
    await run_once(harness, controls={"retry_delay_s": 0})
    start = next(r for r in harness.endpoint.requests if r.url.path == "/runs")
    assert json.loads(start.content)["timeout_s"] == 120


async def test_nonzero_exit_is_failed_and_custom_success_codes_apply(harness):
    harness.endpoint.add_script(SCRIPT, exit_code=3)
    _, _, runs = await run_once(harness)
    assert (runs[0]["outcome"], runs[0]["reason"]) == ("failed", "exit code 3")

    raw_config_update(harness, success_exit_codes=[0, 3])
    harness.endpoint.runs.clear()
    schedule = harness.store.list()[0]
    harness.loop.dispatch(schedule, "manual")
    await harness.settle()
    assert harness.runs(schedule.id)[-1]["outcome"] == "success"


async def test_timeout_and_lost_statuses_count_as_failures(harness):
    harness.endpoint.add_script(SCRIPT, final="timeout", exit_code=-1)
    schedule, _, runs = await run_once(harness)
    assert runs[0]["outcome"] == "timeout"
    harness.endpoint.add_script(SCRIPT, final="lost", exit_code=None)
    harness.loop.dispatch(schedule, "manual")
    await harness.settle()
    assert harness.runs(schedule.id)[-1]["outcome"] == "lost"
    assert harness.runlog.state(schedule.id).consecutive_failures == 2


async def test_retries_make_one_run_that_fails_once(harness):
    harness.endpoint.add_script(SCRIPT, exit_code=1)
    schedule, _, runs = await run_once(harness, controls={"retries": 2, "retry_delay_s": 0})
    assert len(runs) == 1
    assert (runs[0]["outcome"], runs[0]["attempts"]) == ("failed", 3)
    assert harness.runlog.state(schedule.id).consecutive_failures == 1
    assert sum(1 for r in harness.endpoint.requests if r.url.path == "/runs" and r.method == "POST") == 3


async def test_a_retry_that_succeeds_is_a_success(harness):
    harness.endpoint.add_script(SCRIPT, sequence=[1, 0])
    schedule, _, runs = await run_once(harness, controls={"retries": 1, "retry_delay_s": 0})
    assert len(runs) == 1 and runs[0]["attempts"] == 2 and runs[0]["outcome"] == "success"
    assert harness.runlog.state(schedule.id).consecutive_failures == 0


async def test_script_not_found_fails_without_retrying(harness):
    schedule, _, runs = await run_once(harness, script=r"D:\missing.cmd", controls={"retries": 3, "retry_delay_s": 0})
    assert runs[0]["outcome"] == "script_not_found" and "missing.cmd" in runs[0]["reason"]
    assert runs[0]["attempts"] == 1
    assert harness.runlog.state(schedule.id).consecutive_failures == 1


async def test_machine_lock_gives_skipped_locked_that_does_not_count(harness):
    harness.endpoint.add_script(SCRIPT, polls=100)
    harness.endpoint.runs["other-1"] = {
        "script": SCRIPT.lower(), "status": "running", "exit_code": None, "started_at": None, "ended_at": None,
        "duration_ms": None, "stdout_tail": "", "stderr_tail": "", "polls_seen": 0,
    }
    schedule, _, runs = await run_once(harness)
    assert runs[0]["outcome"] == "skipped_locked" and "other-1" in runs[0]["reason"]
    assert harness.runlog.state(schedule.id).consecutive_failures == 0
    assert harness.runlog.state(schedule.id).color(3) == "grey"


async def test_no_overlap_skips_a_second_run_of_the_same_schedule(harness):
    harness.endpoint.add_script(SCRIPT, polls=1000)
    schedule = harness.add()
    harness.loop.start()
    first = harness.loop.dispatch(schedule, "scheduled")
    second = harness.loop.dispatch(schedule, "manual")
    assert (first.status, second.status) == ("started", "skipped_locked")
    assert harness.loop.is_running(schedule.id)
    assert harness.runs(schedule.id)[0]["outcome"] == "skipped_locked"
    await harness.until(lambda: f"{first.run_id}-1" in harness.endpoint.runs)
    harness.endpoint.finish(f"{first.run_id}-1")
    await harness.settle()
    assert harness.runlog.state(schedule.id).last_outcome == "success"


async def test_overlap_allowed_when_no_overlap_is_off(harness):
    harness.endpoint.add_script(SCRIPT, polls=1000)
    schedule = harness.add(controls={"no_overlap": False, "retry_delay_s": 0})
    harness.loop.start()
    first = harness.loop.dispatch(schedule, "scheduled")
    second = harness.loop.dispatch(schedule, "scheduled")
    assert second.status == "started" and first.run_id != second.run_id
    await harness.loop.shutdown()


async def test_offline_machine_logs_missed_not_failure(harness):
    harness.registry.state_for(1).status = "offline"
    schedule, dispatched, runs = await run_once(harness)
    assert dispatched.status == "missed"
    assert runs[0]["outcome"] == "missed" and runs[0]["reason"] == "machine offline"
    assert harness.runlog.state(schedule.id).consecutive_failures == 0
    assert not any(r.url.path == "/runs" for r in harness.endpoint.requests)


async def test_endpoint_down_or_auth_rejected_at_fire_time_logs_missed(harness):
    harness.endpoint.down = True
    await harness.health.tick()
    await harness.health.tick()
    schedule, _, runs = await run_once(harness)
    assert runs[0]["outcome"] == "missed" and runs[0]["reason"] == "scheduler endpoint down"

    harness.endpoint.down = False
    harness.endpoint.reject_keys = True
    await harness.health.tick()
    harness.loop.dispatch(schedule, "scheduled")
    assert "auth rejected" in harness.runs(schedule.id)[-1]["reason"]
    assert harness.runlog.state(schedule.id).consecutive_failures == 0


async def test_unprobed_endpoint_that_refuses_the_start_is_missed(harness):
    harness.endpoint.down = True
    schedule, _, runs = await run_once(harness)
    assert runs[0]["outcome"] == "missed" and "unreachable" in runs[0]["reason"]
    assert harness.runlog.state(schedule.id).consecutive_failures == 0


async def test_rejected_key_at_start_is_missed_with_auth_text(harness):
    harness.endpoint.reject_keys = True
    _, _, runs = await run_once(harness)
    assert runs[0]["outcome"] == "missed" and "auth rejected" in runs[0]["reason"]


async def test_machine_without_scheduler_flag_is_missed(harness):
    raw = harness.config_store.path.read_text().replace('"machine_scheduler": true', '"machine_scheduler": false')
    harness.config_store.path.write_text(raw)
    harness.config_store.force_reload()
    _, dispatched, runs = await run_once(harness)
    assert dispatched.status == "missed" and "not enabled" in runs[0]["reason"]


async def test_threshold_raises_the_alert_and_a_success_releases_it(harness):
    harness.endpoint.add_script(SCRIPT, exit_code=1)
    schedule = harness.add(fail_threshold=2)
    harness.loop.start()
    key = AlertKey("schedule", str(schedule.id))

    harness.loop.dispatch(schedule, "scheduled")
    await harness.settle()
    assert not harness.schedule_incidents.is_open(key)
    harness.loop.dispatch(schedule, "scheduled")
    await harness.settle()
    record = harness.schedule_incidents.get(key)
    assert record["consecutive_failures"] == 2 and record["severity"] == 3

    harness.loop.dispatch(schedule, "scheduled")
    await harness.settle()
    assert harness.schedule_incidents.get(key)["consecutive_failures"] == 3

    harness.endpoint.add_script(SCRIPT, exit_code=0)
    harness.loop.dispatch(schedule, "scheduled")
    await harness.settle()
    assert not harness.schedule_incidents.is_open(key)
    assert harness.runlog.state(schedule.id).color(2) == "green"
    assert len(harness.runs(schedule.id)) == 4


async def test_alert_uses_the_config_default_threshold_and_survives_missed_runs(harness):
    raw_config_update(harness, default_fail_threshold=1)
    harness.endpoint.add_script(SCRIPT, exit_code=1)
    schedule, _, _ = await run_once(harness)
    key = AlertKey("schedule", str(schedule.id))
    assert harness.schedule_incidents.is_open(key)
    harness.registry.state_for(1).status = "offline"
    harness.loop.dispatch(schedule, "scheduled")
    assert harness.schedule_incidents.is_open(key)


async def test_deleting_a_schedule_releases_its_alert(harness):
    harness.endpoint.add_script(SCRIPT, exit_code=1)
    schedule, _, _ = await run_once(harness, fail_threshold=1)
    key = AlertKey("schedule", str(schedule.id))
    assert harness.schedule_incidents.is_open(key)
    harness.store.delete(schedule.id)
    harness.loop.sync_alerts(harness.config_store.get())
    assert not harness.schedule_incidents.is_open(key)


async def test_tick_fires_due_interval_schedules_once_per_period(harness):
    schedule = harness.add(trigger={"kind": "interval", "every_seconds": 60, "start_at": iso(harness.clock.now)})
    harness.loop.start()
    await harness.loop.tick()
    assert harness.runs(schedule.id) == []
    harness.clock.advance(seconds=61)
    await harness.loop.tick()
    await harness.settle()
    await harness.loop.tick()
    await harness.settle()
    assert [r["trigger"] for r in harness.runs(schedule.id)] == ["scheduled"]
    harness.clock.advance(seconds=60)
    await harness.loop.tick()
    await harness.settle()
    assert len(harness.runs(schedule.id)) == 2


async def test_disabled_schedule_does_not_fire(harness):
    schedule = harness.add(enabled=False, trigger={"kind": "interval", "every_seconds": 1, "start_at": iso(harness.clock.now)})
    harness.loop.start()
    await harness.loop.tick()
    harness.clock.advance(seconds=10)
    await harness.loop.tick()
    assert harness.runs(schedule.id) == [] and not harness.loop.active


async def test_active_window_blocks_firing_outside_it(harness):
    schedule = harness.add(
        trigger={"kind": "interval", "every_seconds": 60, "start_at": iso(harness.clock.now)},
        controls={"active_from": "2026-03-01T13:00:00Z", "retry_delay_s": 0},
    )
    harness.loop.start()
    await harness.loop.tick()
    harness.clock.advance(seconds=120)
    await harness.loop.tick()
    assert harness.runs(schedule.id) == []
    harness.clock.advance(hours=1)
    await harness.loop.tick()
    await harness.settle()
    assert len(harness.runs(schedule.id)) == 1


async def test_editing_the_trigger_resets_the_clock_instead_of_firing_a_backlog(harness):
    schedule = harness.add(trigger={"kind": "interval", "every_seconds": 60, "start_at": iso(harness.clock.now)})
    harness.loop.start()
    await harness.loop.tick()
    harness.clock.advance(hours=5)
    harness.store.replace(schedule.id, schedule_input_like(schedule, every=30, start=harness.clock.now))
    await harness.loop.tick()
    assert harness.runs(schedule.id) == []


def schedule_input_like(schedule, every, start):
    from .conftest import schedule_input
    return schedule_input(trigger={"kind": "interval", "every_seconds": every, "start_at": iso(start)})


def _prepare_downtime(harness, **controls):
    schedule = harness.add(
        trigger={"kind": "cron", "cron": "* * * * *"}, controls={"retry_delay_s": 0, **controls},
    )
    InflightStore(harness.state_dir).save([], iso(harness.clock.now))
    harness.clock.advance(minutes=10)
    return schedule


async def test_missed_fire_during_relay_downtime_is_logged_once_by_default(harness):
    schedule = _prepare_downtime(harness)
    harness.loop.start()
    await harness.loop.tick()
    rows = harness.runs(schedule.id)
    assert len(rows) == 1 and rows[0]["outcome"] == "missed" and "relay was not running" in rows[0]["reason"]
    await harness.loop.tick()
    assert len(harness.runs(schedule.id)) == 1
    assert harness.runlog.state(schedule.id).consecutive_failures == 0


async def test_run_if_missed_runs_once_as_a_catch_up(harness):
    schedule = _prepare_downtime(harness, run_if_missed=True)
    harness.loop.start()
    await harness.loop.tick()
    await harness.settle()
    rows = harness.runs(schedule.id)
    assert [(r["trigger"], r["outcome"]) for r in rows] == [("missed_catchup", "success")]


async def test_machine_online_event_fires_schedules_for_that_machine(harness):
    schedule = harness.add(trigger={"kind": "event", "event": "machine_online"})
    harness.loop.start()
    harness.loop.on_machine_online(2)
    assert not harness.loop.active
    harness.loop.on_machine_online(1)
    await harness.settle()
    assert [r["trigger"] for r in harness.runs(schedule.id)] == ["event"]


async def test_relay_start_event_waits_for_the_machine_then_fires(harness):
    harness.registry.state_for(1).status = "pending"
    schedule = harness.add(trigger={"kind": "event", "event": "relay_start"})
    harness.loop.start()
    await harness.loop.tick()
    assert harness.runs(schedule.id) == [] and not harness.loop.active
    harness.registry.state_for(1).status = "online"
    await harness.loop.tick()
    await harness.settle()
    assert [r["outcome"] for r in harness.runs(schedule.id)] == ["success"]
    await harness.loop.tick()
    assert len(harness.runs(schedule.id)) == 1


async def test_reboot_cron_behaves_like_relay_start(harness):
    schedule = harness.add(trigger={"kind": "cron", "cron": "@reboot"})
    harness.loop.start()
    await harness.loop.tick()
    await harness.settle()
    assert len(harness.runs(schedule.id)) == 1


async def test_after_schedule_chains_on_outcome(harness):
    harness.endpoint.add_script(r"D:\jobs\two.cmd")
    first = harness.add()
    on_success = harness.add(name="ok", script=r"D:\jobs\two.cmd", trigger={"kind": "event", "event": "after_schedule", "after_schedule_id": first.id, "on": "success"})
    on_failure = harness.add(name="bad", script=r"D:\jobs\two.cmd", trigger={"kind": "event", "event": "after_schedule", "after_schedule_id": first.id, "on": "failure"}, controls={"no_overlap": False, "retry_delay_s": 0})
    harness.loop.start()
    harness.loop.dispatch(first, "manual")
    await harness.settle()
    assert len(harness.runs(on_success.id)) == 1 and harness.runs(on_failure.id) == []

    harness.endpoint.add_script(SCRIPT, exit_code=1)
    harness.endpoint.runs.clear()
    harness.loop.dispatch(first, "manual")
    await harness.settle()
    assert len(harness.runs(on_success.id)) == 1 and len(harness.runs(on_failure.id)) == 1


async def test_skipped_runs_do_not_trigger_after_schedule(harness):
    first = harness.add()
    follower = harness.add(name="f", trigger={"kind": "event", "event": "after_schedule", "after_schedule_id": first.id, "on": "any"})
    harness.registry.state_for(1).status = "offline"
    harness.loop.start()
    harness.loop.dispatch(first, "manual")
    assert harness.runs(follower.id) == []


async def test_changes_notify_listeners(harness):
    schedule = harness.add()
    harness.loop.start()
    harness.loop.dispatch(schedule, "manual")
    await harness.settle()
    assert harness.changes >= 2


async def test_inflight_run_is_resumed_and_recorded_after_a_restart(harness):
    harness.endpoint.add_script(SCRIPT, final="hang")
    schedule = harness.add()
    harness.loop.start()
    dispatched = harness.loop.dispatch(schedule, "scheduled")
    await harness.until(lambda: f"{dispatched.run_id}-1" in harness.endpoint.runs)
    await harness.loop.shutdown()
    assert harness.runs(schedule.id) == []
    saved = read_doc(harness.state_dir, "scheduler/inflight")
    assert [r["run_id"] for r in saved["runs"]] == [dispatched.run_id]

    endpoint_id = f"{dispatched.run_id}-1"
    harness.endpoint.finish(endpoint_id, exit_code=0)
    restarted = harness.new_loop()
    assert restarted.start() is True
    assert restarted.is_running(schedule.id)
    await harness.settle(restarted)
    rows = harness.runs(schedule.id)
    assert len(rows) == 1 and rows[0]["run_id"] == dispatched.run_id and rows[0]["outcome"] == "success"
    assert not any(r.url.path == "/runs" and r.method == "POST" and json.loads(r.content)["run_id"] != endpoint_id for r in harness.endpoint.requests)
    assert read_doc(harness.state_dir, "scheduler/inflight")["runs"] == []
    await restarted.shutdown()


async def test_resumed_run_whose_endpoint_forgot_it_is_lost(harness):
    harness.endpoint.add_script(SCRIPT, final="hang")
    schedule = harness.add()
    harness.loop.start()
    dispatched = harness.loop.dispatch(schedule, "scheduled")
    await harness.until(lambda: f"{dispatched.run_id}-1" in harness.endpoint.runs)
    await harness.loop.shutdown()
    harness.endpoint.runs.clear()
    restarted = harness.new_loop()
    restarted.start()
    await harness.settle(restarted)
    assert harness.runs(schedule.id)[0]["outcome"] == "lost"
    await restarted.shutdown()


async def test_resume_skips_runs_of_deleted_schedules(harness):
    harness.endpoint.add_script(SCRIPT, final="hang")
    schedule = harness.add()
    harness.loop.start()
    harness.loop.dispatch(schedule, "scheduled")
    await harness.loop.shutdown()
    harness.store.delete(schedule.id)
    restarted = harness.new_loop()
    restarted.start()
    assert not restarted.active
    await restarted.shutdown()


async def test_no_timeout_by_default_sends_zero(harness):
    await run_once(harness, controls={"retry_delay_s": 0})
    start = next(r for r in harness.endpoint.requests if r.url.path == "/runs")
    assert json.loads(start.content)["timeout_s"] == 0


async def test_timeout_zero_is_allowed_per_schedule_and_per_machine(tmp_path, config_dict):
    from .test_machine_defaults import detail, machine, with_machine_scheduler

    assert machine({"enabled": True, "timeout_minutes": 0}).scheduler_defaults.timeout_minutes == 0
    h = with_machine_scheduler(tmp_path, config_dict, {"enabled": True, "timeout_minutes": 0}, default_timeout_minutes=5)
    d = detail(h, h.add(controls={}))
    assert d["effective"]["timeout_s"] == 0 and d["effective_source"]["timeout_s"] == "machine"
    assert d["timeout_display"] == "none"
    d = detail(h, h.add(controls={"timeout_s": 0}))
    assert d["effective_source"]["timeout_s"] == "schedule" and d["effective"]["timeout_s"] == 0
    d = detail(h, h.add(controls={"timeout_s": 90}))
    assert d["timeout_display"] == "90s"


async def test_no_timeout_run_keeps_polling_through_an_endpoint_outage(harness, monkeypatch):
    import _scheduler.loop as loop_mod

    monkeypatch.setattr(loop_mod, "TIMEOUT_GUARD_SECONDS", -1000)
    harness.endpoint.add_script(SCRIPT, polls=1)
    schedule = harness.add(controls={"retry_delay_s": 0})
    harness.loop.start()
    dispatched = harness.loop.dispatch(schedule, "scheduled")
    await harness.until(lambda: f"{dispatched.run_id}-1" in harness.endpoint.runs)
    harness.endpoint.down = True
    await harness.until(lambda: harness.loop.is_waiting(schedule.id))
    await asyncio.sleep(0.3)
    assert harness.loop.is_running(schedule.id) and harness.runs(schedule.id) == []

    from _scheduler.wire import schedule_row
    harness.health.state_for(1).state = "up"
    row = schedule_row(schedule, harness.config_store.get(), harness.health, harness.runlog, harness.loop)
    assert row["running"] is True and row["reason"] == "waiting for endpoint"

    harness.endpoint.down = False
    await harness.settle()
    assert harness.runs(schedule.id)[0]["outcome"] == "success"
    assert not harness.loop.is_waiting(schedule.id)


async def test_waiting_flag_is_not_persisted(harness):
    harness.endpoint.add_script(SCRIPT, final="hang")
    schedule = harness.add()
    harness.loop.start()
    dispatched = harness.loop.dispatch(schedule, "scheduled")
    await harness.until(lambda: f"{dispatched.run_id}-1" in harness.endpoint.runs)
    harness.endpoint.down = True
    await harness.until(lambda: harness.loop.is_waiting(schedule.id))
    saved = read_doc(harness.state_dir, "scheduler/inflight")
    assert "waiting" not in saved["runs"][0]
    await harness.loop.shutdown()


async def test_endpoint_lost_status_is_the_only_lost_for_no_timeout_runs(harness):
    harness.endpoint.add_script(SCRIPT, final="lost", exit_code=None)
    _, _, runs = await run_once(harness)
    assert runs[0]["outcome"] == "lost"


async def test_run_with_a_timeout_still_gets_a_guard(harness, monkeypatch):
    import _scheduler.loop as loop_mod

    monkeypatch.setattr(loop_mod, "TIMEOUT_GUARD_SECONDS", -1000)
    harness.endpoint.add_script(SCRIPT, final="hang")
    schedule = harness.add(controls={"timeout_s": 1, "retry_delay_s": 0})
    harness.loop.start()
    dispatched = harness.loop.dispatch(schedule, "scheduled")
    await harness.until(lambda: f"{dispatched.run_id}-1" in harness.endpoint.runs)
    harness.endpoint.down = True
    await harness.settle()
    assert harness.runs(schedule.id)[0]["outcome"] == "lost"


async def start_hanging(harness, **overrides):
    harness.endpoint.add_script(SCRIPT, final="hang")
    schedule = harness.add(**overrides)
    harness.loop.start()
    dispatched = harness.loop.dispatch(schedule, "scheduled")
    await harness.until(lambda: f"{dispatched.run_id}-1" in harness.endpoint.runs)
    return schedule, dispatched


async def test_stop_kills_through_the_endpoint_and_logs_cancelled(harness):
    schedule, dispatched = await start_hanging(harness)
    result = await harness.loop.stop_runs(schedule.id)
    assert result == {"run_id": dispatched.run_id, "status": "stopping"}
    assert any(r.url.path.endswith(f"{dispatched.run_id}-1/kill") for r in harness.endpoint.requests)
    await harness.settle()
    row = harness.runs(schedule.id)[0]
    assert (row["outcome"], row["reason"], row["run_id"]) == ("cancelled", "stopped by user", dispatched.run_id)
    assert not harness.loop.is_running(schedule.id)
    assert read_doc(harness.state_dir, "scheduler/inflight")["runs"] == []


async def test_stop_abandons_when_the_endpoint_is_unreachable(harness):
    schedule, dispatched = await start_hanging(harness)
    harness.endpoint.down = True
    result = await harness.loop.stop_runs(schedule.id)
    assert result == {"run_id": dispatched.run_id, "status": "cancelled"}
    assert not harness.loop.is_running(schedule.id) and not harness.loop.active
    row = harness.runs(schedule.id)[0]
    assert row["outcome"] == "cancelled"
    assert row["reason"].startswith("abandoned while endpoint unreachable (script may still be running on the machine")
    assert read_doc(harness.state_dir, "scheduler/inflight")["runs"] == []
    harness.endpoint.down = False
    await asyncio.sleep(0.1)
    assert len(harness.runs(schedule.id)) == 1


async def test_stop_abandons_on_auth_rejected_too(harness):
    schedule, _ = await start_hanging(harness)
    harness.endpoint.reject_keys = True
    assert (await harness.loop.stop_runs(schedule.id))["status"] == "cancelled"
    assert harness.runs(schedule.id)[0]["outcome"] == "cancelled"


async def test_cancelled_does_not_count_or_change_colour_and_does_not_retry(harness):
    schedule, _ = await start_hanging(harness, controls={"retries": 3, "retry_delay_s": 0})
    harness.endpoint.add_script(SCRIPT, exit_code=0)
    await harness.loop.stop_runs(schedule.id)
    await harness.settle()
    state = harness.runlog.state(schedule.id)
    assert state.consecutive_failures == 0 and state.color(3) == "grey" and state.last_outcome == "cancelled"
    assert len(harness.runs(schedule.id)) == 1
    assert sum(1 for r in harness.endpoint.requests if r.url.path == "/runs" and r.method == "POST") == 1


async def test_cancelled_keeps_a_green_row_green_and_a_failure_streak(harness):
    harness.endpoint.add_script(SCRIPT, exit_code=1)
    schedule, _, _ = await run_once(harness)
    harness.endpoint.add_script(SCRIPT, final="hang")
    harness.endpoint.runs.clear()
    dispatched = harness.loop.dispatch(schedule, "manual")
    await harness.until(lambda: f"{dispatched.run_id}-1" in harness.endpoint.runs)
    await harness.loop.stop_runs(schedule.id)
    await harness.settle()
    assert harness.runlog.state(schedule.id).consecutive_failures == 1


async def test_stop_during_retry_wait_cancels_without_touching_the_endpoint(harness):
    harness.endpoint.add_script(SCRIPT, exit_code=1)
    schedule = harness.add(controls={"retries": 1, "retry_delay_s": 60})
    harness.loop.start()
    harness.loop.dispatch(schedule, "scheduled")
    await harness.until(lambda: any(a.phase == "retry_wait" for a in harness.loop.active.values()))
    before = len(harness.endpoint.requests)
    assert (await harness.loop.stop_runs(schedule.id))["status"] == "cancelled"
    assert len(harness.endpoint.requests) == before
    row = harness.runs(schedule.id)[0]
    assert row["outcome"] == "cancelled" and row["reason"] == "stopped by user"


async def test_stop_with_nothing_running_returns_none(harness):
    schedule = harness.add()
    harness.loop.start()
    assert await harness.loop.stop_runs(schedule.id) is None


async def test_an_abandoned_schedule_can_be_edited_again(harness):
    schedule, _ = await start_hanging(harness)
    harness.endpoint.down = True
    await harness.loop.stop_runs(schedule.id)
    assert not harness.loop.is_running(schedule.id)
    harness.endpoint.down = False
    again = harness.loop.dispatch(schedule, "manual")
    assert again.status == "started"
    await harness.loop.shutdown()


async def test_script_path_and_interpreter_are_stored_in_the_run_log(harness):
    harness.endpoint.add_script(SCRIPT, script_path=r"C:\services\jobs\ok.cmd", interpreter="cmd.exe")
    _, _, runs = await run_once(harness)
    assert runs[0]["script_path"] == r"C:\services\jobs\ok.cmd" and runs[0]["interpreter"] == "cmd.exe"
    assert runs[0]["outcome"] == "success"


async def test_log_rows_without_endpoint_paths_have_nulls(harness):
    _, _, runs = await run_once(harness)
    assert runs[0]["script_path"] is None and runs[0]["interpreter"] is None


async def test_relative_script_paths_are_accepted_and_sent_untouched(harness):
    harness.endpoint.add_script(r"jobs\nightly.cmd", script_path=r"C:\services\jobs\nightly.cmd", interpreter="cmd.exe")
    schedule, _, runs = await run_once(harness, script=r"jobs\nightly.cmd")
    start = next(r for r in harness.endpoint.requests if r.url.path == "/runs")
    assert json.loads(start.content)["script"] == r"jobs\nightly.cmd"
    assert runs[0]["outcome"] == "success" and runs[0]["script_path"] == r"C:\services\jobs\nightly.cmd"


async def test_outside_scripts_dir_reason(harness):
    harness.endpoint.rejections[r"..\evil.cmd".lower()] = {"error": "outside_scripts_dir", "script_path": r"C:\evil.cmd"}
    _, _, runs = await run_once(harness, script=r"..\evil.cmd")
    assert runs[0]["outcome"] == "script_not_found"
    assert runs[0]["reason"] == r"script not found: C:\evil.cmd (outside services folder)"
    assert runs[0]["script_path"] == r"C:\evil.cmd"


async def test_unsupported_on_this_os_reason_and_unknown_error_code(harness):
    harness.endpoint.rejections["job.sh"] = {"error": "unsupported_on_this_os"}
    _, _, runs = await run_once(harness, script="job.sh")
    assert runs[0]["reason"] == "unsupported on this OS: job.sh"
    harness.endpoint.rejections["job.ps1"] = {"error": "weird"}
    schedule = harness.store.list()[0]
    harness.store.replace(schedule.id, __import__("tests._scheduler.conftest", fromlist=["x"]).schedule_input(script="job.ps1"))
    harness.loop.dispatch(harness.store.get(schedule.id), "manual")
    await harness.settle()
    assert harness.runs(schedule.id)[-1]["reason"] == "script not found: job.ps1 (weird)"


async def test_missing_relative_script_gets_its_resolved_path_from_script_check(harness):
    harness.endpoint.check_extra["gone.py"] = {"resolved": r"C:\services\gone.py"}
    _, _, runs = await run_once(harness, script="gone.py")
    assert runs[0]["reason"] == r"script not found: C:\services\gone.py"
    assert runs[0]["script_path"] == r"C:\services\gone.py"
    checks = [r for r in harness.endpoint.requests if r.url.path == "/scripts/check"]
    assert len(checks) == 1


async def test_start_body_path_means_no_extra_check_call(harness):
    harness.endpoint.rejections["gone.py"] = {"error": "outside_scripts_dir", "script_path": r"C:\x\gone.py"}
    await run_once(harness, script="gone.py")
    assert not [r for r in harness.endpoint.requests if r.url.path == "/scripts/check"]


async def test_check_fallback_failure_keeps_the_configured_name_and_never_blocks(harness, monkeypatch):
    import _scheduler.loop as loop_mod

    async def slow_check(*_a, **_k):
        await asyncio.sleep(10)

    monkeypatch.setattr(loop_mod, "CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(harness.client, "check_script", slow_check)
    _, _, runs = await run_once(harness, script="gone.py")
    assert runs[0]["outcome"] == "script_not_found" and runs[0]["reason"] == "script not found: gone.py"


async def test_check_error_code_fills_the_reason_when_start_body_has_none(harness):
    harness.endpoint.check_extra["gone.py"] = {"resolved": r"C:\s\gone.py", "error": "outside_scripts_dir"}
    _, _, runs = await run_once(harness, script="gone.py")
    assert runs[0]["reason"] == r"script not found: C:\s\gone.py (outside services folder)"
