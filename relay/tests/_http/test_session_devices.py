from __future__ import annotations

import json

import pytest

from tests._accounts_helpers import get_accounts, set_accounts
from fastapi.testclient import TestClient

from main import create_app

DEV_A, DEV_B = "device-aaaaaaaa", "device-bbbbbbbb"


@pytest.fixture
def accounts_path(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([
        {"account_secret": "1111", "name": "Ops", "min_severity": 2, "device_codes": []},
        {"account_secret": "2222", "name": "Boss", "min_severity": 5, "device_codes": []},
    ]))
    return path


@pytest.fixture
def client(tmp_path, accounts_path, write_config, config_dict):
    config_dict["settings"]["push"] = {"accounts_file": str(accounts_path)}
    return TestClient(create_app(write_config(config_dict), state_dir=tmp_path / "state"))


def login(client, device_id=None, secret="1111", platform="android", name="android-device"):
    body = {"account_secret": secret, "device_name": name, "platform": platform}
    if device_id is not None:
        body["device_id"] = device_id
    resp = client.post("/api/auth/login", json=body)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def status(client, headers):
    return client.get("/api/auth/me", headers=headers).status_code


def register(client, headers, token):
    resp = client.post("/api/push/register", headers=headers, json={"token": token, "platform": "android", "device_name": "p"})
    assert resp.status_code == 200


def tokens(accounts_path):
    return [d["token"] for a in get_accounts(accounts_path.parent / "state") for d in a["device_codes"]]


def test_two_device_ids_on_the_same_account_and_platform_coexist(client):
    a, b = login(client, DEV_A), login(client, DEV_B)
    assert status(client, a) == 200 and status(client, b) == 200


def test_the_same_device_id_replaces_its_old_session(client):
    first = login(client, DEV_A)
    second = login(client, DEV_A)
    assert status(client, first) == 401 and status(client, second) == 200


def test_clients_without_a_device_id_never_evict_anything(client):
    first, second = login(client), login(client)
    with_id = login(client, DEV_A)
    assert status(client, first) == 200 and status(client, second) == 200 and status(client, with_id) == 200


def test_same_device_id_on_another_account_is_not_replaced(client):
    ops, boss = login(client, DEV_A), login(client, DEV_A, secret="2222")
    assert status(client, ops) == 200 and status(client, boss) == 200


def test_replaced_session_unregisters_its_push_token(client, accounts_path):
    first = login(client, DEV_A)
    register(client, first, "tok-old-1")
    other = login(client, DEV_B)
    register(client, other, "tok-other")
    login(client, DEV_A)
    assert tokens(accounts_path) == ["tok-other"]


def test_replacement_keeps_a_token_the_new_session_registers_again(client, accounts_path):
    first = login(client, DEV_A)
    register(client, first, "tok-same")
    second = login(client, DEV_A)
    register(client, second, "tok-same")
    assert tokens(accounts_path) == ["tok-same"]


def test_device_id_is_stored_with_the_session_and_the_push_device_and_returned_by_me(client, accounts_path):
    headers = login(client, DEV_A)
    register(client, headers, "tok-1")
    assert client.get("/api/auth/me", headers=headers).json()["device_id"] == DEV_A
    assert get_accounts(accounts_path.parent / "state")[0]["device_codes"][0]["device_id"] == DEV_A
    assert client.get("/api/auth/me", headers=login(client)).json()["device_id"] is None


def test_a_rotated_token_for_the_same_device_id_replaces_the_old_entry(client, accounts_path):
    headers = login(client, DEV_A)
    register(client, headers, "tok-1")
    register(client, headers, "tok-2")
    assert tokens(accounts_path) == ["tok-2"]


@pytest.mark.parametrize("device_id", ["short", "x" * 129, 12345345])
def test_invalid_device_ids_are_rejected(client, device_id):
    resp = client.post("/api/auth/login", json={"account_secret": "1111", "device_name": "p", "platform": "android", "device_id": device_id})
    assert resp.status_code == 422


def test_session_survives_ws_disconnect_and_relay_restart(tmp_path, accounts_path, write_config, config_dict):
    config_dict["settings"]["push"] = {"accounts_file": str(accounts_path)}
    path = write_config(config_dict)
    first = TestClient(create_app(path, state_dir=tmp_path / "state"))
    headers = login(first, DEV_A)
    register(first, headers, "tok-keep")
    token = headers["Authorization"].split()[1]
    for _ in range(2):
        with first.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_json({"type": "hello", "device": "d", "clientId": "c"})
            ws.receive_json()
    assert status(first, headers) == 200 and tokens(accounts_path) == ["tok-keep"]
    second = TestClient(create_app(path, state_dir=tmp_path / "state"))
    assert status(second, headers) == 200 and tokens(accounts_path) == ["tok-keep"]


def test_removing_the_account_ends_its_sessions(client, accounts_path):
    headers = login(client, DEV_A)
    set_accounts(accounts_path.parent / "state", [get_accounts(accounts_path.parent / "state")[1]])
    assert status(client, headers) == 401


def test_dead_token_removal_revokes_the_session_that_owned_it(client):
    headers = login(client, DEV_A)
    register(client, headers, "tok-dead")
    keep = login(client, DEV_B)
    register(client, keep, "tok-live")
    assert client.app.state.sessions.revoke_for_tokens({"tok-dead"}) == 1
    assert status(client, headers) == 401 and status(client, keep) == 200


def test_registering_the_same_token_on_every_start_is_idempotent(client, accounts_path):
    headers = login(client, DEV_A)
    for _ in range(3):
        register(client, headers, "tok-1")
        headers = login(client, DEV_A)
    register(client, headers, "tok-1")
    assert tokens(accounts_path) == ["tok-1"]
