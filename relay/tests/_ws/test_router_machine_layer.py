from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"


@pytest.fixture
def client(write_config, config_dict) -> TestClient:
    path = write_config(config_dict)
    app = create_app(path)
    return TestClient(app)


def _hello(ws):
    ws.send_json({"type": "hello", "device": "d1", "clientId": "c1"})
    return ws.receive_json()


def test_ws_machine_serves_real_snapshot_once_registry_marks_it_online(client: TestClient):
    state = client.app.state.machine_registry.state_for(1)
    state.status = "online"
    state.active_services = [{"node_id": "n1", "node_type": "Database", "status": "ok"}]

    with client.websocket_connect(f"/ws/machine/1?{AUTH_QS}") as ws:
        snapshot = _hello(ws)
        assert snapshot["type"] == "snapshot"
        assert snapshot["machine_id"] == 1
        assert snapshot["active_services"] == [{"node_id": "n1", "node_type": "Database", "status": "ok"}]


def test_ws_thin_snapshot_carries_real_config_derived_machine_inventory(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        snapshot = _hello(ws)
        assert snapshot["type"] == "snapshot"
        assert "servers" not in snapshot
        assert "platforms" not in snapshot
        assert snapshot["machines"] == [
            {
                "machine_id": 1,
                "machine_label_id": "1",
                "machine_name": "test-machine-1",
                "machine_type": "LOCAL",
                "machine_os": "Windows",
                "machine_ip": "127.0.0.1",
                "status": "pending",
                "last_probe_ts": None,
                "last_active_ts": None,
                "ping": None,
                "node_count": 0,
                "platforms": [],
                "nodes": [],
                "machine_scheduler": False,
                "scheduler_state": "off",
                "scheduler_defaults": None,
                "scheduler_reason": None,
            }
        ]


def test_ws_combined_snapshot_carries_open_machine_down_alert(client: TestClient):
    from _alerts.dedup import AlertKey

    client.app.state.machine_alert_incidents.claim(AlertKey("machine", "1"), {"machine_id": 1, "severity": 2})

    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        snapshot = _hello(ws)
        alert = next(a for a in snapshot["activeAlerts"] if a["alertId"] == "machine:1")
        assert alert["kind"] == "machine"
        assert alert["machine_id"] == 1
        assert alert["severity"] == 2


def test_ws_machine_offline_still_closes_4410_even_when_registered_previously_online_then_flipped(client: TestClient):
    with client.websocket_connect(f"/ws/machine/1?{AUTH_QS}") as ws:
        message = ws.receive()
        assert message["code"] == 4410


def test_supervisor_kill_closes_a_live_ws_machine_connection(client: TestClient):
    async def _scenario():
        registry = client.app.state.machine_registry
        state = registry.state_for(1)
        state.status = "online"
        closed = asyncio.Event()

        async def _close_cb():
            closed.set()

        conn_id = state.register_connection(_close_cb)
        client.app.state.machine_supervisor.kill(1)
        await asyncio.wait_for(closed.wait(), timeout=1)
        state.unregister_connection(conn_id)

    asyncio.run(_scenario())


def test_root_ws_carries_config_issues_including_orphan_pipeline_errors(client: TestClient):
    from _contract.schema import RelayConfig
    from config_issues import compute_config_issues, orphan_pipeline_issues

    config = client.app.state.config_store.get()
    rows = [{"pipelineId": 2, "sourceSystem": "1001", "targetSystem": "2001", "_hostServerId": 1001}]
    issues = compute_config_issues(config) + orphan_pipeline_issues(config, rows)
    client.app.state.estate_loop.latest = {"configIssues": [i.model_dump(exclude_none=True) for i in issues]}
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        snapshot = _hello(ws)
    assert "configIssues" in snapshot
    assert {i["code"] for i in snapshot["configIssues"]} == {"pipeline_references_unknown_server"}
    assert {i["severity"] for i in snapshot["configIssues"]} == {"error"}
    assert any("server 2001" in i["message"] for i in snapshot["configIssues"])


def test_root_ws_config_issues_default_to_the_static_list(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        snapshot = _hello(ws)
    assert snapshot["configIssues"] == []


def test_root_ws_carries_service_pairs_and_pipelines_and_defaults_to_empty(client: TestClient):
    pair = {"id": "9001-9002", "from": "9001", "to": "9002", "packets": 0}
    pipeline = {"pipelineId": 7, "sourceSystem": "9001", "targetSystem": "9002", "cross_machine": None}
    client.app.state.estate_loop.latest = {"servicePairs": [pair], "servicePipelines": [pipeline]}
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        snapshot = _hello(ws)
    assert snapshot["pairs"] == [pair]
    assert snapshot["pipelines"] == [pipeline]

    client.app.state.estate_loop.latest = {}
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        snapshot = _hello(ws)
    assert snapshot["pairs"] == [] and snapshot["pipelines"] == []
