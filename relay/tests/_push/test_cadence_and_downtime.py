from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from _push.accounts import account_key
from _push.downtime import DowntimeStore
from _push.schedule_window import WindowError, is_active, parse_schedule
from _push.sources import alert_scopes

from .conftest import alert, device
from tests._state_helpers import read_doc


def epoch(year, month, day, hour, minute, tz) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz)).timestamp()


def window(**overrides) -> dict:
    base = {"machine": "1", "service": None, "timezone": "Asia/Karachi", "days": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "17:00", "note": ""}
    return {**base, **overrides}


def acct_window(**overrides) -> dict:
    return {k: v for k, v in window(**overrides).items() if k in ("timezone", "days", "start", "end")}


def machine_alert(**kw):
    return {**alert("machine:1", severity=2), "machine_id": 1, **kw}


def set_ops_window(push, schedule) -> None:
    accounts = push.read_accounts()
    accounts[0]["push_window"] = schedule
    push.write_accounts(accounts)


def kinds(push) -> list[str]:
    return [r["body"]["message"]["data"]["kind"] for r in push.fcm.requests if r["body"]["message"]["token"] == "ops-1"]


async def test_first_then_repeat_every_15_minutes_while_unacknowledged(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    assert kinds(push) == ["first"]
    push.fcm.clear()
    await push.run(14 * 60)
    assert push.fcm.requests == []
    await push.run(60)
    assert kinds(push) == ["repeat"]
    sound = push.fcm.requests[0]["body"]["message"]
    assert sound["data"]["sound_id"] == "sev2_repeat" and sound["android"]["notification"]["channel_id"] == "sev2_repeat"
    assert sound["apns"]["payload"]["aps"]["sound"] == "sev2_repeat.wav"
    push.fcm.clear()
    await push.run(15 * 60)
    assert kinds(push) == ["repeat"]


async def test_acknowledged_alerts_repeat_every_30_minutes_and_switching_applies_at_once(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    push.alerts = [{**alert(), "acknowledged": True}]
    push.fcm.clear()
    await push.run(29 * 60)
    assert push.fcm.requests == []
    await push.run(120)
    assert kinds(push) == ["repeat"]
    push.fcm.clear()
    await push.run(16 * 60)
    assert push.fcm.requests == []
    push.alerts = [alert()]
    await push.run(2)
    assert kinds(push) == ["repeat"]


async def test_cadence_stops_when_the_alert_clears_and_there_is_no_recovery_push(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    push.fcm.clear()
    push.alerts = []
    await push.run(60 * 60)
    assert push.fcm.requests == []


async def test_default_intervals_come_from_config(push):
    push_cfg = push.config_store.get().settings.push
    assert (push_cfg.repeat_minutes_unacknowledged, push_cfg.repeat_minutes_acknowledged) == (15, 30)


@pytest.mark.parametrize("severity", [1, 3, 5])
async def test_sound_id_follows_severity_and_kind(push, severity):
    push.write_accounts([{"account_secret": "1111", "name": "Ops", "min_severity": 5, "device_codes": [device("ops-1")]}])
    push.alerts = [alert(severity=severity)]
    await push.dispatcher.tick()
    await push.run(16)
    message = push.fcm.requests[0]["body"]["message"]
    assert message["data"]["sound_id"] == f"sev{severity}_first"
    assert message["data"]["severity"] == str(severity) and message["data"]["kind"] == "first"
    assert message["android"]["priority"] == "HIGH"
    assert message["apns"]["headers"] == {"apns-priority": "10", "apns-push-type": "alert"}
    assert message["apns"]["payload"]["aps"]["interruption-level"] == "time-sensitive"


async def test_machine_downtime_blocks_all_pushes_and_the_next_tick_after_it_ends_delivers(push):
    tz = "Asia/Karachi"
    push.clock.now = epoch(2026, 10, 12, 10, 0, tz)
    push.dispatcher.downtime.create(window(timezone=tz))
    push.alerts = [machine_alert()]
    await push.dispatcher.tick()
    await push.run(60)
    assert push.fcm.requests == []
    push.clock.now = epoch(2026, 10, 12, 16, 59, tz)
    await push.run(30)
    assert push.fcm.requests == []
    await push.run(60)
    assert kinds(push) == ["first"]


async def test_downtime_for_another_machine_does_not_block(push):
    push.dispatcher.downtime.create(window(machine="2", timezone="UTC", start="00:00", end="23:59"))
    push.clock.now = epoch(2026, 10, 12, 10, 0, "UTC")
    push.alerts = [machine_alert()]
    await push.dispatcher.tick()
    await push.run(16)
    assert kinds(push) == ["first"]


async def test_repeat_resumes_after_downtime_ends(push):
    tz = "UTC"
    push.clock.now = epoch(2026, 10, 12, 8, 0, tz)
    push.alerts = [machine_alert()]
    await push.dispatcher.tick()
    await push.run(16)
    assert kinds(push) == ["first"]
    push.dispatcher.downtime.create(window(timezone=tz, start="08:10", end="08:50"))
    push.fcm.clear()
    push.clock.now = epoch(2026, 10, 12, 8, 14, tz)
    await push.run(30 * 60)
    assert push.fcm.requests == []
    await push.run(10 * 60)
    assert kinds(push) == ["repeat"]


async def test_account_window_gates_one_account_only_and_delivers_inside_it(push):
    tz = "Asia/Karachi"
    set_ops_window(push, acct_window(timezone=tz, start="09:00", end="17:00"))
    push.clock.now = epoch(2026, 10, 12, 20, 0, tz)
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    assert sorted(push.fcm.tokens()) == ["boss-1"]
    push.fcm.clear()
    push.clock.now = epoch(2026, 10, 13, 8, 59, tz)
    await push.run(30)
    assert push.fcm.tokens().count("ops-1") == 0
    await push.run(60)
    assert sorted(t for t in push.fcm.tokens() if t.startswith("ops")) == ["ops-1", "ops-2"]
    assert kinds(push) == ["first"]


async def test_each_account_uses_its_own_timezone(push):
    set_ops_window(push, acct_window(timezone="America/Chicago", start="09:00", end="17:00"))
    accounts = push.read_accounts()
    accounts[1]["push_window"] = acct_window(timezone="Asia/Karachi", start="09:00", end="17:00")
    push.write_accounts(accounts)
    push.clock.now = epoch(2026, 10, 12, 10, 0, "America/Chicago")
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    assert sorted(push.fcm.tokens()) == ["ops-1", "ops-2"]


async def test_account_window_dies_with_the_account(push):
    set_ops_window(push, acct_window())
    assert push.read_accounts()[0]["push_window"]["timezone"] == "Asia/Karachi"
    push.write_accounts(push.read_accounts()[1:])
    assert all("push_window" not in a for a in push.read_accounts())


async def test_removed_account_sent_records_are_pruned(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    state = push.dispatcher._states["machine:4"]
    assert set(state.sent) == {account_key("1111"), account_key("2222")}
    push.write_accounts(push.read_accounts()[1:])
    await push.run(2)
    assert set(state.sent) == {account_key("2222")}


async def test_sent_state_persists_across_restart_without_resend(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    sent = len(push.fcm.requests)
    state = read_doc(push.tmp / "state", "push/state")
    assert set(state["alerts"]["machine:4"]["sent"]) == {account_key("1111"), account_key("2222")}
    push.dispatcher = push.new_dispatcher()
    await push.run(60)
    assert len(push.fcm.requests) == sent


def test_midnight_crossing_belongs_to_the_start_day():
    tz = "Asia/Karachi"
    win = parse_schedule(acct_window(timezone=tz, days=[4], start="22:00", end="06:00"))
    assert is_active(win, epoch(2026, 10, 16, 23, 0, tz))
    assert is_active(win, epoch(2026, 10, 17, 5, 59, tz))
    assert not is_active(win, epoch(2026, 10, 17, 6, 0, tz))
    assert not is_active(win, epoch(2026, 10, 16, 5, 0, tz))
    assert not is_active(win, epoch(2026, 10, 17, 23, 0, tz))
    assert not is_active(win, epoch(2026, 10, 16, 21, 59, tz))


def test_end_equal_to_start_covers_a_full_day_from_start():
    win = parse_schedule(acct_window(timezone="UTC", days=[0], start="08:00", end="08:00"))
    assert is_active(win, epoch(2026, 10, 12, 8, 0, "UTC")) and is_active(win, epoch(2026, 10, 13, 7, 59, "UTC"))
    assert not is_active(win, epoch(2026, 10, 13, 8, 0, "UTC")) and not is_active(win, epoch(2026, 10, 12, 7, 59, "UTC"))


def test_timezone_conversion_follows_dst():
    win = parse_schedule(acct_window(timezone="America/Chicago", days=[0], start="09:00", end="17:00"))
    utc = ZoneInfo("UTC")
    assert is_active(win, datetime(2026, 10, 12, 14, 30, tzinfo=utc).timestamp())
    assert not is_active(win, datetime(2026, 10, 12, 13, 30, tzinfo=utc).timestamp())
    assert is_active(win, datetime(2026, 11, 9, 15, 30, tzinfo=utc).timestamp())
    assert not is_active(win, datetime(2026, 11, 9, 14, 30, tzinfo=utc).timestamp())


def test_dst_transition_day_uses_wall_clock_time():
    win = parse_schedule(acct_window(timezone="America/Chicago", days=[6], start="03:00", end="05:00"))
    assert is_active(win, epoch(2026, 3, 8, 3, 30, "America/Chicago"))
    assert not is_active(win, epoch(2026, 3, 8, 5, 0, "America/Chicago"))


def test_downtime_matching_machine_versus_service(tmp_path):
    store = DowntimeStore(tmp_path)
    now = epoch(2026, 10, 12, 10, 0, "Asia/Karachi")
    store.create(window(machine="1", service="db-a"))
    assert store.is_down("1", "db-a", now)
    assert not store.is_down("1", "db-b", now)
    assert not store.is_down("1", None, now)
    assert not store.is_down("2", "db-a", now)
    store.create(window(machine="3", service=None))
    assert store.is_down("3", None, now) and store.is_down("3", "anything", now)
    assert not store.is_down(None, None, now)


def test_downtime_persists_and_reloads(tmp_path):
    store = DowntimeStore(tmp_path)
    created = store.create(window(note="patching"))
    assert DowntimeStore(tmp_path).list() == [created]
    assert read_doc(tmp_path, "push/downtime") == {"windows": [created]}


@pytest.mark.parametrize("patch", [
    {"timezone": "Mars/Base"}, {"timezone": ""}, {"timezone": None}, {"days": []}, {"days": [7]}, {"days": [-1]},
    {"days": "mon"}, {"days": [True]}, {"start": "9:00"}, {"end": "24:00"}, {"start": 900}, {"machine": ""}, {"service": ""},
    {"service": 5}, {"note": 3},
])
def test_invalid_windows_are_rejected(tmp_path, patch):
    with pytest.raises(WindowError):
        DowntimeStore(tmp_path).create(window(**patch))


@pytest.fixture
def scope_config(config_dict, write_config):
    from _lib.config import ConfigStore

    from ..conftest import make_database_node

    machine_two = {**config_dict["machines"][0], "machine_id": 2, "machine_name": "m2", "machine_port": 29102}
    config_dict["machines"][0]["nodes"] = [make_database_node("db-a", 7)]
    machine_two["nodes"] = [make_database_node("db-b", 8)]
    config_dict["machines"].append(machine_two)
    config_dict["services"] = [make_database_node("db-standalone", 9)]
    store = ConfigStore(write_config(config_dict))
    store.load_startup()
    return store.get()


def test_machine_and_scheduler_endpoint_alerts_scope_to_the_machine(scope_config):
    assert alert_scopes({"kind": "machine", "machine_id": 1}, scope_config) == [("1", None)]
    assert alert_scopes({"kind": "scheduler_endpoint", "machine_id": 2}, scope_config) == [("2", None)]


def test_object_alerts_scope_to_machine_and_node(scope_config):
    assert alert_scopes({"kind": "object", "affects": ["db:mysql:7:query"]}, scope_config) == [("1", "db-a")]
    assert alert_scopes({"kind": "object", "affects": ["db:mysql:99:query"]}, scope_config) == []
    assert alert_scopes({"kind": "object", "affects": ["db:mysql:9:query"]}, scope_config) == []
    assert alert_scopes({"kind": "object", "affects": ["bus:1:health"]}, scope_config) == []


def test_schedule_alerts_scope_to_the_schedules_machine(scope_config):
    alert = {"kind": "schedule", "schedule_id": 5}
    assert alert_scopes(alert, scope_config, None, lambda sid: 2 if sid == 5 else None) == [("2", None)]
    assert alert_scopes(alert, scope_config, None, lambda sid: None) == []
    assert alert_scopes(alert, scope_config) == []


@pytest.mark.parametrize("kind", ["pipeline", "bus", "lost", "relay_heartbeat"])
def test_pipeline_family_alerts_scope_to_both_ends_of_the_pipeline(scope_config, kind):
    alert = {"kind": kind, "affects": ["7:3"]}
    lookup = {"7:3": {"sourceSystem": 7, "targetSystem": 8}}
    assert alert_scopes(alert, scope_config, lookup) == [("1", "db-a"), ("2", "db-b")]


@pytest.mark.parametrize("kind", ["pipeline", "bus", "lost", "relay_heartbeat"])
def test_pipeline_family_alerts_fall_back_to_the_pipeline_server_prefix(scope_config, kind):
    assert alert_scopes({"kind": kind, "affects": ["8:1"]}, scope_config) == [("2", "db-b")]
    assert alert_scopes({"kind": kind, "affects": ["9:1"]}, scope_config) == []
    assert alert_scopes({"kind": kind, "affects": ["4"]}, scope_config) == []
    assert alert_scopes({"kind": kind, "affects": ["7:3"]}, scope_config, {"7:3": {"sourceSystem": 9, "targetSystem": 8}}) == [("2", "db-b")]


def test_unknown_alert_kinds_have_no_scope(scope_config):
    assert alert_scopes({"kind": "mystery", "affects": ["7:3"]}, scope_config) == []


async def test_a_machine_window_suppresses_a_pipeline_alert_through_the_dispatcher(push):
    tz = "UTC"
    push.clock.now = epoch(2026, 10, 12, 10, 0, tz)
    push.dispatcher._scope_resolver = lambda alert, config: [("1", None), ("2", "db-b")]
    push.dispatcher.downtime.create(window(machine="2", service="db-b", timezone=tz, start="09:00", end="11:00"))
    push.alerts = [alert("pipeline:7:3", kind="pipeline")]
    await push.dispatcher.tick()
    await push.run(60)
    assert push.fcm.requests == []


async def test_shared_ack_switches_cadence_to_30_minutes_and_a_recurrence_starts_unacknowledged(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    assert len(push.fcm.requests) == 3
    push.dispatcher.acks.acknowledge("machine:4", "Ops")
    push.fcm.clear()
    await push.run(29 * 60)
    assert push.fcm.requests == []
    await push.run(120)
    assert len(push.fcm.requests) == 3
    push.alerts = []
    await push.run(20)
    assert not push.dispatcher.acks.is_acked("machine:4")
    push.alerts = [alert()]
    push.fcm.clear()
    await push.run(50)
    assert kinds(push) == ["first"]
    push.fcm.clear()
    await push.run(14 * 60)
    assert push.fcm.requests == []
    await push.run(60)
    assert kinds(push) == ["repeat"]


def _push_logs(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "relay.push"]


async def test_sends_and_suppressions_are_logged_once_per_transition_without_tokens(push, caplog):
    caplog.set_level("INFO", logger="relay.push")
    tz = "UTC"
    push.clock.now = epoch(2026, 10, 12, 10, 0, tz)
    set_ops_window(push, acct_window(timezone=tz, start="20:00", end="21:00"))
    push.dispatcher.mutes.mute("a:3", account_key("2222"))
    window_id = push.dispatcher.downtime.create(window(machine="1", timezone=tz, start="00:00", end="01:00"))["id"]
    push.alerts = [alert("a:3", severity=3, reason="sev three"), machine_alert()]
    await push.dispatcher.tick()
    await push.run(120)
    logs = _push_logs(caplog)
    assert logs.count("push suppressed alert=a:3 account=Ops reason=below min_severity") == 1
    assert logs.count("push suppressed alert=a:3 account=Boss reason=muted") == 1
    assert logs.count("push suppressed alert=machine:1 account=Ops reason=outside account push window") == 1
    assert "push sent alert=machine:1 account=Boss kind=first sound=sev2_first" in logs
    assert not any(token in " ".join(logs) for token in ("ops-1", "boss-1", "1111", "2222"))
    caplog.clear()
    push.dispatcher.downtime.create(window(machine="1", timezone=tz, start="09:00", end="11:00"))
    await push.run(5)
    reasons = [m for m in _push_logs(caplog) if "downtime window" in m]
    assert len(reasons) == 2 and all("alert=machine:1" in m for m in reasons)
    assert window_id not in " ".join(reasons)
    await push.run(5)
    assert len([m for m in _push_logs(caplog) if "downtime window" in m]) == 2


def _all_kinds(push) -> list[tuple[str, str]]:
    return [(r["body"]["message"]["data"]["alertId"], r["body"]["message"]["data"]["kind"])
            for r in push.fcm.requests if r["body"]["message"]["token"] == "ops-1"]


async def _notified(push, alerts):
    push.alerts = alerts
    await push.dispatcher.tick()
    await push.run(16)
    push.fcm.requests.clear()


async def test_a_long_stop_does_not_make_every_alert_repeat_at_once(push):
    alerts = [alert(f"machine:{n}", severity=2) for n in (4, 5, 6)]
    await _notified(push, alerts)
    push.clock.advance(10 * 86400)
    push.dispatcher = push.new_dispatcher()
    await push.run(180)
    assert _all_kinds(push) == []
    await push.run(15 * 60)
    assert sorted(_all_kinds(push)) == [(f"machine:{n}", "repeat") for n in (4, 5, 6)]


async def test_an_alert_never_notified_still_gets_its_first_push_after_a_long_stop(push):
    await _notified(push, [alert("machine:4")])
    push.clock.advance(10 * 86400)
    push.dispatcher = push.new_dispatcher()
    push.alerts = [alert("machine:4"), alert("machine:9", reason="new problem")]
    await push.run(30)
    assert _all_kinds(push) == [("machine:9", "first")]


async def test_a_short_restart_keeps_a_repeat_that_was_due(push):
    await _notified(push, [alert("machine:4")])
    push.clock.advance(20 * 60)
    push.dispatcher = push.new_dispatcher()
    await push.run(5)
    assert _all_kinds(push) == [("machine:4", "repeat")]


async def test_rebaseline_uses_the_acknowledged_interval(push):
    await _notified(push, [alert("machine:4")])
    push.clock.advance(50 * 60)
    push.dispatcher = push.new_dispatcher()
    push.dispatcher.acks.acknowledge("machine:4", "Ops")
    await push.run(5)
    assert _all_kinds(push) == [("machine:4", "repeat")]  # 50 min < 2 x 30 min: a legitimate short outage
    push.fcm.requests.clear()
    push.clock.advance(70 * 60)
    push.dispatcher = push.new_dispatcher()
    push.dispatcher.acks.acknowledge("machine:4", "Ops")
    await push.run(5)
    assert _all_kinds(push) == []  # 70 min >= 2 x 30 min: missed while down, re-baselined
