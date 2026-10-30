from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _alerts.dedup import AlertKey
from _ws.messages import validate_inbound
from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"


@pytest.fixture
def client(write_config, config_dict) -> TestClient:
    return TestClient(create_app(write_config(config_dict)))


def _raise(client, machine_id=4):
    client.app.state.machine_alert_incidents.claim(AlertKey("machine", str(machine_id)), {"machine_id": machine_id, "severity": 2})


def _hello(ws):
    ws.send_json({"type": "hello", "device": "d1", "clientId": "c1"})
    return ws.receive_json()


def test_dashboard_ack_shape_validates():
    msg = validate_inbound({"type": "ack", "alertId": "machine:4", "ackedBy": "dash-web", "pin": "1234"})
    assert msg is not None and msg.alertId == "machine:4" and msg.ackedBy == "dash-web" and msg.clientId is None


def test_dashboard_silence_shape_validates_and_extras_are_ignored():
    msg = validate_inbound({"type": "silence", "alertId": "machine:4", "clientId": "c1", "device": "phone", "future": 1})
    assert msg is not None and msg.alertId == "machine:4" and msg.device == "phone"
    assert validate_inbound({"type": "silence", "clientId": "c1", "minutes": 5}) is not None


def test_ack_with_the_right_pin_gets_an_ok_result(client):
    _raise(client)
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        _hello(ws)
        ws.send_json({"type": "ack", "alertId": "machine:4", "ackedBy": "dash", "pin": "1234"})
        assert ws.receive_json() == {"type": "ackResult", "alertId": "machine:4", "ok": True, "reason": None}


def test_ack_with_a_wrong_or_missing_pin_is_refused_with_a_reason(client):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        _hello(ws)
        ws.send_json({"type": "ack", "alertId": "machine:4", "ackedBy": "dash", "pin": "0000"})
        assert ws.receive_json() == {"type": "ackResult", "alertId": "machine:4", "ok": False, "reason": "invalid_pin"}
        ws.send_json({"type": "ack", "alertId": "machine:4", "ackedBy": "dash"})
        assert ws.receive_json()["reason"] == "invalid_pin"


def test_legacy_ack_with_client_id_still_works(client):
    _raise(client)
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        _hello(ws)
        ws.send_json({"type": "ack", "alertId": "machine:4", "clientId": "c1", "pin": "1234"})
        assert ws.receive_json()["ok"] is True


def test_ack_for_an_alert_that_is_not_active_is_refused(client):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        _hello(ws)
        ws.send_json({"type": "ack", "alertId": "machine:4", "ackedBy": "dash", "pin": "1234"})
        assert ws.receive_json() == {"type": "ackResult", "alertId": "machine:4", "ok": False, "reason": "unknown_alert"}


def test_silence_is_accepted_without_breaking_the_session(client):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        _hello(ws)
        ws.send_json({"type": "silence", "alertId": "machine:4", "clientId": "c1", "device": "phone"})
        ws.send_json({"type": "resync"})
        assert ws.receive_json()["type"] == "snapshot"


def test_rate_limited_acks_get_a_rate_limited_result(write_config, config_dict):
    config_dict["settings"]["pin_rate_limit_attempts"] = 2
    client = TestClient(create_app(write_config(config_dict)))
    _raise(client)
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        _hello(ws)
        reasons = []
        for _ in range(4):
            ws.send_json({"type": "ack", "alertId": "machine:4", "clientId": "c1", "pin": "0000"})
            reasons.append(ws.receive_json()["reason"])
        assert reasons[:2] == ["invalid_pin", "invalid_pin"] and reasons[2:] == ["rate_limited", "rate_limited"]
