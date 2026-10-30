import pytest
from fastapi.testclient import TestClient

from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"
BAD_AUTH_QS = "clientId=test_web_id&clientSecret=wrong"


@pytest.fixture
def client(write_config, config_dict) -> TestClient:
    path = write_config(config_dict)
    app = create_app(path)
    return TestClient(app)


def test_requires_auth(client: TestClient):
    resp = client.get("/api/config/check")
    assert resp.status_code == 401


def test_rejects_bad_credentials(client: TestClient):
    resp = client.get(f"/api/config/check?{BAD_AUTH_QS}")
    assert resp.status_code == 401


def test_returns_servers_buses_and_config_issues(write_config, config_dict):
    config_dict["service_busses"] = [
        {"id": 1, "name": "bus-1", "health_url": "http://127.0.0.1:5300/health", "amqp_port": 5671, "topic": "t"},
    ]
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "node-srv-1001", "node_name": "srv-1001", "node_type": "Database",
            "node_platform": "mysql", "server_id": 1001,
            "node_config": {
                "host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db",
                "used_buses": [1],
            },
        },
    ]
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    resp = client.get(f"/api/config/check?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["servers"] == [
        {"id": 1001, "name": "srv-1001", "platform": "mysql", "host": "127.0.0.1", "port": 3306, "usedBuses": [1]}
    ]
    assert body["service_busses"] == [
        {"id": 1, "name": "bus-1", "health_url": "http://127.0.0.1:5300/health", "amqp_port": 5671, "topic": "t"}
    ]
    codes = {issue["code"] for issue in body["configIssues"]}
    assert "server_unused_by_any_bus" not in codes
    assert "service_bus_unused_by_any_server" not in codes
    assert "machine_without_nodes" not in codes


def test_never_echoes_credentials(write_config, config_dict):
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "node-srv-1001", "node_name": "srv-1001", "node_type": "Database",
            "node_platform": "mysql", "server_id": 1001,
            "node_config": {
                "host": "127.0.0.1", "port": 3306, "user": "super-secret-user",
                "password": "super-secret-password", "database": "db",
            },
        }
    ]
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    resp = client.get(f"/api/config/check?{AUTH_QS}")
    assert "super-secret-user" not in resp.text
    assert "super-secret-password" not in resp.text
