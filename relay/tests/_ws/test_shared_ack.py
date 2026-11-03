from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from _alerts.dedup import AlertKey
from _alerts.shared_ack import AckStore
from main import create_app
from tests._state_helpers import read_doc

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"


@pytest.fixture
def client(write_config, config_dict) -> TestClient:
    return TestClient(create_app(write_config(config_dict)))


def _raise(client, machine_id=4):
    client.app.state.machine_alert_incidents.claim(AlertKey("machine", str(machine_id)), {"machine_id": machine_id, "severity": 2})


def _hello(ws):
    ws.send_json({"type": "hello", "device": "d1", "clientId": "c1"})
    return ws.receive_json()


def _alert(snapshot, alert_id="machine:4"):
    return next(a for a in snapshot["activeAlerts"] if a["alertId"] == alert_id)


def test_ack_is_stored_shown_on_the_wire_and_broadcast_to_every_client(client):
    _raise(client)
    with client.websocket_connect(f"/ws?{AUTH_QS}") as a, client.websocket_connect(f"/ws?{AUTH_QS}") as b:
        assert _alert(_hello(a))["acknowledged"] is False
        _hello(b)
        a.send_json({"type": "ack", "alertId": "machine:4", "ackedBy": "dash", "pin": "1234"})
        assert a.receive_json() == {"type": "ackResult", "alertId": "machine:4", "ok": True, "reason": None}
        for ws in (a, b):
            alert = _alert(ws.receive_json())
            assert alert["acknowledged"] is True and alert["acknowledged_by"] == "Test"
            assert alert["acknowledged_at"].endswith("Z")
        b.send_json({"type": "resync"})
        assert _alert(b.receive_json())["acknowledged"] is True


def test_a_wrong_pin_stores_nothing(client):
    _raise(client)
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        _hello(ws)
        ws.send_json({"type": "ack", "alertId": "machine:4", "ackedBy": "dash", "pin": "0000"})
        assert ws.receive_json()["reason"] == "invalid_pin"
    assert not client.app.state.alert_acks.is_acked("machine:4")


def test_ack_persists_atomically_and_survives_a_restart(tmp_path):
    store = AckStore(tmp_path, clock=lambda: 1000.0)
    store.acknowledge("machine:4", "Ops")
    saved = read_doc(tmp_path, "push/acks")
    assert saved == {"acks": {"machine:4": {"acknowledged_at": 1000.0, "acknowledged_by": "Ops"}}}
    assert AckStore(tmp_path).is_acked("machine:4")


def test_a_second_ack_keeps_the_first_acknowledger(tmp_path):
    store = AckStore(tmp_path)
    store.acknowledge("a", "Ops")
    version = store.version
    assert store.acknowledge("a", "Boss")["acknowledged_by"] == "Ops" and store.version == version


def test_ack_clears_after_the_alert_has_been_gone_longer_than_the_hold(tmp_path):
    now = {"t": 100.0}
    store = AckStore(tmp_path, clock=lambda: now["t"])
    store.acknowledge("a", "Ops")
    store.retain(set(), 15)
    now["t"] += 10
    store.retain({"a"}, 15)
    now["t"] += 10
    store.retain(set(), 15)
    assert store.is_acked("a")
    now["t"] += 15
    store.retain(set(), 15)
    assert not store.is_acked("a")
    assert read_doc(tmp_path, "push/acks") == {"acks": {}}
