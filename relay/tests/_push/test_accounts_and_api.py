from __future__ import annotations

import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from _alerts.dedup import AlertKey
from _push.accounts import AccountStore, account_key
from _push.dispatcher import PushDispatcher
from _push.fcm import FcmClient
from _push.sources import active_alerts
from main import create_app

from .conftest import FakeClock, FakeFcm, alert, device


def _accounts(tmp_path, accounts=None):
    path = tmp_path / "push_accounts.json"
    path.write_text(json.dumps(accounts if accounts is not None else [
        {"account_secret": "1111", "name": "Ops", "min_severity": 2, "device_codes": []},
        {"account_secret": "2222", "name": "Boss", "min_severity": 5, "device_codes": []},
    ]))
    return path


@pytest.fixture
def app(tmp_path, write_config, config_dict):
    config_dict["settings"]["push"] = {
        "enabled": True, "accounts_file": str(_accounts(tmp_path)), "service_account_file": str(tmp_path / "missing.json"),
    }
    return create_app(write_config(config_dict))


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def login(client, secret="1111", device_name="Pixel", platform="android") -> dict:
    resp = client.post("/api/auth/login", json={"account_secret": secret, "device_name": device_name, "platform": platform})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def register(client, headers, token="tok-1", platform="android", name="Pixel", **extra):
    return client.post("/api/push/register", headers=headers, json={"token": token, "platform": platform, "device_name": name, **extra})


def devices(app):
    return {a["name"]: [d["token"] for d in a["device_codes"]] for a in app.state.push.accounts.accounts()}


def test_push_routes_need_a_session(client):
    for method, path in (("post", "/api/push/register"), ("post", "/api/push/unregister"), ("get", "/api/push/status?token=x"),
                         ("get", "/api/push/mutes"), ("post", "/api/push/mute"), ("post", "/api/push/unmute")):
        assert getattr(client, method)(path).status_code == 401


def test_register_uses_the_session_account_and_ignores_a_sent_secret(client, app):
    ops = login(client, "1111")
    resp = register(client, ops, account_secret="2222")
    assert resp.status_code == 200 and resp.json()["account"] == "Ops" and resp.json()["min_severity"] == 2
    entry = app.state.push.accounts.accounts()[0]["device_codes"][0]
    assert entry["token"] == "tok-1" and entry["platform"] == "android" and entry["device_name"] == "Pixel"
    assert entry["registered_at"].endswith("Z") and entry["last_seen_at"].endswith("Z")
    assert devices(app) == {"Ops": ["tok-1"], "Boss": []}


def test_register_is_idempotent_per_token_and_refreshes_the_entry(client, app):
    ops = login(client, "1111")
    register(client, ops, name="Old name")
    register(client, ops, name="New name")
    assert devices(app)["Ops"] == ["tok-1"]
    assert app.state.push.accounts.accounts()[0]["device_codes"][0]["device_name"] == "New name"


def test_re_registering_a_token_under_another_account_moves_it(client, app):
    register(client, login(client, "1111"))
    resp = register(client, login(client, "2222"))
    assert resp.json()["account"] == "Boss"
    assert devices(app) == {"Ops": [], "Boss": ["tok-1"]}


@pytest.mark.parametrize("body", [
    {"platform": "android", "device_name": "x"},
    {"token": "t", "platform": "windows", "device_name": "x"},
    {"token": "", "platform": "web", "device_name": "x"},
    {"token": "t", "platform": "web", "device_name": 5},
])
def test_invalid_register_bodies_are_422(client, body):
    resp = client.post("/api/push/register", headers=login(client), json=body)
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_request"


def test_unregister_and_status_are_scoped_to_the_session_account(client, app):
    ops, boss = login(client, "1111"), login(client, "2222")
    register(client, ops, token="tok-9")
    assert client.get("/api/push/status?token=tok-9", headers=ops).json() == {
        "registered": True, "account": "Ops", "min_severity": 2, "push_enabled": False,
    }
    assert client.get("/api/push/status?token=tok-9", headers=boss).json()["registered"] is False
    assert client.post("/api/push/unregister", headers=boss, json={"token": "tok-9"}).json() == {"unregistered": False}
    assert client.post("/api/push/unregister", headers=ops, json={"token": "tok-9"}).json() == {"unregistered": True}
    assert client.post("/api/push/unregister", headers=ops, json={"token": "tok-9"}).json() == {"unregistered": False}
    assert client.post("/api/push/unregister", headers=ops, json={}).status_code == 422
    assert devices(app)["Ops"] == []


def _active_machine_alert(app):
    app.state.estate_loop.latest = {}
    app.state.machine_alert_incidents.claim(AlertKey("machine", "4"), {"machine_id": 4, "severity": 2})


def test_mute_and_unmute_use_the_session_account(client, app):
    _active_machine_alert(app)
    ops, boss = login(client, "1111"), login(client, "2222")
    body = {"alertId": "machine:4"}
    assert client.post("/api/push/mute", headers=ops, json=body).json() == {"alertId": "machine:4", "muted": True}
    assert app.state.push.mutes.is_muted("machine:4", account_key("1111"))
    assert not app.state.push.mutes.is_muted("machine:4", account_key("2222"))
    assert client.get("/api/push/mutes", headers=ops).json() == {"muted": ["machine:4"]}
    assert client.get("/api/push/mutes", headers=boss).json() == {"muted": []}
    assert client.post("/api/push/unmute", headers=ops, json=body).json() == {"alertId": "machine:4", "muted": False}
    assert client.get("/api/push/mutes", headers=ops).json() == {"muted": []}


def test_mute_needs_an_active_alert_and_an_id(client, app):
    _active_machine_alert(app)
    ops = login(client)
    missing = client.post("/api/push/mute", headers=ops, json={"alertId": "machine:99"})
    assert missing.status_code == 404 and missing.json()["error"] == "unknown_alert"
    assert client.post("/api/push/mute", headers=ops, json={}).status_code == 422


def test_accounts_file_with_string_device_codes_is_normalised(tmp_path):
    path = _accounts(tmp_path, [{"account_secret": "s", "name": "N", "min_severity": 3, "device_codes": ["plain-token"]}])
    [device_entry] = AccountStore(path).accounts()[0]["device_codes"]
    assert device_entry["token"] == "plain-token" and device_entry["platform"] == "android"


def test_missing_accounts_file_means_no_accounts_and_every_secret_is_refused(tmp_path):
    store = AccountStore(tmp_path / "absent.json")
    assert store.accounts() == [] and store.find_by_secret("1111") is None and store.register("1111", "t", "web", "d") is None
    assert not (tmp_path / "absent.json").exists()


def test_a_corrupt_accounts_file_keeps_the_last_good_copy_and_refuses_writes(tmp_path):
    from _push.accounts import AccountsFileError

    path = _accounts(tmp_path)
    store = AccountStore(path)
    assert len(store.accounts()) == 2
    path.write_text("{oops")
    assert len(store.accounts()) == 2
    with pytest.raises(AccountsFileError):
        store.register("1111", "t", "web", "d")
    assert path.read_text() == "{oops"


def test_concurrent_registrations_never_lose_entries(tmp_path):
    path = _accounts(tmp_path)
    store = AccountStore(path)
    errors = []

    def work(index: int):
        try:
            for n in range(15):
                store.register("1111" if n % 2 == 0 else "2222", f"t{index}-{n}", "web", "d")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    stored = [d["token"] for a in json.loads(path.read_text()) for d in a["device_codes"]]
    assert sorted(stored) == sorted(f"t{i}-{n}" for i in range(4) for n in range(15))


def test_owner_edits_survive_a_register_and_are_visible_without_restart(tmp_path):
    path = _accounts(tmp_path)
    store = AccountStore(path)
    store.register("1111", "t1", "web", "d")
    raw = json.loads(path.read_text())
    raw.append({"account_secret": "3333", "name": "Added", "min_severity": 1, "device_codes": []})
    path.write_text(json.dumps(raw))
    assert store.find_by_secret("3333")["name"] == "Added"
    store.register("3333", "t2", "ios", "d")
    names = {a["name"]: [d["token"] for d in a["device_codes"]] for a in json.loads(path.read_text())}
    assert names == {"Ops": ["t1"], "Boss": [], "Added": ["t2"]}


async def test_every_alert_source_reaches_the_dispatcher(tmp_path, write_config, config_dict):
    config_dict["settings"]["push"] = {
        "enabled": True,
        "accounts_file": str(_accounts(tmp_path, [{"account_secret": "1111", "name": "Ops", "min_severity": 5, "device_codes": [device("ops-1")]}])),
        "service_account_file": str(tmp_path / "firebase-service-account.json"),
    }
    (tmp_path / "firebase-service-account.json").write_text(json.dumps({"project_id": "p"}))
    app = create_app(write_config(config_dict), state_dir=tmp_path / "state")
    app.state.estate_loop.latest = {}
    clock, fcm = FakeClock(), FakeFcm()
    dispatcher = PushDispatcher(
        app.state.config_store, tmp_path / "pushstate", lambda: active_alerts(app.state, app.state.config_store.get()),
        fcm_factory=lambda path: FcmClient(path, http_client=httpx.Client(transport=httpx.MockTransport(fcm.handler)), token_provider=lambda: "t"),
        clock=clock,
    )
    app.state.machine_alert_incidents.claim(AlertKey("machine", "4"), {"machine_id": 4, "severity": 2})
    app.state.pipeline_alert_incidents.claim(AlertKey("pipeline", "4002:4"), {"pipeline_id": "4002:4", "severity": 3})
    app.state.schedule_incidents.claim(AlertKey("schedule", "7"), {
        "schedule_id": 7, "name": "nightly", "severity": 3, "consecutive_failures": 3, "threshold": 3, "last_reason": "exit 1",
    })
    app.state.scheduler_endpoint_incidents.claim(AlertKey("scheduler_endpoint", "4"), {
        "machine_id": 4, "severity": 2, "state": "down", "detail": "refused",
    })
    await dispatcher.tick()
    clock.advance(16)
    await dispatcher.tick()
    sent = {r["body"]["message"]["data"]["alertId"]: r["body"]["message"] for r in fcm.requests}
    assert set(sent) == {"machine:4", "pipeline:4002:4", "schedule:7", "scheduler_endpoint:4"}
    assert sent["machine:4"]["data"]["alertKind"] == "machine"
    assert "schedule #7" in sent["schedule:7"]["notification"]["body"]
    assert "scheduler endpoint down" in sent["scheduler_endpoint:4"]["notification"]["title"]
    assert sent["scheduler_endpoint:4"]["notification"]["title"].startswith("SEV2 · ")

    fcm.clear()
    app.state.machine_alert_incidents.release(AlertKey("machine", "4"))
    app.state.schedule_incidents.release(AlertKey("schedule", "7"))
    clock.advance(1000)
    await dispatcher.tick()
    assert {r["body"]["message"]["data"]["alertId"] for r in fcm.requests} <= {"pipeline:4002:4", "scheduler_endpoint:4"}


def test_config_issues_for_push_are_computed_from_the_files():
    from _contract.schema import RelayConfig
    from config_issues import compute_config_issues

    from ..conftest import make_config_dict

    raw = make_config_dict()
    raw["settings"]["push"] = {"enabled": True, "accounts_file": "/nowhere/accounts.json", "service_account_file": "/nowhere/key.json"}
    issues = {i.code: i for i in compute_config_issues(RelayConfig.model_validate(raw))}
    assert issues["push_service_account_missing"].severity == "warning"
    assert issues["push_accounts_file_missing"].severity == "info"
    raw["settings"]["push"]["enabled"] = False
    assert not [i for i in compute_config_issues(RelayConfig.model_validate(raw)) if i.code.startswith("push_")]


def test_push_settings_defaults():
    from _contract.schema import PushSettings

    push = PushSettings()
    assert (push.enabled, push.grace_seconds, push.min_interval_seconds) == (False, 15, 120)
    assert push.accounts_file == "" and push.service_account_file == "config/firebase-service-account.json"


def test_default_accounts_file_lives_under_the_state_dir(write_config, config_dict, tmp_path):
    app = create_app(write_config(config_dict), state_dir=tmp_path / "somestate")
    assert app.state.accounts.path == (tmp_path / "somestate" / "auth" / "push_accounts.json").resolve()


def test_config_check_names_the_default_accounts_path_in_the_message():
    from _contract.schema import RelayConfig
    from config_issues import compute_config_issues
    from pathlib import Path

    from ..conftest import make_config_dict

    raw = make_config_dict()
    raw["settings"]["push"] = {"enabled": True, "service_account_file": "/nowhere/key.json"}
    issues = {i.code: i for i in compute_config_issues(RelayConfig.model_validate(raw), None, Path("/state-x"))}
    assert "push_accounts.json" in issues["push_accounts_file_missing"].message and "auth" in issues["push_accounts_file_missing"].message


class _ConfirmHarness:
    def __init__(self, tmp_path, write_config, config_dict, with_key=True):
        self.fcm = FakeFcm()
        key = tmp_path / "firebase-service-account.json"
        if with_key:
            key.write_text(json.dumps({"project_id": "demo"}))
        config_dict["settings"]["push"] = {"enabled": True, "accounts_file": str(_accounts(tmp_path)), "service_account_file": str(key)}
        self.app = create_app(write_config(config_dict))
        self.app.state.push._fcm_factory = lambda path: FcmClient(
            path, http_client=httpx.Client(transport=httpx.MockTransport(self.fcm.handler)), token_provider=lambda: "t",
        )
        self.client = TestClient(self.app)


def test_new_registration_sends_one_confirmation_to_that_token_only(tmp_path, write_config, config_dict):
    h = _ConfirmHarness(tmp_path, write_config, config_dict)
    resp = register(h.client, login(h.client, "1111"), token="new-1", name="Pixel 9")
    assert resp.json() == {"account": "Ops", "min_severity": 2, "confirmation_sent": True, "confirmation_error": None}
    [request] = h.fcm.requests
    message = request["body"]["message"]
    assert message["token"] == "new-1"
    assert message["notification"]["title"] == "Notifications enabled"
    assert message["notification"]["body"] == "Ops on Pixel 9 — you'll receive alerts of severity 1 to 2 (1 = most critical)."
    assert message["data"] == {"type": "push_enabled"}
    assert message["android"]["priority"] == "HIGH" and message["android"]["notification"]["notification_priority"] == "PRIORITY_MAX"
    assert message["apns"]["headers"] == {"apns-priority": "10", "apns-push-type": "alert"}
    assert message["apns"]["payload"]["aps"]["alert"] == message["notification"]
    assert message["webpush"]["headers"] == {"Urgency": "high", "TTL": "3600"}


def test_idempotent_reregistration_sends_nothing(tmp_path, write_config, config_dict):
    h = _ConfirmHarness(tmp_path, write_config, config_dict)
    headers = login(h.client, "1111")
    register(h.client, headers, token="new-1")
    h.fcm.clear()
    resp = register(h.client, headers, token="new-1", name="Renamed")
    assert resp.json()["confirmation_sent"] is False and resp.json()["confirmation_error"] is None
    assert h.fcm.requests == []


def test_moving_a_token_to_another_account_confirms_again(tmp_path, write_config, config_dict):
    h = _ConfirmHarness(tmp_path, write_config, config_dict)
    register(h.client, login(h.client, "1111"), token="new-1")
    h.fcm.clear()
    resp = register(h.client, login(h.client, "2222"), token="new-1")
    assert resp.json()["confirmation_sent"] is True
    assert "Boss on Pixel" in h.fcm.requests[0]["body"]["message"]["notification"]["body"]


def test_missing_key_still_registers_and_reports_why(tmp_path, write_config, config_dict):
    h = _ConfirmHarness(tmp_path, write_config, config_dict, with_key=False)
    resp = register(h.client, login(h.client, "1111"), token="new-1")
    assert resp.status_code == 200
    assert resp.json()["confirmation_sent"] is False
    assert resp.json()["confirmation_error"] == "push disabled: service account key missing"
    assert devices(h.app)["Ops"] == ["new-1"]


def test_push_disabled_in_config_reports_it(tmp_path, write_config, config_dict):
    h = _ConfirmHarness(tmp_path, write_config, config_dict)
    raw = json.loads(h.app.state.config_store.path.read_text())
    raw["settings"]["push"]["enabled"] = False
    h.app.state.config_store.path.write_text(json.dumps(raw))
    h.app.state.config_store.force_reload()
    resp = register(h.client, login(h.client, "1111"), token="new-1")
    assert resp.json()["confirmation_error"] == "push disabled in config"


def test_a_dead_token_is_removed_and_registration_still_returns_200(tmp_path, write_config, config_dict):
    h = _ConfirmHarness(tmp_path, write_config, config_dict)
    resp = register(h.client, login(h.client, "1111"), token="dead-1")
    assert resp.status_code == 200
    assert resp.json()["confirmation_sent"] is False and "removed" in resp.json()["confirmation_error"]
    assert devices(h.app)["Ops"] == []


def test_a_transient_fcm_error_keeps_the_device(tmp_path, write_config, config_dict):
    h = _ConfirmHarness(tmp_path, write_config, config_dict)
    resp = register(h.client, login(h.client, "1111"), token="err-1")
    assert resp.status_code == 200 and resp.json()["confirmation_error"].startswith("FCM error")
    assert devices(h.app)["Ops"] == ["err-1"]
