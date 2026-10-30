from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from _alerts.dedup import AlertKey
from _contract.schema import Machine
from _scheduler.wire import schedule_detail, scheduler_alerts_wire
from main import create_app

from .conftest import Harness, SCRIPT
from .test_http_schedules import AUTH, BODY, PIN

FORBIDDEN_TEXT = (
    "scheduler endpoint refused the relay IP: the endpoint saw the relay from a different IP than allowed "
    "(e.g. via another network adapter) - add that IP to machine_scheduler.allowed_ips"
)
BASE = {"machine_id": 1, "machine_ip": "10.0.0.1", "machine_name": "m", "machine_type": "t", "machine_os": "Windows"}


def machine(value) -> Machine:
    return Machine.model_validate({**BASE, "machine_scheduler": value})


def test_bool_forms():
    on, off = machine(True), machine(False)
    assert (on.machine_scheduler, on.scheduler_defaults) == (True, None)
    assert (off.machine_scheduler, off.scheduler_defaults) == (False, None)
    assert Machine.model_validate(BASE).machine_scheduler is False


def test_object_form_is_normalised():
    m = machine({"enabled": True, "fail_threshold": 2, "timeout_minutes": 5, "retries": 1, "retry_delay_seconds": 9, "run_if_missed": True})
    assert m.machine_scheduler is True
    assert m.scheduler_defaults.model_dump(exclude={"enabled"}, exclude_none=True) == {
        "fail_threshold": 2, "timeout_minutes": 5, "retries": 1, "retry_delay_seconds": 9, "run_if_missed": True,
    }


def test_object_form_enabled_false_and_without_overrides():
    assert machine({"enabled": False, "fail_threshold": 2}).machine_scheduler is False
    only = machine({"enabled": True})
    assert only.machine_scheduler is True and only.scheduler_defaults is None


def test_object_form_rejects_unknown_keys_and_bad_values():
    for bad in ({"enabled": True, "bogus": 1}, {"fail_threshold": 0}, {"timeout_minutes": -1}, {"retries": -1}):
        with pytest.raises(ValidationError):
            machine(bad)


def with_machine_scheduler(tmp_path, config_dict, value, **scheduler) -> Harness:
    config_dict["scheduler"] = scheduler
    h = Harness(tmp_path, config_dict)
    raw = json.loads(h.config_store.path.read_text())
    raw["machines"][0]["machine_scheduler"] = value
    h.config_store.path.write_text(json.dumps(raw))
    h.config_store.force_reload()
    return h


def detail(h, schedule):
    return schedule_detail(schedule, h.config_store.get(), h.health, h.runlog, h.loop)


def test_precedence_schedule_then_machine_then_default(tmp_path, config_dict):
    h = with_machine_scheduler(
        tmp_path, config_dict, {"enabled": True, "fail_threshold": 2, "retries": 4, "timeout_minutes": 3}, default_fail_threshold=7,
    )
    own = h.add(fail_threshold=9, controls={"retries": 1})
    d = detail(h, own)
    assert d["effective"] == {
        "timeout_s": 180, "retries": 1, "retry_delay_s": 60, "run_if_missed": False, "fail_threshold": 9,
    }
    assert d["effective_source"] == {
        "timeout_s": "machine", "retries": "schedule", "retry_delay_s": "default", "run_if_missed": "default",
        "fail_threshold": "schedule",
    }
    inherits = h.add(controls={})
    d = detail(h, inherits)
    assert d["fail_threshold"] == 2 and d["effective"]["retries"] == 4
    assert d["effective_source"]["fail_threshold"] == "machine" and d["effective_source"]["retries"] == "machine"


def test_default_applies_when_machine_has_no_overrides(tmp_path, config_dict):
    h = with_machine_scheduler(tmp_path, config_dict, True, default_fail_threshold=7)
    d = detail(h, h.add(controls={}))
    assert d["fail_threshold"] == 7 and set(d["effective_source"].values()) == {"default"}


async def test_machine_fail_threshold_drives_the_alert(tmp_path, config_dict):
    h = with_machine_scheduler(tmp_path, config_dict, {"enabled": True, "fail_threshold": 1}, default_fail_threshold=5)
    h.endpoint.add_script(SCRIPT, exit_code=1)
    schedule = h.add()
    h.loop.start()
    h.loop.dispatch(schedule, "manual")
    await h.settle()
    assert h.schedule_incidents.is_open(AlertKey("schedule", str(schedule.id)))
    alert = scheduler_alerts_wire(h.schedule_incidents, h.endpoint_incidents, h.config_store.get())[0]
    assert "alert at 1" in alert["reason"]
    await h.loop.shutdown()


async def test_machine_retries_apply_to_runs(tmp_path, config_dict):
    h = with_machine_scheduler(tmp_path, config_dict, {"enabled": True, "retries": 1, "retry_delay_seconds": 0})
    h.endpoint.add_script(SCRIPT, exit_code=1)
    schedule = h.add(controls={})
    h.loop.start()
    h.loop.dispatch(schedule, "manual")
    await h.settle()
    assert h.runs(schedule.id)[0]["attempts"] == 2
    await h.loop.shutdown()


@pytest.fixture
def app(write_config, config_dict):
    config_dict["machines"][0]["machine_scheduler"] = {"enabled": True, "fail_threshold": 2}
    application = create_app(write_config(config_dict))
    application.state.scheduler_loop.enabled = True
    return application


def snapshot(app) -> dict:
    with TestClient(app).websocket_connect("/ws?token=test-session-token-0123456789abcdef0123456789abcdef") as ws:
        ws.send_json({"type": "hello", "device": "d", "clientId": "c"})
        return ws.receive_json()


def test_ws_machine_keeps_a_boolean_and_exposes_defaults(app):
    m = snapshot(app)["machines"][0]
    assert m["machine_scheduler"] is True
    assert m["scheduler_defaults"] == {"fail_threshold": 2}


def test_ws_defaults_are_null_for_the_bool_form(write_config, config_dict):
    config_dict["machines"][0]["machine_scheduler"] = True
    m = snapshot(create_app(write_config(config_dict)))["machines"][0]
    assert m["machine_scheduler"] is True and m["scheduler_defaults"] is None


def test_rest_detail_has_effective_source_and_machine_threshold(app):
    client = TestClient(app)
    resp = client.post("/api/schedules", json=BODY, headers=PIN)
    body = resp.json()
    assert body["fail_threshold"] == 2 and body["effective_source"]["fail_threshold"] == "machine"
    assert body["stored_fail_threshold"] is None


async def test_forbidden_ip_has_its_own_reason_and_alert_text(harness):
    machine_cfg = harness.config_store.get().machines[0]
    harness.endpoint.forbid = True
    await harness.health.tick()
    assert harness.health.wire_state(machine_cfg) == "auth_rejected"
    assert harness.health.reason(machine_cfg) == FORBIDDEN_TEXT
    alert = scheduler_alerts_wire(harness.schedule_incidents, harness.endpoint_incidents, harness.config_store.get())[0]
    assert alert["reason"].endswith(FORBIDDEN_TEXT)

    harness.endpoint.forbid, harness.endpoint.reject_keys = False, True
    await harness.health.tick()
    assert harness.health.reason(machine_cfg) == "scheduler auth rejected: check clock / machine IP"


async def test_forbidden_ip_at_fire_time_is_logged_as_missed_with_that_text(harness):
    harness.endpoint.forbid = True
    schedule = harness.add()
    harness.loop.start()
    harness.loop.dispatch(schedule, "manual")
    await harness.settle()
    assert harness.runs(schedule.id)[0]["reason"] == FORBIDDEN_TEXT
    await harness.loop.shutdown()


async def test_only_skipped_locked_run_is_not_reported_as_never_run(harness):
    harness.endpoint.runs["other-1"] = {
        "script": SCRIPT.lower(), "status": "running", "exit_code": None, "started_at": None, "ended_at": None,
        "duration_ms": None, "stdout_tail": "", "stderr_tail": "", "polls_seen": 0, "plan": 0,
    }
    schedule = harness.add()
    harness.loop.start()
    harness.loop.dispatch(schedule, "manual")
    await harness.settle()
    harness.health.state_for(1).state = "up"
    row = detail(harness, schedule)
    assert row["last_outcome"] == "skipped_locked" and row["state"] == "grey"
    assert row["reason"] == "no completed run yet"
    await harness.loop.shutdown()


def test_never_run_keeps_its_text(harness):
    harness.health.state_for(1).state = "up"
    assert detail(harness, harness.add())["reason"] == "never run"


SESSION_BAD = [
    ({}, "missing", "none"),
    ({"Authorization": "Bearer not-a-real-session-token-S3CRET"}, "unknown", "header"),
    ({"X-Client-Id": "test_web_id", "X-Client-Secret": "test_web_secret"}, "missing", "none"),
]


@pytest.mark.parametrize("headers,cause,source", SESSION_BAD)
def test_session_rejection_is_logged_and_distinguishable(app, caplog, headers, cause, source):
    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="relay.auth"):
        resp = client.post("/api/schedules/1/run", headers={**headers, "X-Ack-Pin": "1234"})
    assert resp.status_code == 401 and resp.json() == {"detail": "session_invalid"}
    assert f"session rejected: {cause} (token from {source})" in caplog.text
    assert "/api/schedules/1/run" in caplog.text and "client_ip=" in caplog.text
    assert "S3CRET" not in caplog.text and "test_web_secret" not in caplog.text


def test_query_token_source_is_reported(app, caplog):
    with caplog.at_level(logging.WARNING, logger="relay.auth"):
        TestClient(app).get("/api/schedules?token=wrong-token-value")
    assert "unknown (token from query)" in caplog.text and "wrong-token-value" not in caplog.text


def test_pin_401_stays_distinct_from_client_401(app):
    client = TestClient(app)
    resp = client.post("/api/schedules/1/run", headers={**AUTH, "X-Ack-Pin": "0000"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid_pin", "error": "invalid_pin", "message": "wrong PIN"}


def test_correct_credentials_and_pin_are_stable_across_many_calls(app):
    client = TestClient(app)
    client.post("/api/schedules", json=BODY, headers=PIN)
    codes = {client.post("/api/schedules/1/run", headers=PIN).status_code for _ in range(30)}
    assert codes <= {200, 202}


def test_machine_side_keys_are_accepted_and_ignored_functionally():
    m = machine({"enabled": True, "allowed_ips": ["10.0.0.5", "192.168.1.9"], "interpreters": {".py": r"C:\Python\python.exe"}})
    assert m.machine_scheduler is True
    assert m.scheduler_defaults is None


def test_machine_side_keys_do_not_leak_into_scheduler_defaults():
    m = machine({"enabled": True, "fail_threshold": 2, "allowed_ips": ["10.0.0.5"], "interpreters": {".py": "py"}})
    assert m.scheduler_defaults.model_dump(exclude_none=True, exclude={"enabled"}) == {"fail_threshold": 2}


@pytest.mark.parametrize("bad", [["not-an-ip"], ["10.0.0.256"], ["::1"], "10.0.0.1"])
def test_allowed_ips_must_be_an_ipv4_list(bad):
    with pytest.raises(ValidationError):
        machine({"enabled": True, "allowed_ips": bad})


def test_interpreters_must_map_strings():
    with pytest.raises(ValidationError):
        machine({"enabled": True, "interpreters": {".py": 3}})
    with pytest.raises(ValidationError):
        machine({"enabled": True, "interpreters": [".py"]})


def test_scheduler_extra_allowed_ips_validated():
    from _contract.schema import SchedulerSettings

    assert SchedulerSettings(extra_allowed_ips=["10.1.1.1"]).extra_allowed_ips == ["10.1.1.1"]
    assert SchedulerSettings().extra_allowed_ips == []
    with pytest.raises(ValidationError):
        SchedulerSettings(extra_allowed_ips=["nope"])


def test_same_machine_entry_works_in_a_full_relay_config(write_config, config_dict):
    config_dict["machines"][0]["machine_scheduler"] = {
        "enabled": True, "fail_threshold": 2, "allowed_ips": ["10.0.0.5"], "interpreters": {".py": "py"},
    }
    config_dict["scheduler"] = {"extra_allowed_ips": ["10.9.9.9"]}
    config = create_app(write_config(config_dict)).state.config_store.get()
    assert config.machines[0].machine_scheduler is True and config.scheduler.extra_allowed_ips == ["10.9.9.9"]


def test_forbidden_ip_text_carries_the_adapter_hint():
    assert "different IP than allowed" in FORBIDDEN_TEXT and "machine_scheduler.allowed_ips" in FORBIDDEN_TEXT
