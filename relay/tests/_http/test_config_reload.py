import time

from fastapi.testclient import TestClient

from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"


def test_requires_auth(write_config, config_dict):
    path = write_config(config_dict)
    client = TestClient(create_app(path))
    resp = client.post("/config/reload")
    assert resp.status_code == 401


def test_reload_picks_up_a_real_on_disk_change(write_config, config_dict):
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)
    assert app.state.config_store.get().settings.ack_pin == "1234"

    config_dict["settings"]["ack_pin"] = "9999"
    time.sleep(0.02)
    write_config(config_dict)

    resp = client.post(f"/config/reload?{AUTH_QS}")
    assert resp.status_code == 200
    assert resp.json() == {"reloaded": True, "error": None}
    assert app.state.config_store.get().settings.ack_pin == "9999"


def test_o7_reload_applies_new_bus_config_before_the_next_poll_rebuilds_the_object_graph(write_config, config_dict):
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    assert client.get(f"/api/buses?{AUTH_QS}").json() == []

    config_dict["service_busses"] = [
        {"id": 1, "name": "bus-1", "health_url": "http://localhost:5300/health", "amqp_port": 5672, "topic": "sync"},
    ]
    time.sleep(0.02)
    write_config(config_dict)

    reload_resp = client.post(f"/config/reload?{AUTH_QS}")
    assert reload_resp.json() == {"reloaded": True, "error": None}

    body = client.get(f"/api/buses?{AUTH_QS}").json()
    assert [b["id"] for b in body] == [1]
    assert body[0]["ok"] is True


def test_reload_reports_failure_and_keeps_last_known_good(write_config, config_dict):
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    path.write_text("{not valid json", encoding="utf-8")

    resp = client.post(f"/config/reload?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reloaded"] is False
    assert body["error"]
    assert app.state.config_store.get().settings.ack_pin == "1234"
