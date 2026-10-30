import pytest
from fastapi.testclient import TestClient

from main import create_app
from _lib.config import StartupConfigError


def test_app_starts_and_serves_health(write_config, config_dict):
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_app_refuses_to_start_on_bad_startup_config(write_config, config_dict):
    del config_dict["settings"]["ack_pin"]
    path = write_config(config_dict)

    with pytest.raises(StartupConfigError):
        create_app(path)


def test_app_end_to_end_auth_accept_and_reject(write_config, config_dict):
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    with client.websocket_connect("/ws?token=test-session-token-0123456789abcdef0123456789abcdef") as ws:
        ws.send_json({"type": "hello", "device": "d", "clientId": "c"})
        assert ws.receive_json()["type"] == "snapshot"

    with client.websocket_connect("/ws?token=nope") as ws:
        message = ws.receive()
        assert message["type"] == "websocket.close"
        assert message["code"] == 4401
