from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from _alerts.dedup import AlertKey, OpenIncidentIndex
from _contract.schema import ObjectSeverities
from _metrics.pair_scan_runner import PairScanResult
from _metrics.readability import ReadStatus
from _ws.estate_payload import _build_alert_scope, pipeline_alerts_wire
from _alerts.engine import evaluate_machine_alerts


@dataclass
class _FakeServer:
    id: int
    platform: str
    name: str = "s"
    used_buses: list = field(default_factory=list)


@dataclass
class _FakeBus:
    id: int
    name: str = "bus"


@dataclass
class _FakeConfig:
    servers: list
    service_busses: list


def _attribution(emitted: dict) -> SimpleNamespace:
    return SimpleNamespace(emitted=emitted, blocked_by={})


def _settings() -> SimpleNamespace:
    return SimpleNamespace(backlog_max=100, lag_max=60, ping_seconds=10, ping_down_multiplier=3, ping_blocked_multiplier=2)


def _alerts_settings() -> SimpleNamespace:
    return SimpleNamespace(severity_channels={}, escalated_channels={})


def _pipeline_row(pipeline_id: int, source: int, target: int, *, pending_outbox: int = 0, min_severity=None) -> dict:
    return {
        "pipelineId": pipeline_id,
        "sourceSystem": source,
        "targetSystem": target,
        "pendingOutbox": pending_outbox,
        "sendLagSeconds": None,
        "applyLagSeconds": None,
        "minSeverity": min_severity,
        "_hostServerId": source,
    }


def test_a_real_db_object_down_produces_an_object_alert_with_configured_severity():
    server = _FakeServer(id=1, platform="mysql")
    config = _FakeConfig(servers=[server], service_busses=[])
    settings = _settings()
    db_object_id = "db:mysql:1:query"
    attribution = _attribution({db_object_id: ReadStatus.DOWN})
    pipelines_wire: list[dict] = []
    pair_scan = PairScanResult()

    scope = _build_alert_scope(config, settings, attribution, pipelines_wire, pair_scan)
    incidents = OpenIncidentIndex()
    result = evaluate_machine_alerts(
        scope, incidents,
        object_severities=ObjectSeverities(),
        previous_degraded={},
        lost_ticks={},
        row_data={},
    )

    raised = [e for e in result.events if e["type"] == "raised"]
    assert any(e["kind"] == "object" and e["object_id"] == db_object_id and e["severity"] == 2 for e in raised)

    wire = pipeline_alerts_wire(incidents, _alerts_settings(), {}, {})
    assert any(a["alertId"] == f"object:{db_object_id}" and a["severity"] == 2 for a in wire)


def test_a_degraded_pipeline_with_real_backlog_produces_a_pipeline_alert():
    source, target = _FakeServer(id=1, platform="mysql"), _FakeServer(id=2, platform="postgresql")
    config = _FakeConfig(servers=[source, target], service_busses=[])
    settings = _settings()
    attribution = _attribution({"db:mysql:1:query": ReadStatus.OK, "db:postgresql:2:query": ReadStatus.OK})
    pipelines_wire = [_pipeline_row(101, 1, 2, pending_outbox=150, min_severity=3)]
    pair_scan = PairScanResult()

    scope = _build_alert_scope(config, settings, attribution, pipelines_wire, pair_scan)
    incidents = OpenIncidentIndex()
    result = evaluate_machine_alerts(
        scope, incidents,
        object_severities=ObjectSeverities(),
        previous_degraded={},
        lost_ticks={},
        row_data={},
    )

    raised = [e for e in result.events if e["type"] == "raised"]
    assert any(e["kind"] == "pipeline" and e["pipeline_id"] == 101 and e["severity"] == 3 for e in raised)

    wire = pipeline_alerts_wire(incidents, _alerts_settings(), {}, {})
    assert any(a["alertId"] == "pipeline:101" and a["kind"] == "pipeline" for a in wire)


def test_dedup_does_not_emit_two_alerts_for_one_still_open_root_cause():
    server = _FakeServer(id=1, platform="mysql")
    config = _FakeConfig(servers=[server], service_busses=[])
    settings = _settings()
    db_object_id = "db:mysql:1:query"
    attribution = _attribution({db_object_id: ReadStatus.DOWN})
    pair_scan = PairScanResult()
    incidents = OpenIncidentIndex()

    raised_events_per_tick = []
    for _ in range(2):
        scope = _build_alert_scope(config, settings, attribution, [], pair_scan)
        result = evaluate_machine_alerts(
            scope, incidents,
            object_severities=ObjectSeverities(),
            previous_degraded={},
            lost_ticks={},
            row_data={},
        )
        raised_events_per_tick.append([e for e in result.events if e["type"] == "raised" and e["kind"] == "object"])

    assert len(raised_events_per_tick[0]) == 1
    assert len(raised_events_per_tick[1]) == 0
    assert len(pipeline_alerts_wire(incidents, _alerts_settings(), {}, {})) == 1


def test_object_recovering_to_ok_resolves_the_incident_not_a_second_raise():
    server = _FakeServer(id=1, platform="mysql")
    config = _FakeConfig(servers=[server], service_busses=[])
    settings = _settings()
    db_object_id = "db:mysql:1:query"
    pair_scan = PairScanResult()
    incidents = OpenIncidentIndex()

    scope_down = _build_alert_scope(config, settings, _attribution({db_object_id: ReadStatus.DOWN}), [], pair_scan)
    evaluate_machine_alerts(scope_down, incidents, object_severities=ObjectSeverities(), previous_degraded={}, lost_ticks={}, row_data={})
    assert incidents.is_open(AlertKey("object", db_object_id))

    scope_ok = _build_alert_scope(config, settings, _attribution({db_object_id: ReadStatus.OK}), [], pair_scan)
    result = evaluate_machine_alerts(scope_ok, incidents, object_severities=ObjectSeverities(), previous_degraded={}, lost_ticks={}, row_data={})

    resolved = [e for e in result.events if e["type"] == "resolved" and e["kind"] == "object"]
    assert any(e["object_id"] == db_object_id for e in resolved)
    assert len(pipeline_alerts_wire(incidents, _alerts_settings(), {}, {})) == 0


def test_standalone_service_object_alert_carries_the_real_error_and_no_machine():
    incidents = OpenIncidentIndex()
    incidents.claim(AlertKey("object", "db:mssql:9001:query"), {"object_id": "db:mssql:9001:query", "kind": "db", "severity": 2})
    error = "server 9001 (mssql) authentication rejected: Login failed for user 'admindev001'. (18456)"
    info = {9001: {"name": "MSSQL", "standalone": True, "error": error}}

    (alert,) = pipeline_alerts_wire(incidents, _alerts_settings(), {}, {}, None, info)

    assert alert["alertId"] == "object:db:mssql:9001:query"
    assert alert["server_id"] == 9001
    assert alert["machine_id"] is None
    assert alert["detail"] == error
    assert "18456" in alert["reason"] and alert["reason"].startswith("MSSQL (server 9001)")


def test_hosted_server_object_alert_has_no_machine_id_key_and_falls_back_without_error():
    incidents = OpenIncidentIndex()
    incidents.claim(AlertKey("object", "db:mysql:1:query"), {"object_id": "db:mysql:1:query", "kind": "db", "severity": 2})
    info = {1: {"name": "MySQL", "standalone": False, "error": None}}

    (alert,) = pipeline_alerts_wire(incidents, _alerts_settings(), {}, {}, None, info)

    assert "machine_id" not in alert and "detail" not in alert
    assert alert["reason"] == "MySQL (server 1) is not ok"
