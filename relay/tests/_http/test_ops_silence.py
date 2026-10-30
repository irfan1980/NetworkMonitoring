from fastapi.testclient import TestClient

from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"
PIN = "1234"


def _client(write_config, config_dict):
    path = write_config(config_dict)
    return TestClient(create_app(path))


def test_create_requires_http_auth(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.post("/ops/silence", json={"reason": "maint"})
    assert resp.status_code == 401


def test_list_requires_http_auth(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.get("/ops/silences")
    assert resp.status_code == 401


def test_delete_requires_http_auth(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.delete("/ops/silence/1")
    assert resp.status_code == 401


def test_create_rejects_missing_pin(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.post(f"/ops/silence?{AUTH_QS}", json={"reason": "maint"})
    assert resp.status_code == 401


def test_create_rejects_wrong_pin(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.post(f"/ops/silence?{AUTH_QS}&pin=0000", json={"reason": "maint"})
    assert resp.status_code == 401


def test_create_accepts_correct_pin_and_returns_the_silence(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.post(f"/ops/silence?{AUTH_QS}&pin={PIN}", json={"reason": "planned maintenance", "minutes": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "planned maintenance"
    assert body["id"] == 1
    assert body["created_by"] == "Test"


def test_create_defaults_minutes_to_device_silence_minutes(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.post(f"/ops/silence?{AUTH_QS}&pin={PIN}", json={"reason": "no explicit minutes"})
    assert resp.status_code == 200
    from datetime import datetime

    body = resp.json()
    created = datetime.strptime(body["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    expires = datetime.strptime(body["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert (expires - created).total_seconds() == 15 * 60


def test_create_uses_non_default_device_silence_minutes_c15(write_config, config_dict):
    from datetime import datetime

    config_dict["settings"]["device_silence_minutes"] = 45
    client = _client(write_config, config_dict)
    resp = client.post(f"/ops/silence?{AUTH_QS}&pin={PIN}", json={"reason": "no explicit minutes"})
    assert resp.status_code == 200
    body = resp.json()
    created = datetime.strptime(body["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    expires = datetime.strptime(body["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert (expires - created).total_seconds() == 45 * 60


def test_create_rate_limits_after_repeated_wrong_pins(write_config, config_dict):
    client = _client(write_config, config_dict)
    for _ in range(5):
        client.post(f"/ops/silence?{AUTH_QS}&pin=0000", json={"reason": "x"})
    resp = client.post(f"/ops/silence?{AUTH_QS}&pin={PIN}", json={"reason": "x"})
    assert resp.status_code == 429


def test_list_returns_only_active_silences(write_config, config_dict):
    client = _client(write_config, config_dict)
    client.post(f"/ops/silence?{AUTH_QS}&pin={PIN}", json={"reason": "one", "minutes": 30})
    client.post(f"/ops/silence?{AUTH_QS}&pin={PIN}", json={"reason": "two", "minutes": 30})

    resp = client.get(f"/ops/silences?{AUTH_QS}")
    assert resp.status_code == 200
    reasons = {s["reason"] for s in resp.json()}
    assert reasons == {"one", "two"}


def test_delete_requires_correct_pin(write_config, config_dict):
    client = _client(write_config, config_dict)
    created = client.post(f"/ops/silence?{AUTH_QS}&pin={PIN}", json={"reason": "one", "minutes": 30}).json()

    resp = client.delete(f"/ops/silence/{created['id']}?{AUTH_QS}&pin=0000")
    assert resp.status_code == 401

    still_listed = client.get(f"/ops/silences?{AUTH_QS}").json()
    assert any(s["id"] == created["id"] for s in still_listed)


def test_delete_removes_a_real_silence(write_config, config_dict):
    client = _client(write_config, config_dict)
    created = client.post(f"/ops/silence?{AUTH_QS}&pin={PIN}", json={"reason": "one", "minutes": 30}).json()

    resp = client.delete(f"/ops/silence/{created['id']}?{AUTH_QS}&pin={PIN}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "id": created["id"]}

    after = client.get(f"/ops/silences?{AUTH_QS}").json()
    assert after == []


def test_delete_unknown_id_returns_404(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.delete(f"/ops/silence/99999?{AUTH_QS}&pin={PIN}")
    assert resp.status_code == 404
