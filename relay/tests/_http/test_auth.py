from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from _lib.log_redact import RedactWsSecretFilter
from _push.accounts import AccountStore
from tests._accounts_helpers import get_accounts, set_accounts
from _session.store import SessionStore, hash_token
from main import create_app

EXEMPT = {"/health", "/api/auth/login"}


class Clock:
    def __init__(self, now: float = 2_000_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def accounts_path(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([
        {"account_secret": "1111", "name": "Ops", "min_severity": 2, "device_codes": []},
        {"account_secret": "2222", "name": "Boss", "min_severity": 5, "device_codes": []},
    ]))
    return path


@pytest.fixture
def app(tmp_path, accounts_path, write_config, config_dict):
    config_dict["settings"]["push"] = {"accounts_file": str(accounts_path)}
    config_dict["settings"]["pin_rate_limit_attempts"] = 3
    return create_app(write_config(config_dict), state_dir=tmp_path / "state")


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def login(client, secret="1111", device_name="Pixel", platform="android"):
    return client.post("/api/auth/login", json={"account_secret": secret, "device_name": device_name, "platform": platform})


def bearer(resp) -> dict:
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_login_returns_a_token_account_and_expiry(client):
    resp = login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"token", "account", "min_severity", "expires_at"}
    assert body["account"] == "Ops" and body["min_severity"] == 2 and len(body["token"]) >= 43
    assert body["expires_at"].endswith("Z")


def test_wrong_secret_is_401_with_the_documented_shape(client):
    resp = login(client, "nope")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid_account_secret", "error": "invalid_account_secret", "message": "invalid account secret"}


def test_wrong_secrets_are_rate_limited_per_source_and_right_ones_never_count(client):
    for _ in range(20):
        assert login(client, "1111").status_code == 200
    for _ in range(3):
        assert login(client, "nope").status_code == 401
    locked = login(client, "1111")
    assert locked.status_code == 429 and locked.json()["error"] == "rate_limited"


@pytest.mark.parametrize("body", [
    {"device_name": "x", "platform": "web"},
    {"account_secret": "", "device_name": "x", "platform": "web"},
    {"account_secret": "1111", "device_name": "x", "platform": "windows"},
    {"account_secret": "1111", "device_name": 5, "platform": "web"},
])
def test_login_validates_the_body(client, body):
    resp = client.post("/api/auth/login", json=body)
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_request"


def test_only_the_token_hash_is_stored_never_the_token_or_the_secret(client, tmp_path):
    resp = login(client)
    from tests._state_helpers import dump_state_text

    raw = dump_state_text(tmp_path / "state")
    token = resp.json()["token"]
    assert token not in raw and "1111" not in raw
    cols = ("hash", "account", "account_key", "device_name", "platform", "created_at", "last_used_at", "expires_at", "push_token", "device_id")
    from _state.database import Database

    record = dict(zip(cols, Database.for_dir(tmp_path / "state").query(f"SELECT {', '.join(cols)} FROM sessions")[0]))
    assert record["hash"] == hash_token(token)
    assert {"hash", "account", "device_name", "platform", "created_at", "last_used_at", "expires_at"} <= set(record)
    assert record["account"] == "Ops" and record["device_name"] == "Pixel" and record["platform"] == "android"


def test_me_and_logout(client, app):
    headers = bearer(login(client))
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["account"] == "Ops" and me["min_severity"] == 2 and me["expires_at"].endswith("Z")
    assert client.post("/api/auth/logout", headers=headers).json() == {"logged_out": True}
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=headers).json() == {"detail": "session_invalid"}


def test_logout_also_unregisters_the_push_token_registered_through_the_session(client, app):
    headers = bearer(login(client))
    client.post("/api/push/register", headers=headers, json={"token": "tok-1", "platform": "android", "device_name": "Pixel"})
    other = bearer(login(client, "2222"))
    client.post("/api/push/register", headers=other, json={"token": "tok-2", "platform": "web", "device_name": "Browser"})
    client.post("/api/auth/logout", headers=headers)
    tokens = {d["token"] for a in app.state.push.accounts.accounts() for d in a["device_codes"]}
    assert tokens == {"tok-2"}


def test_removing_an_account_invalidates_its_sessions_on_next_use(client, tmp_path):
    ops, boss = bearer(login(client, "1111")), bearer(login(client, "2222"))
    set_accounts(tmp_path / "state", [a for a in get_accounts(tmp_path / "state") if a["name"] != "Ops"])
    assert client.get("/api/auth/me", headers=ops).status_code == 401
    assert client.get("/api/auth/me", headers=boss).status_code == 200


def test_changing_an_accounts_secret_invalidates_its_sessions(client, tmp_path):
    ops = bearer(login(client, "1111"))
    raw = get_accounts(tmp_path / "state")
    raw[0]["account_secret"] = "9999"
    set_accounts(tmp_path / "state", raw)
    assert client.get("/api/auth/me", headers=ops).status_code == 401
    assert login(client, "1111").status_code == 401
    assert login(client, "9999").status_code == 200


def test_min_severity_changes_in_the_database_show_up_without_a_new_login(client, tmp_path):
    ops = bearer(login(client, "1111"))
    raw = get_accounts(tmp_path / "state")
    raw[0]["min_severity"] = 4
    set_accounts(tmp_path / "state", raw)
    assert client.get("/api/auth/me", headers=ops).json()["min_severity"] == 4


def _routes(app):
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() not in {"HEAD", "OPTIONS"}:
                yield method.upper(), path


def _url(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def test_every_rest_route_rejects_requests_without_a_session_except_health_and_login(app, client):
    seen = set()
    for method, path in _routes(app):
        url = _url(path)
        resp = client.request(method, url, json={})
        seen.add(path)
        if path in EXEMPT:
            assert resp.status_code != 401 or path == "/api/auth/login", (method, path)
        else:
            assert resp.status_code == 401 and resp.json() == {"detail": "session_invalid"}, (method, path, resp.status_code)
    assert {
        "/api/schedules", "/api/push/register", "/api/auth/me", "/api/auth/logout", "/ops/silence", "/api/config/check",
        "/api/downtime", "/api/downtime/{window_id}", "/api/push/window",
    } <= seen


def test_every_rest_route_accepts_a_valid_session(app, client):
    headers = bearer(login(client))
    for method, path in _routes(app):
        if path in EXEMPT:
            continue
        url = _url(path)
        if method != "GET" or "/api/auth/logout" in path:
            continue
        assert client.request(method, url, headers=headers).status_code != 401, (method, path)


def test_client_id_and_secret_alone_never_grant_rest_access(client):
    for kwargs in (
        {"headers": {"X-Client-Id": "test_web_id", "X-Client-Secret": "test_web_secret"}},
        {"params": {"clientId": "test_web_id", "clientSecret": "test_web_secret"}},
        {"headers": {"Authorization": "Bearer test_web_secret"}},
    ):
        assert client.get("/api/config/check", **kwargs).status_code == 401


def test_health_needs_no_session(client):
    assert client.get("/health").status_code == 200


def test_token_in_the_query_string_is_accepted_for_rest_too(client):
    token = login(client).json()["token"]
    assert client.get("/api/auth/me", params={"token": token}).status_code == 200


def test_sessions_survive_a_relay_restart(tmp_path, accounts_path, write_config, config_dict):
    config_dict["settings"]["push"] = {"accounts_file": str(accounts_path)}
    path = write_config(config_dict)
    first = TestClient(create_app(path, state_dir=tmp_path / "state"))
    headers = bearer(login(first))
    second = TestClient(create_app(path, state_dir=tmp_path / "state"))
    assert second.get("/api/auth/me", headers=headers).status_code == 200


def _store(tmp_path, accounts_path, clock):
    from _state.database import Database

    set_accounts(tmp_path / "state", json.loads(accounts_path.read_text()))
    accounts = AccountStore(Database.for_dir(tmp_path / "state"))
    return SessionStore(tmp_path / "state", lambda: accounts, clock=clock), accounts


def test_sessions_never_expire_from_idle_time(tmp_path, accounts_path):
    clock = Clock()
    store, accounts = _store(tmp_path, accounts_path, clock)
    token, _ = store.create(accounts.find_by_secret("1111"), "Pixel", "android")
    clock.now += 5 * 365 * 86400
    assert store.verify(token)[0] is not None
    reloaded, _ = _store(tmp_path, accounts_path, clock)
    assert reloaded.verify(token)[0] is not None


def test_expires_at_is_renewed_on_use_but_touched_at_most_once_a_minute(tmp_path, accounts_path):
    clock = Clock()
    store, accounts = _store(tmp_path, accounts_path, clock)
    token, session = store.create(accounts.find_by_secret("1111"), "Pixel", "android")
    created = session.record.expires_at
    clock.now += 30
    assert store.verify(token)[0].record.expires_at == created
    clock.now += 40
    assert store.verify(token)[0].record.expires_at > created


def test_token_is_redacted_in_logs():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, 'WebSocket /ws?token=abcDEF_123-xyz&x=1 "accepted"', None, None)
    RedactWsSecretFilter().filter(record)
    assert "abcDEF_123-xyz" not in record.getMessage() and "token=***REDACTED***&x=1" in record.getMessage()
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "GET %s", ("/ws?clientSecret=s3&token=tt",), None)
    RedactWsSecretFilter().filter(record)
    assert "s3" not in record.getMessage() and "tt" not in record.getMessage().replace("***", "")


def test_rejections_are_logged_without_the_token(client, caplog):
    with caplog.at_level(logging.WARNING, logger="relay.auth"):
        client.get("/api/auth/me", headers={"Authorization": "Bearer secret-token-value"})
        client.get("/api/auth/me")
    assert "session rejected: unknown (token from header)" in caplog.text
    assert "session rejected: missing (token from none)" in caplog.text
    assert "secret-token-value" not in caplog.text
