from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import create_app

TOKEN = "test-session-token-0123456789abcdef0123456789abcdef"
BEARER = {"authorization": f"Bearer {TOKEN}"}
PUBLIC = "https://dash.example.com"
EVIL = "https://evil.example.com"
LOGIN_BODY = {"account_secret": "test-secret", "platform": "web", "device_name": "pytest"}


@pytest.fixture
def client(write_config, config_dict) -> TestClient:
    config_dict["settings"]["ws_allowed_origins"] = [PUBLIC]
    return TestClient(create_app(write_config(config_dict)))


def _preflight(client, origin, path="/api/auth/me", method="GET", headers="authorization"):
    return client.options(path, headers={
        "origin": origin, "access-control-request-method": method, "access-control-request-headers": headers,
    })


@pytest.mark.parametrize("origin", [PUBLIC, "http://localhost:8081", "http://127.0.0.1:8081", "http://192.168.1.20:8081"])
def test_allowed_origin_reaches_rest_and_gets_cors_headers(client, origin):
    r = client.get("/api/auth/me", headers={**BEARER, "origin": origin})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert r.headers["vary"] == "Origin"


@pytest.mark.parametrize("origin", [EVIL, "null", "http://8.8.8.8", "https://localhost.evil.com"])
def test_disallowed_origin_is_refused_403_even_with_a_valid_token(client, origin):
    r = client.get("/api/auth/me", headers={**BEARER, "origin": origin})
    assert r.status_code == 403
    assert r.json() == {"detail": "origin_forbidden"}
    assert "access-control-allow-origin" not in r.headers


def test_disallowed_origin_is_refused_on_login_and_no_session_is_created(client):
    r = client.post("/api/auth/login", json=LOGIN_BODY, headers={"origin": EVIL})
    assert r.status_code == 403
    assert "token" not in r.text


def test_disallowed_origin_is_refused_on_health(client):
    assert client.get("/health", headers={"origin": EVIL}).status_code == 403


def test_allowed_origin_can_log_in(client):
    r = client.post("/api/auth/login", json=LOGIN_BODY, headers={"origin": PUBLIC})
    assert r.status_code == 200 and r.json()["token"]


def test_no_origin_header_still_works_everywhere(client):
    assert client.get("/health").status_code == 200
    assert client.post("/api/auth/login", json=LOGIN_BODY).status_code == 200
    r = client.get("/api/auth/me", headers=BEARER)
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_no_origin_without_token_is_still_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_preflight_from_allowed_origin_is_answered_204(client):
    r = _preflight(client, PUBLIC, "/api/schedules", "POST", "authorization, content-type, x-ack-pin")
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == PUBLIC
    assert "POST" in r.headers["access-control-allow-methods"]
    assert "x-ack-pin" in r.headers["access-control-allow-headers"]


def test_preflight_from_disallowed_origin_is_403_without_cors_headers(client):
    r = _preflight(client, EVIL, "/api/auth/login", "POST", "content-type")
    assert r.status_code == 403
    assert "access-control-allow-origin" not in r.headers


def test_preflight_without_origin_is_not_special_cased(client):
    r = client.options("/api/auth/me", headers={"access-control-request-method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_the_client_ip_never_grants_trust(client):
    # TestClient peers are loopback; a public Origin still has to be listed
    assert client.get("/health", headers={"origin": EVIL}).status_code == 403


def test_forwarded_headers_do_not_change_the_decision(client):
    h = {"origin": EVIL, "x-forwarded-for": "127.0.0.1", "x-forwarded-host": "dash.example.com", "host": "dash.example.com"}
    assert client.get("/health", headers=h).status_code == 403
    h["origin"] = PUBLIC
    assert client.get("/health", headers=h).status_code == 200


def test_hot_reload_applies_a_changed_origin_list(write_config, config_dict):
    path = write_config(config_dict)
    client = TestClient(create_app(path))
    assert client.get("/health", headers={"origin": PUBLIC}).status_code == 403
    config_dict["settings"]["ws_allowed_origins"] = [PUBLIC]
    write_config(config_dict)
    assert client.post("/config/reload", headers=BEARER).json()["reloaded"] is True
    assert client.get("/health", headers={"origin": PUBLIC}).status_code == 200


def test_websocket_uses_the_same_rule(client):
    with client.websocket_connect(f"/ws?token={TOKEN}", headers={"origin": PUBLIC}):
        pass
    with client.websocket_connect(f"/ws?token={TOKEN}", headers={"origin": EVIL}) as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close" and msg["code"] == 4403
    with client.websocket_connect(f"/ws?token={TOKEN}"):
        pass
