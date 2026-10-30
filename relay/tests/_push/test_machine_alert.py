from __future__ import annotations

import pytest

from _contract.schema import RelayConfig
from _push.machine_alert import parse_machine_alert
from config_issues import compute_config_issues

from .conftest import alert

FULL = {"enabled": True, "alert_report_interval": 60, "alert_repeat_interval": 300, "alert_acknoledge_repeat_interval": 900}


def machine_alert(**kw):
    return {**alert("machine:1", severity=2), "machine_id": 1, **kw}


def set_block(push, block, machine_id=1):
    for m in push.config_store.get().machines:
        if m.machine_id == machine_id:
            m.machine_alert = block


def kinds(push) -> list[str]:
    return [r["body"]["message"]["data"]["kind"] for r in push.fcm.requests if r["body"]["message"]["token"] == "ops-1"]


async def test_missing_block_keeps_global_defaults(push):
    push.alerts = [machine_alert()]
    await push.dispatcher.tick()
    await push.run(14)
    assert push.fcm.requests == []
    await push.run(2)
    assert kinds(push) == ["first"]
    push.fcm.clear()
    await push.run(14 * 60)
    assert push.fcm.requests == []
    await push.run(60)
    assert kinds(push) == ["repeat"]


async def test_report_interval_is_the_per_machine_grace(push):
    set_block(push, FULL)
    push.alerts = [machine_alert()]
    await push.dispatcher.tick()
    await push.run(59)
    assert push.fcm.requests == []
    await push.run(2)
    assert kinds(push) == ["first"]


async def test_zero_report_interval_pushes_immediately(push):
    set_block(push, {"alert_report_interval": 0})
    push.alerts = [machine_alert()]
    await push.dispatcher.tick()
    assert kinds(push) == ["first"]


async def test_repeat_and_acknowledged_repeat_per_machine(push):
    set_block(push, FULL)
    push.alerts = [machine_alert()]
    await push.dispatcher.tick()
    await push.run(61)
    push.fcm.clear()
    await push.run(298)
    assert push.fcm.requests == []
    await push.run(4)
    assert kinds(push) == ["repeat"]
    push.alerts = [{**machine_alert(), "acknowledged": True}]
    push.fcm.clear()
    await push.run(890)
    assert push.fcm.requests == []
    await push.run(20)
    assert kinds(push) == ["repeat"]


async def test_partial_block_falls_back_per_key(push):
    set_block(push, {"alert_repeat_interval": 120})
    push.alerts = [machine_alert()]
    await push.dispatcher.tick()
    await push.run(16)
    assert kinds(push) == ["first"]
    push.fcm.clear()
    await push.run(125)
    assert kinds(push) == ["repeat"]


async def test_acknowledge_spelling_is_accepted(push):
    set_block(push, {"alert_acknowledge_repeat_interval": 100 * 60 // 10})
    push.alerts = [{**machine_alert(), "acknowledged": True}]
    await push.dispatcher.tick()
    await push.run(16)
    push.fcm.clear()
    await push.run(590)
    assert push.fcm.requests == []
    await push.run(20)
    assert kinds(push) == ["repeat"]


async def test_two_machines_the_shorter_interval_wins(push):
    other = push.config_store.get().machines[0].model_copy(update={"machine_id": 2, "machine_alert": {
        "alert_report_interval": 30, "alert_repeat_interval": 3000}})
    push.config_store.get().machines.append(other)
    set_block(push, {"alert_report_interval": 600, "alert_repeat_interval": 200})
    push.dispatcher._scope_resolver = lambda a, c: [("1", None), ("2", None)]
    push.alerts = [alert("pipeline:x", kind="pipeline")]
    await push.dispatcher.tick()
    await push.run(29)
    assert push.fcm.requests == []
    await push.run(3)
    assert kinds(push) == ["first"]
    push.fcm.clear()
    await push.run(195)
    assert push.fcm.requests == []
    await push.run(10)
    assert kinds(push) == ["repeat"]


async def test_disabled_machine_gets_no_push_and_logs_the_reason(push, caplog):
    set_block(push, {"enabled": False})
    push.alerts = [machine_alert()]
    with caplog.at_level("INFO", logger="relay.push"):
        await push.dispatcher.tick()
        await push.run(120)
    assert push.fcm.requests == []
    assert "reason=machine alerts disabled" in caplog.text
    set_block(push, {"enabled": True})
    await push.run(2)
    assert kinds(push) == ["first"]


async def test_disabled_machine_does_not_silence_an_alert_shared_with_an_enabled_machine(push):
    other = push.config_store.get().machines[0].model_copy(update={"machine_id": 2, "machine_alert": None})
    push.config_store.get().machines.append(other)
    set_block(push, {"enabled": False})
    push.dispatcher._scope_resolver = lambda a, c: [("1", None), ("2", None)]
    push.alerts = [alert("pipeline:x", kind="pipeline")]
    await push.dispatcher.tick()
    await push.run(16)
    assert kinds(push) == ["first"]


async def test_other_machines_are_unaffected(push):
    set_block(push, {"enabled": False}, machine_id=1)
    push.dispatcher._scope_resolver = lambda a, c: [("9", None)]
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    assert kinds(push) == ["first"]


@pytest.mark.parametrize("block", [
    {"alert_report_interval": -1}, {"alert_report_interval": 3601}, {"alert_repeat_interval": 59},
    {"alert_repeat_interval": 86401}, {"alert_repeat_interval": "300"}, {"alert_repeat_interval": 1.5},
    {"alert_repeat_interval": True}, {"enabled": "yes"}, {"surprise": 1}, "nope",
])
def test_bad_values_become_a_config_issue_and_fall_back(config_dict, block):
    config_dict["machines"][0]["machine_alert"] = block
    config = RelayConfig.model_validate(config_dict)
    issues = [i for i in compute_config_issues(config) if i.code == "machine_alert_invalid"]
    assert len(issues) == 1 and issues[0].where["machineId"] == "1"
    alert_cfg, problems = parse_machine_alert(config.machines[0].machine_alert)
    assert problems
    assert alert_cfg.enabled is True and alert_cfg.repeat is None


def test_one_bad_key_keeps_the_good_ones():
    parsed, problems = parse_machine_alert({"alert_report_interval": 30, "alert_repeat_interval": 5})
    assert parsed.report == 30 and parsed.repeat is None and len(problems) == 1


def test_valid_block_raises_no_issue(config_dict):
    config_dict["machines"][0]["machine_alert"] = FULL
    config = RelayConfig.model_validate(config_dict)
    assert not [i for i in compute_config_issues(config) if i.code == "machine_alert_invalid"]
