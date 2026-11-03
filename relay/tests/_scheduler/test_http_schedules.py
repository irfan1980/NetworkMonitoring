from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from _scheduler.exec_key import exec_key
from _scheduler.executor_client import ExecutorClient
from _scheduler.inflight import ActiveRun
from main import create_app

from .fake_endpoint import FakeEndpoint

AUTH = {"Authorization": "Bearer test-session-token-0123456789abcdef0123456789abcdef"}
PIN = {"X-Ack-Pin": "1234", **AUTH}
BAD_PIN = {"X-Ack-Pin": "0000", **AUTH}
SCRIPT = r"D:\jobs\ok.cmd"

BODY = {
    "name": "nightly",
    "machine_id": 1,
    "script": SCRIPT,
    "args": ["-v"],
    "trigger": {"kind": "daily", "time": "02:00"},
}


def build_app(write_config, config_dict, attempts: int):
    config_dict["machines"][0]["machine_scheduler"] = True
    config_dict["settings"]["pin_rate_limit_attempts"] = attempts
    application = create_app(write_config(config_dict))
    application.state.scheduler_loop.enabled = True
    return application


@pytest.fixture
def app(write_config, config_dict):
    return build_app(write_config, config_dict, 100)


@pytest.fixture
def limited_client(write_config, config_dict) -> TestClient:
    return TestClient(build_app(write_config, config_dict, 3))


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def create(client, **overrides) -> dict:
    resp = client.post("/api/schedules", json={**BODY, **overrides}, headers=PIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


WRITE_ROUTES = [
    ("post", "/api/schedules", BODY),
    ("put", "/api/schedules/1", BODY),
    ("delete", "/api/schedules/1", None),
    ("post", "/api/schedules/1/enable", None),
    ("post", "/api/schedules/1/disable", None),
    ("post", "/api/schedules/check-script", {"machine_id": 1, "script": SCRIPT}),
    ("post", "/api/schedules/1/run", None),
    ("post", "/api/schedules/1/stop", None),
]


def call(client, method, path, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(path, **kwargs)


@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
def test_every_write_route_needs_client_auth(client, method, path, body):
    assert call(client, method, path, body, {"X-Ack-Pin": "1234"}).status_code == 401


@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
def test_every_write_route_rejects_missing_and_wrong_pin(client, method, path, body):
    assert call(client, method, path, body, AUTH).status_code == 401
    assert call(client, method, path, body, BAD_PIN).status_code == 401


@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
def test_every_write_route_is_rate_limited_with_429(limited_client, method, path, body):
    codes = [call(limited_client, method, path, body, BAD_PIN).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3] == 429
    assert call(limited_client, method, path, body, PIN).status_code == 429


def test_correct_pin_is_never_rate_limited_on_write_routes(write_config, config_dict):
    limited = TestClient(build_app(write_config, config_dict, 3))
    for _ in range(20):
        assert limited.post("/api/schedules", json=BODY, headers=PIN).status_code == 201


def test_nothing_is_written_without_the_pin(client, app):
    client.post("/api/schedules", json=BODY, headers=BAD_PIN)
    assert app.state.schedule_store.list() == []


def test_read_routes_need_client_auth_but_not_the_pin(client):
    create(client)
    for path in ("/api/schedules", "/api/schedules/1", "/api/schedules/1/runs"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers=AUTH).status_code == 200
    assert client.post("/api/schedules/preview", json={"trigger": {"kind": "daily", "time": "02:00"}}).status_code == 401


def test_create_returns_the_detail_shape(client):
    detail = create(client, fail_threshold=5)
    assert detail["id"] == 1 and detail["name"] == "nightly" and detail["enabled"] is True
    assert detail["trigger"]["kind"] == "daily" and detail["trigger_summary"] == "daily at 02:00 UTC"
    assert detail["state"] == "grey" and detail["reason"] == "endpoint not checked yet"
    assert detail["fail_threshold"] == 5 and detail["stored_fail_threshold"] == 5
    assert detail["effective"] == {
        "timeout_s": 0, "retries": 0, "retry_delay_s": 60, "run_if_missed": False, "fail_threshold": 5,
    }
    assert detail["timeout_display"] == "none"
    assert detail["running"] is False and detail["last_run"] is None and detail["args"] == ["-v"]
    assert detail["next_run"] is None


def test_ids_are_sequential(client):
    assert [create(client)["id"] for _ in range(3)] == [1, 2, 3]


def test_list_get_and_404(client):
    create(client)
    assert [s["id"] for s in client.get("/api/schedules", headers=AUTH).json()["schedules"]] == [1]
    assert client.get("/api/schedules/1", headers=AUTH).json()["id"] == 1
    missing = client.get("/api/schedules/9", headers=AUTH)
    assert missing.status_code == 404 and missing.json()["error"] == "not_found"


def test_machine_must_exist_and_have_the_scheduler_flag(client, app):
    bad = client.post("/api/schedules", json={**BODY, "machine_id": 77}, headers=PIN)
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_machine"
    app.state.config_store.get().machines[0].machine_scheduler = False
    off = client.post("/api/schedules", json=BODY, headers=PIN)
    assert off.status_code == 422 and "machine_scheduler" in off.json()["message"]


def test_invalid_body_gives_422_with_a_message(client):
    resp = client.post("/api/schedules", json={**BODY, "trigger": {"kind": "cron", "cron": "99 * * * *"}}, headers=PIN)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_schedule" and "cron" in body["message"] and body["errors"][0]["field"].startswith("trigger")
    assert client.post("/api/schedules", json={"name": "x"}, headers=PIN).status_code == 422


def test_after_schedule_must_reference_an_existing_non_cyclic_schedule(client):
    first = create(client)
    ok = {"kind": "event", "event": "after_schedule", "after_schedule_id": first["id"], "on": "any"}
    second = create(client, trigger=ok)
    assert client.post("/api/schedules", json={**BODY, "trigger": {**ok, "after_schedule_id": 99}}, headers=PIN).status_code == 422
    cyc = {**BODY, "trigger": {"kind": "event", "event": "after_schedule", "after_schedule_id": second["id"]}}
    assert client.put(f"/api/schedules/{first['id']}", json=cyc, headers=PIN).status_code == 422
    selfref = {**BODY, "trigger": {"kind": "event", "event": "after_schedule", "after_schedule_id": first["id"]}}
    assert client.put(f"/api/schedules/{first['id']}", json=selfref, headers=PIN).status_code == 422


def test_update_delete_enable_disable(client):
    create(client)
    updated = client.put("/api/schedules/1", json={**BODY, "name": "renamed"}, headers=PIN)
    assert updated.status_code == 200 and updated.json()["name"] == "renamed"
    assert client.post("/api/schedules/1/disable", headers=PIN).json()["enabled"] is False
    assert client.get("/api/schedules/1", headers=AUTH).json()["reason"] == "disabled"
    assert client.post("/api/schedules/1/enable", headers=PIN).json()["enabled"] is True
    assert client.delete("/api/schedules/1", headers=PIN).json() == {"deleted": True, "id": 1}
    assert client.get("/api/schedules/1", headers=AUTH).status_code == 404
    assert client.put("/api/schedules/1", json=BODY, headers=PIN).status_code == 404
    assert client.post("/api/schedules/1/enable", headers=PIN).status_code == 404


def test_put_and_delete_while_running_give_409(client, app):
    create(client)
    app.state.scheduler_loop.active["r1"] = ActiveRun(
        run_id="r1", schedule_id=1, trigger="scheduled", machine_id=1, script=SCRIPT, args=[],
        timeout_s=60, retries=0, retry_delay_s=0, started_at="2026-03-01T12:00:00Z",
    )
    for resp in (client.put("/api/schedules/1", json=BODY, headers=PIN), client.delete("/api/schedules/1", headers=PIN)):
        assert resp.status_code == 409
        assert resp.json() == {"error": "running", "message": "running, try again after it finishes"}
    assert client.get("/api/schedules/1", headers=AUTH).json()["running"] is True
    assert app.state.schedule_store.get(1) is not None


def test_writes_are_refused_when_this_relay_does_not_hold_the_scheduler_lock(client, app):
    app.state.scheduler_loop.enabled = False
    resp = client.post("/api/schedules", json=BODY, headers=PIN)
    assert resp.status_code == 409 and resp.json()["detail"]["error"] == "scheduler_disabled"
    assert client.get("/api/schedules", headers=AUTH).status_code == 200


def test_preview_returns_five_utc_times(client):
    resp = client.post("/api/schedules/preview", json={"trigger": {"kind": "daily", "time": "10:30"}}, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["next"]) == 5 and all(t.endswith("Z") and "T10:30:00" in t for t in body["next"])
    assert body["summary"] == "daily at 10:30 UTC"


def test_preview_invalid_cron_is_422_with_message(client):
    resp = client.post("/api/schedules/preview", json={"trigger": {"kind": "cron", "cron": "nope"}}, headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_trigger" and "cron" in resp.json()["message"]


def test_preview_of_events_is_empty(client):
    body = client.post("/api/schedules/preview", json={"trigger": {"kind": "event", "event": "relay_start"}}, headers=AUTH).json()
    assert body["next"] == []


def test_runs_endpoint_pages_newest_first(client, app):
    create(client)
    for i in range(5):
        app.state.run_log.append(1, {
            "run_id": f"r{i}", "trigger": "scheduled", "started_at": "2026-03-01T12:00:00Z",
            "ended_at": "2026-03-01T12:00:01Z", "attempts": 1, "exit_code": 0, "outcome": "success",
            "reason": None, "stdout_tail": "", "stderr_tail": "",
        })
    first = client.get("/api/schedules/1/runs?limit=2", headers=AUTH).json()
    assert [r["run_id"] for r in first["runs"]] == ["r4", "r3"] and first["next_before"] == 4
    nxt = client.get(f"/api/schedules/1/runs?limit=2&before={first['next_before']}", headers=AUTH).json()
    assert [r["run_id"] for r in nxt["runs"]] == ["r2", "r1"]
    last = client.get("/api/schedules/1/runs?limit=2&before=2", headers=AUTH).json()
    assert [r["run_id"] for r in last["runs"]] == ["r0"] and last["next_before"] is None
    assert client.get("/api/schedules/9/runs", headers=AUTH).status_code == 404
    assert client.get("/api/schedules/1/runs?limit=0", headers=AUTH).status_code == 422


def mock_endpoint(app) -> FakeEndpoint:
    endpoint = FakeEndpoint()
    endpoint.add_script(SCRIPT)
    app.state.executor_client = ExecutorClient(
        lambda: app.state.config_store.get().scheduler, transport=httpx.MockTransport(endpoint.handler)
    )
    return endpoint


def test_check_script_proxies_the_endpoint(client, app):
    endpoint = mock_endpoint(app)
    ok = client.post("/api/schedules/check-script", json={"machine_id": 1, "script": SCRIPT}, headers=PIN)
    assert ok.json() == {"exists": True, "is_file": True}
    missing = client.post("/api/schedules/check-script", json={"machine_id": 1, "script": r"D:\no.cmd"}, headers=PIN)
    assert missing.json() == {"exists": False, "is_file": False}
    assert endpoint.requests[0].headers["x-exec-key"] == exec_key(1, "127.0.0.1")

    endpoint.down = True
    down = client.post("/api/schedules/check-script", json={"machine_id": 1, "script": SCRIPT}, headers=PIN)
    assert down.status_code == 502 and down.json()["error"] == "endpoint_unavailable"
    endpoint.down, endpoint.reject_keys = False, True
    rejected = client.post("/api/schedules/check-script", json={"machine_id": 1, "script": SCRIPT}, headers=PIN)
    assert rejected.status_code == 502 and rejected.json()["error"] == "auth_rejected"


def test_check_script_validates_input_and_machine(client):
    assert client.post("/api/schedules/check-script", json={"machine_id": 1}, headers=PIN).status_code == 422
    assert client.post("/api/schedules/check-script", json={"machine_id": 5, "script": "x"}, headers=PIN).status_code == 422


def test_manual_run_logs_a_manual_missed_when_the_machine_is_offline(client, app):
    create(client)
    resp = client.post("/api/schedules/1/run", headers=PIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "missed" and body["reason"] == "machine offline"
    row = app.state.run_log.read(1)[0][0]
    assert row["run_id"] == body["run_id"] and row["trigger"] == "manual"


def test_manual_run_of_a_machine_without_the_flag_is_422(client, app):
    create(client)
    app.state.config_store.get().machines[0].machine_scheduler = False
    assert client.post("/api/schedules/1/run", headers=PIN).status_code == 422


def test_ws_snapshot_carries_schedules_and_machine_scheduler_fields(client, app):
    create(client)
    app.state.run_log.append(1, {
        "run_id": "r1", "trigger": "scheduled", "started_at": "2026-03-01T12:00:00Z", "ended_at": "2026-03-01T12:00:01Z",
        "attempts": 1, "exit_code": 0, "outcome": "success", "reason": None, "stdout_tail": "", "stderr_tail": "",
    })
    app.state.machine_registry.state_for(1).status = "online"
    app.state.health_probe.state_for(1).state = "up"
    with client.websocket_connect("/ws?token=test-session-token-0123456789abcdef0123456789abcdef") as ws:
        ws.send_json({"type": "hello", "device": "d", "clientId": "c"})
        snap = ws.receive_json()
    assert snap["type"] == "snapshot"
    machine = snap["machines"][0]
    assert machine["machine_scheduler"] is True and machine["scheduler_state"] == "up"
    row = snap["schedules"][0]
    assert set(row) >= {
        "id", "name", "machine_id", "script", "trigger_summary", "next_run", "last_run", "last_outcome",
        "consecutive_failures", "fail_threshold", "state", "running",
    }
    assert row["state"] == "green" and row["last_outcome"] == "success" and row["last_run"] == "2026-03-01T12:00:01Z"
    assert row["next_run"].endswith("Z") and row["trigger_summary"] == "daily at 02:00 UTC" and row["running"] is False
    assert "reason" not in row


def test_ws_schedule_is_grey_with_a_reason_when_the_endpoint_is_down(client, app):
    create(client)
    app.state.machine_registry.state_for(1).status = "online"
    app.state.health_probe.state_for(1).state = "down"
    with client.websocket_connect("/ws?token=test-session-token-0123456789abcdef0123456789abcdef") as ws:
        ws.send_json({"type": "hello", "device": "d", "clientId": "c"})
        snap = ws.receive_json()
    assert snap["schedules"][0]["state"] == "grey" and snap["schedules"][0]["reason"] == "endpoint down"
    assert snap["machines"][0]["scheduler_state"] == "down"
    assert snap["machines"][0]["scheduler_reason"] == "endpoint down"


def test_ws_active_alerts_include_schedule_and_endpoint_alerts(client, app):
    from _alerts.dedup import AlertKey

    create(client)
    app.state.schedule_incidents.claim(AlertKey("schedule", "1"), {
        "schedule_id": 1, "name": "nightly", "severity": 3, "consecutive_failures": 3, "threshold": 3, "last_reason": "exit code 1",
    })
    app.state.scheduler_endpoint_incidents.claim(AlertKey("scheduler_endpoint", "1"), {
        "machine_id": 1, "severity": 2, "state": "auth_rejected", "detail": "x",
    })
    with client.websocket_connect("/ws?token=test-session-token-0123456789abcdef0123456789abcdef") as ws:
        ws.send_json({"type": "hello", "device": "d", "clientId": "c"})
        alerts = {a["alertId"]: a for a in ws.receive_json()["activeAlerts"]}
    assert alerts["schedule:1"]["kind"] == "schedule" and alerts["schedule:1"]["severity"] == 3
    assert "failed 3 times in a row" in alerts["schedule:1"]["reason"]
    assert alerts["scheduler_endpoint:1"]["reason"].endswith("scheduler auth rejected: check clock / machine IP")
    assert alerts["scheduler_endpoint:1"]["severity"] == 2


def test_object_severities_are_config_overridable(write_config, config_dict):
    config_dict["alerts"] = {"object_severities": {"schedule": 1, "scheduler_endpoint": 4}}
    config = create_app(write_config(config_dict)).state.config_store.get()
    assert (config.alerts.object_severities.schedule, config.alerts.object_severities.scheduler_endpoint) == (1, 4)


def test_config_check_flags_schedules_with_a_bad_machine(client, app):
    create(client)
    app.state.config_store.get().machines[0].machine_scheduler = False
    codes = [i["code"] for i in client.get("/api/config/check", headers=AUTH).json()["configIssues"]]
    assert "schedule_machine_scheduler_off" in codes
    app.state.config_store.get().machines.clear()
    codes = [i["code"] for i in client.get("/api/config/check", headers=AUTH).json()["configIssues"]]
    assert "schedule_machine_missing" in codes


def test_scheduler_config_section_is_optional_and_defaulted(write_config, config_dict):
    config = create_app(write_config(config_dict)).state.config_store.get().scheduler
    assert config.model_dump() == {
        "executor_port": 8095, "relay_ip": "10.101.8.85", "tick_seconds": 1, "health_probe_seconds": 5,
        "health_down_after_misses": 2, "default_timeout_minutes": 0, "default_retries": 0,
        "default_retry_delay_seconds": 60, "default_fail_threshold": 3, "default_run_if_missed": False,
        "success_exit_codes": [0], "log_keep_days": 90, "log_keep_runs": 1000, "log_output_tail_kb": 64, "log_max_age_days": 30, "extra_allowed_ips": [],
    }
    assert config_dict["machines"][0].get("machine_scheduler") is None


def test_schedules_persist_across_app_instances(write_config, config_dict, app, client, tmp_path):
    create(client)
    again = create_app(write_config(config_dict), state_dir=app.state.state_dir)
    assert [s.name for s in again.state.schedule_store.list()] == ["nightly"]


def test_stop_route_not_running_is_409_and_unknown_is_404(client):
    create(client)
    resp = client.post("/api/schedules/1/stop", headers=PIN)
    assert resp.status_code == 409 and resp.json()["error"] == "not_running"
    assert client.post("/api/schedules/9/stop", headers=PIN).status_code == 404


def test_stop_route_returns_run_id_and_status_and_frees_the_schedule(client, app):
    create(client)
    app.state.scheduler_loop.active["r1"] = ActiveRun(
        run_id="r1", schedule_id=1, trigger="scheduled", machine_id=1, script=SCRIPT, args=[],
        timeout_s=0, retries=0, retry_delay_s=0, started_at="2026-03-01T12:00:00Z",
    )
    mock_endpoint(app).down = True
    resp = client.post("/api/schedules/1/stop", headers=PIN)
    assert resp.status_code == 200 and resp.json() == {"run_id": "r1", "status": "cancelled"}
    assert app.state.scheduler_loop.active == {}
    assert client.put("/api/schedules/1", json=BODY, headers=PIN).status_code == 200


def test_pin_errors_carry_both_the_detail_and_the_error_shape(limited_client):
    missing = limited_client.post("/api/schedules/1/run", headers=AUTH)
    assert missing.json() == {"detail": "invalid_pin", "error": "invalid_pin", "message": "wrong PIN"}
    for _ in range(2):
        limited_client.post("/api/schedules/1/run", headers=BAD_PIN)
    locked = limited_client.post("/api/schedules/1/run", headers=BAD_PIN)
    assert locked.status_code == 429
    assert locked.json()["detail"] == "rate_limited" and locked.json()["error"] == "rate_limited"


def test_check_script_route_exposes_resolved_and_error(client, app):
    endpoint = mock_endpoint(app)
    endpoint.add_script(SCRIPT, script_path=r"C:\services\ok.cmd")
    endpoint.rejections["bad.cmd"] = {"error": "outside_scripts_dir"}
    ok = client.post("/api/schedules/check-script", json={"machine_id": 1, "script": SCRIPT}, headers=PIN).json()
    assert ok == {"exists": True, "is_file": True, "resolved": r"C:\services\ok.cmd"}
    bad = client.post("/api/schedules/check-script", json={"machine_id": 1, "script": "bad.cmd"}, headers=PIN).json()
    assert bad["exists"] is False and bad["error"] == "outside_scripts_dir"


def test_relative_script_paths_pass_schedule_validation(client):
    assert create(client, script="jobs/nightly.cmd")["script"] == "jobs/nightly.cmd"
    assert create(client, script="run.sh")["script"] == "run.sh"


def test_runs_endpoint_returns_script_path_and_interpreter(client, app):
    create(client)
    app.state.run_log.append(1, {
        "run_id": "r1", "trigger": "scheduled", "started_at": "2026-03-01T12:00:00Z", "ended_at": "2026-03-01T12:00:01Z",
        "attempts": 1, "exit_code": 0, "outcome": "success", "reason": None, "stdout_tail": "", "stderr_tail": "",
        "script_path": r"C:\s\ok.cmd", "interpreter": "cmd.exe",
    })
    run = client.get("/api/schedules/1/runs", headers=AUTH).json()["runs"][0]
    assert run["script_path"] == r"C:\s\ok.cmd" and run["interpreter"] == "cmd.exe"
