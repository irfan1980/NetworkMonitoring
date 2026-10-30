from __future__ import annotations

from _alerts.dedup import AlertKey
from _scheduler.wire import scheduler_alerts_wire

KEY = AlertKey("scheduler_endpoint", "1")


async def test_up_when_endpoint_answers(harness):
    await harness.health.tick()
    assert harness.health.wire_state(harness.config_store.get().machines[0]) == "up"
    assert not harness.endpoint_incidents.is_open(KEY)


async def test_down_needs_consecutive_misses_then_alerts(harness):
    machine = harness.config_store.get().machines[0]
    await harness.health.tick()
    harness.endpoint.down = True
    await harness.health.tick()
    assert harness.health.wire_state(machine) == "up"
    await harness.health.tick()
    assert harness.health.wire_state(machine) == "down"
    record = harness.endpoint_incidents.get(KEY)
    assert record["severity"] == 2 and record["state"] == "down"

    wire = scheduler_alerts_wire(harness.schedule_incidents, harness.endpoint_incidents, harness.config_store.get())
    assert wire[0]["alertId"] == "scheduler_endpoint:1"
    assert wire[0]["reason"].endswith("scheduler endpoint down")
    assert wire[0]["machine_id"] == 1 and wire[0]["kind"] == "scheduler_endpoint"

    harness.endpoint.down = False
    await harness.health.tick()
    assert harness.health.wire_state(machine) == "up"
    assert not harness.endpoint_incidents.is_open(KEY)


async def test_a_single_miss_followed_by_success_never_alerts(harness):
    await harness.health.tick()
    harness.endpoint.down = True
    await harness.health.tick()
    harness.endpoint.down = False
    await harness.health.tick()
    assert not harness.endpoint_incidents.is_open(KEY)


async def test_401_is_auth_rejected_immediately_with_its_own_text(harness):
    machine = harness.config_store.get().machines[0]
    harness.endpoint.reject_keys = True
    await harness.health.tick()
    assert harness.health.wire_state(machine) == "auth_rejected"
    wire = scheduler_alerts_wire(harness.schedule_incidents, harness.endpoint_incidents, harness.config_store.get())
    assert wire[0]["reason"].endswith("scheduler auth rejected: check clock / machine IP")

    harness.endpoint.reject_keys = False
    await harness.health.tick()
    assert harness.health.wire_state(machine) == "up"
    assert not harness.endpoint_incidents.is_open(KEY)


async def test_down_can_turn_into_auth_rejected_on_the_same_alert(harness):
    harness.endpoint.down = True
    await harness.health.tick()
    await harness.health.tick()
    harness.endpoint.down = False
    harness.endpoint.reject_keys = True
    await harness.health.tick()
    assert harness.endpoint_incidents.get(KEY)["state"] == "auth_rejected"
    assert len(harness.endpoint_incidents) == 1


async def test_offline_machine_is_not_probed_and_the_alert_is_suppressed(harness):
    machine = harness.config_store.get().machines[0]
    harness.endpoint.down = True
    await harness.health.tick()
    await harness.health.tick()
    assert harness.endpoint_incidents.is_open(KEY)

    harness.registry.state_for(1).status = "offline"
    before = len(harness.endpoint.requests)
    await harness.health.tick()
    assert len(harness.endpoint.requests) == before
    assert not harness.endpoint_incidents.is_open(KEY)
    assert harness.health.wire_state(machine) == "down"
    assert harness.health.reason(machine) == "machine offline"

    await harness.health.tick()
    assert not harness.endpoint_incidents.is_open(KEY)


async def test_machine_scheduler_false_is_off_and_never_probed(tmp_path, config_dict):
    from .conftest import Harness

    h = Harness(tmp_path, config_dict)
    raw = h.config_store.path.read_text()
    h.config_store.path.write_text(raw.replace('"machine_scheduler": true', '"machine_scheduler": false'))
    h.config_store.force_reload()
    machine = h.config_store.get().machines[0]
    await h.health.tick()
    assert h.health.wire_state(machine) == "off"
    assert h.endpoint.requests == []
