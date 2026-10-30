import json

import pytest
from fastapi.testclient import TestClient

from main import create_app

from ..conftest import TEST_TOKEN, make_database_node

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

WINDOW = {"machine": "1", "service": None, "timezone": "Asia/Karachi", "days": [0, 1], "start": "22:00", "end": "06:00", "note": "backup"}
ACCOUNT_WINDOW = {"timezone": "America/Chicago", "days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}


@pytest.fixture
def client(write_config, config_dict):
    config_dict["machines"][0]["nodes"] = [make_database_node("db-a", 7)]
    return TestClient(create_app(write_config(config_dict)))


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/downtime"), ("POST", "/api/downtime"), ("PUT", "/api/downtime/x"), ("DELETE", "/api/downtime/x"),
    ("GET", "/api/push/window"), ("PUT", "/api/push/window"),
])
def test_endpoints_require_a_session(client, method, path):
    resp = client.request(method, path, json={})
    assert resp.status_code == 401 and resp.json() == {"detail": "session_invalid"}


def test_downtime_crud_round_trip_and_persistence(client):
    assert client.get("/api/downtime", headers=AUTH).json() == {"windows": []}
    created = client.post("/api/downtime", headers=AUTH, json=WINDOW)
    assert created.status_code == 200
    body = created.json()
    assert body["id"] and {k: v for k, v in body.items() if k != "id"} == WINDOW
    assert client.get("/api/downtime", headers=AUTH).json() == {"windows": [body]}

    updated = client.put(f"/api/downtime/{body['id']}", headers=AUTH, json={**WINDOW, "service": "db-a", "days": [3]})
    assert updated.status_code == 200 and updated.json()["id"] == body["id"] and updated.json()["service"] == "db-a"
    assert client.get("/api/downtime", headers=AUTH).json()["windows"][0]["days"] == [3]

    saved = json.loads((client.app.state.state_dir / "push" / "downtime.json").read_text())
    assert saved["windows"][0]["id"] == body["id"]

    assert client.delete(f"/api/downtime/{body['id']}", headers=AUTH).status_code == 200
    assert client.get("/api/downtime", headers=AUTH).json() == {"windows": []}


def test_downtime_unknown_id_is_404(client):
    assert client.put("/api/downtime/nope", headers=AUTH, json=WINDOW).status_code == 404
    assert client.delete("/api/downtime/nope", headers=AUTH).status_code == 404


@pytest.mark.parametrize("patch", [
    {"timezone": "Not/AZone"}, {"days": []}, {"days": [9]}, {"start": "25:00"}, {"end": "6am"}, {"machine": ""}, {"service": 3},
])
def test_downtime_validation_returns_400_and_stores_nothing(client, patch):
    assert client.post("/api/downtime", headers=AUTH, json={**WINDOW, **patch}).status_code == 400
    created = client.post("/api/downtime", headers=AUTH, json=WINDOW).json()
    assert client.put(f"/api/downtime/{created['id']}", headers=AUTH, json={**WINDOW, **patch}).status_code == 400
    assert client.get("/api/downtime", headers=AUTH).json()["windows"] == [created]


def test_account_window_get_put_clear(client):
    assert client.get("/api/push/window", headers=AUTH).json() == {"window": None}
    put = client.put("/api/push/window", headers=AUTH, json=ACCOUNT_WINDOW)
    assert put.status_code == 200 and put.json() == {"window": ACCOUNT_WINDOW}
    assert client.get("/api/push/window", headers=AUTH).json() == {"window": ACCOUNT_WINDOW}
    stored = json.loads((client.app.state.state_dir / "auth" / "push_accounts.json").read_text())
    assert stored[0]["push_window"] == ACCOUNT_WINDOW
    assert client.put("/api/push/window", headers=AUTH, json={"window": None}).json() == {"window": None}
    assert client.get("/api/push/window", headers=AUTH).json() == {"window": None}


@pytest.mark.parametrize("patch", [{"timezone": "Nowhere/Land"}, {"days": []}, {"days": [7]}, {"start": "9"}, {"end": "99:99"}])
def test_account_window_validation_returns_400(client, patch):
    assert client.put("/api/push/window", headers=AUTH, json={**ACCOUNT_WINDOW, **patch}).status_code == 400
    assert client.get("/api/push/window", headers=AUTH).json() == {"window": None}


@pytest.mark.parametrize("patch", [{"machine": "999"}, {"service": "nope"}, {"machine": "1", "service": "db-b"}])
def test_downtime_rejects_unknown_machine_or_service_with_a_clear_message(client, patch):
    resp = client.post("/api/downtime", headers=AUTH, json={**WINDOW, **patch})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_window"
    assert "machine" in resp.json()["message"] or "service" in resp.json()["message"]
    created = client.post("/api/downtime", headers=AUTH, json=WINDOW).json()
    assert client.put(f"/api/downtime/{created['id']}", headers=AUTH, json={**WINDOW, **patch}).status_code == 400


def test_downtime_accepts_a_known_node_and_stored_windows_survive_config_changes(client, tmp_path):
    ok = client.post("/api/downtime", headers=AUTH, json={**WINDOW, "service": "db-a"})
    assert ok.status_code == 200
    from _push.downtime import DowntimeStore

    stale = DowntimeStore(client.app.state.state_dir)
    stale.create({**WINDOW, "machine": "999", "service": "gone"})
    assert len(DowntimeStore(client.app.state.state_dir).list()) == 2
