import pytest

from fastapi.testclient import TestClient

from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"


def _bus_config(config_dict, servers=None, service_busses=None):
    config_dict["service_busses"] = list(config_dict.get("service_busses", [])) + [
        {"id": bus["id"], "name": bus["name"], "health_url": bus["health_url"], "amqp_port": bus["amqp_port"], "topic": bus["topic"]}
        for bus in (service_busses or [])
    ]
    nodes = list(config_dict["machines"][0]["nodes"])
    for server in servers or []:
        nodes.append(
            {
                "node_id": f"node-srv-{server['id']}",
                "node_name": server["name"],
                "node_type": "Database",
                "node_platform": server["platform"],
                "server_id": server["id"],
                "node_config": {
                    "host": server["host"],
                    "port": server["port"],
                    "user": server["user"],
                    "password": server["password"],
                    "database": server["database"],
                    "used_buses": server.get("used_buses", []),
                },
            }
        )
    config_dict["machines"][0]["nodes"] = nodes
    return config_dict


def test_requires_auth(write_config, config_dict):
    path = write_config(_bus_config(config_dict))
    client = TestClient(create_app(path))
    resp = client.get("/api/buses")
    assert resp.status_code == 401


def test_rejects_bad_credentials(write_config, config_dict):
    path = write_config(_bus_config(config_dict))
    client = TestClient(create_app(path))
    resp = client.get("/api/buses?clientId=test_web_id&clientSecret=wrong")
    assert resp.status_code == 401


@pytest.mark.live_lab
def test_real_healthy_bus_reports_ok_true_no_blocked_by(write_config, config_dict):
    config_dict = _bus_config(
        config_dict,
        service_busses=[
            {"id": 1, "name": "bus-1", "health_url": "http://localhost:5300/health", "amqp_port": 5672, "topic": "sync"}
        ],
    )
    path = write_config(config_dict)
    client = TestClient(create_app(path))

    resp = client.get(f"/api/buses?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {"id": 1, "name": "bus-1", "ok": True, "blockedBy": None, "topics": ["sync"], "usedBy": []}
    ]


def test_real_unreachable_bus_reports_ok_false(write_config, config_dict):
    config_dict = _bus_config(
        config_dict,
        service_busses=[
            {"id": 9, "name": "bus-down", "health_url": "http://localhost:5399/health", "amqp_port": 5399, "topic": "sync"}
        ],
    )
    path = write_config(config_dict)
    client = TestClient(create_app(path))

    resp = client.get(f"/api/buses?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()[0]
    assert body["ok"] is False


def test_used_by_lists_referencing_server_ids(write_config, config_dict):
    config_dict = _bus_config(
        config_dict,
        service_busses=[
            {"id": 1, "name": "bus-1", "health_url": "http://localhost:5300/health", "amqp_port": 5672, "topic": "sync"}
        ],
        servers=[
            {
                "id": 1001, "name": "srv", "platform": "mysql", "host": "127.0.0.1", "port": 3306,
                "user": "u", "password": "p", "database": "db", "used_buses": [1],
            }
        ],
    )
    path = write_config(config_dict)
    client = TestClient(create_app(path))

    resp = client.get(f"/api/buses?{AUTH_QS}")
    assert resp.status_code == 200
    assert resp.json()[0]["usedBy"] == [1001]


@pytest.mark.live_lab
def test_accumulates_flap_confirmation_state_across_requests(write_config, config_dict):
    config_dict = _bus_config(
        config_dict,
        service_busses=[
            {"id": 1, "name": "bus-1", "health_url": "http://localhost:5300/health", "amqp_port": 5672, "topic": "sync"}
        ],
    )
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    first = client.get(f"/api/buses?{AUTH_QS}").json()[0]
    second = client.get(f"/api/buses?{AUTH_QS}").json()[0]
    assert first["ok"] is True
    assert second["ok"] is True
    assert app.state.down_confirm.confirmed_status("bus:1:health").value == "ok"
