from __future__ import annotations

import asyncio
from types import SimpleNamespace

from _alerts.dedup import AlertKey, OpenIncidentIndex
from _alerts.summary import SummaryAggregates, SummaryResult
from _lib.config import ConfigStore
from _ws.estate_loop import EstateSnapshotLoop
from _ws.estate_payload import (
    _pipeline_health,
    _wire_summary_level,
    build_estate_sections,
    machine_alerts_wire,
    pipeline_alerts_wire,
    static_snapshot_sections,
)


def _result(level: str, branch: str) -> SummaryResult:
    return SummaryResult(
        level=level, branch=branch, note="x",
        aggregates=SummaryAggregates(worst_backlog=None, lost_summary={"count": 0, "pipeline_ids": []}, messages_held_by={}, biggest_pile_outside_buses=None),
    )


def test_wire_level_down_db_objects_is_problem_not_outage():
    assert _wire_summary_level(_result("red", "down_db_objects")) == "problem"


def test_wire_level_pure_service_bus_down_is_outage():
    assert _wire_summary_level(_result("grey", "service_bus_down")) == "outage"


def test_wire_level_service_bus_down_magnitude_override_is_problem_not_outage():
    assert _wire_summary_level(_result("red", "service_bus_down")) == "problem"


def test_wire_level_all_relays_stopped_is_offline():
    assert _wire_summary_level(_result("grey", "all_relays_stopped")) == "offline"


def test_wire_level_red_yellow_green_translate_uniformly():
    assert _wire_summary_level(_result("red", "red_backlog")) == "problem"
    assert _wire_summary_level(_result("yellow", "yellow_backlog")) == "attention"
    assert _wire_summary_level(_result("green", "good")) == "good"


def _lab_config_dict(config_dict):
    config_dict["service_busses"] = [
        {"id": 1, "name": "bus-1", "health_url": "http://localhost:5300/health", "amqp_port": 5672, "topic": "sync"},
    ]
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "node-mysql-1001", "node_name": "MySQL", "node_type": "Database", "node_platform": "mysql",
            "server_id": 1001,
            "node_config": {
                "host": "127.0.0.1", "port": 3306, "user": "root", "password": "fake-mysql-pw",
                "database": "mysql_test_for_service_bus", "used_buses": [1],
            },
        },
        {
            "node_id": "node-postgres-1002", "node_name": "PostgreSQL", "node_type": "Database",
            "node_platform": "postgresql", "server_id": 1002,
            "node_config": {
                "host": "::1", "port": 5432, "user": "postgres", "password": "fake-postgres-pw",
                "database": "pg_test_for_service_bus", "used_buses": [1],
            },
        },
    ]
    return config_dict


class _FakeAppState:
    def __init__(self, config_store: ConfigStore):
        self.config_store = config_store
        self.parity_loop = None
        self.poller = None
        self.down_confirm = None
        from _metrics.down_confirm import DownConfirm
        from _metrics.poll_stats import PollStats
        from _metrics.poller import Poller

        settings = config_store.get().settings
        self.poll_stats = PollStats(window_samples=settings.poll_stats_window_samples)
        self.down_confirm = DownConfirm.from_settings(settings)
        self.poller = Poller.from_settings(settings, self.down_confirm, self.poll_stats)

        from _alerts.dedup import OpenIncidentIndex

        self.machine_alert_incidents = OpenIncidentIndex()


def test_static_snapshot_sections_needs_no_live_data(write_config, config_dict):
    path = write_config(_lab_config_dict(config_dict))
    config_store = ConfigStore(path)
    config = config_store.load_startup()

    sections = static_snapshot_sections(config)
    assert {s["id"] for s in sections["servers"]} == {"1001", "1002"}
    assert all(isinstance(s["id"], str) for s in sections["servers"])
    assert sections["platforms"] == sections["servers"]
    assert sections["pipelines"] == []
    assert sections["thresholds"]["yellow"] < sections["thresholds"]["red"]
    assert sections["thresholds"]["degraded"] == 100


def test_build_estate_sections_against_the_real_lab(write_config, config_dict):
    path = write_config(_lab_config_dict(config_dict))
    config_store = ConfigStore(path)
    config_store.load_startup()
    app_state = _FakeAppState(config_store)

    sections = asyncio.run(build_estate_sections(app_state))

    assert {s["id"] for s in sections["servers"]} == {"1001", "1002"}
    assert isinstance(sections["pipelines"], list)
    assert isinstance(sections["pairs"], list)
    assert isinstance(sections["buses"], list) and len(sections["buses"]) == 1
    assert sections["buses"][0]["id"] == "1"
    node_ids = {n["id"] for n in sections["topology"]["nodes"] if n["kind"] == "platform"}
    assert node_ids == {"1001", "1002"}
    assert sections["summary"]["level"] in {"good", "attention", "problem", "offline", "outage"}
    assert "yellow" in sections["thresholds"] and "degraded" in sections["thresholds"] and "red" in sections["thresholds"]
    assert sections["thresholds"]["degraded"] == 100

    db_objects = [o for o in sections["objects"] if o["id"].startswith("db:")]
    assert db_objects and all(o["kind"] == "db_query" for o in db_objects)

    for pipeline in sections["pipelines"]:
        server_part, _, number = pipeline["pipelineId"].partition(":")
        assert server_part in {"1001", "1002"} and number.isdigit() and pipeline["pipelineNo"] == int(number)
        assert pipeline["health"] in {"Healthy", "Degraded", "Down"}
        assert isinstance(pipeline["pendingOutbox"], int)
        assert isinstance(pipeline["minSeverity"], int)

    for pair in sections["pairs"]:
        assert pair["from"] in {"1001", "1002"}
        assert pair["to"] in {"1001", "1002"}
        assert pair["id"] == f"{pair['from']}->{pair['to']}"
        assert ":" not in pair["id"]
        assert isinstance(pair["senderOk"], bool)
        assert isinstance(pair["receiverOk"], bool)
        assert pair["queued"] is None or isinstance(pair["queued"], int)
        if pair["queued"] is not None:
            assert pair["queued"] >= 0
            assert pair["queuedUnknown"] is None
        assert isinstance(pair["lost"], int) and pair["lost"] >= 0


def _lab_config_with_unreachable_mssql(config_dict):
    d = _lab_config_dict(config_dict)
    d["settings"]["tcp_timeout_ms"] = 2500
    d["settings"]["query_timeout_ms_default"] = 4000
    d["settings"]["per_server_timeout_ms"] = 4000
    d["machines"][0]["nodes"].append(
        {
            "node_id": "node-mssql-1003", "node_name": "MSSQL (unreachable)", "node_type": "Database",
            "node_platform": "mssql", "server_id": 1003,
            "node_config": {
                "host": "192.0.2.1", "port": 1433, "user": "sa", "password": "unreachable123",
                "database": "sync", "used_buses": [1],
            },
        }
    )
    return d


def test_unreachable_server_surfaces_not_ok_on_the_wire_and_summary_is_problem(write_config, config_dict):
    path = write_config(_lab_config_with_unreachable_mssql(config_dict))
    config_store = ConfigStore(path)
    config_store.load_startup()
    app_state = _FakeAppState(config_store)

    sections = asyncio.run(build_estate_sections(app_state))

    db_object = next(o for o in sections["objects"] if o["id"] == "db:mssql:1003:query")
    assert db_object["status"] != "ok"
    assert db_object["status"] == "down"
    assert db_object["kind"] == "db_query"

    node = next(n for n in sections["topology"]["nodes"] if n["id"] == "1003")
    assert node["dbOk"] is False
    assert node["senderOk"] is False
    assert node["receiverOk"] is False

    for pair in sections["pairs"]:
        if pair["from"] == "1003":
            assert pair["senderOk"] is False
        if pair["to"] == "1003":
            assert pair["receiverOk"] is False

    assert sections["summary"]["level"] == "problem"


def test_estate_loop_tick_caches_and_pushes_to_registered_sessions(write_config, config_dict):
    path = write_config(_lab_config_dict(config_dict))
    config_store = ConfigStore(path)
    config_store.load_startup()
    app_state = _FakeAppState(config_store)
    loop = EstateSnapshotLoop(config_store, app_state)

    sent: list[dict] = []

    class _FakeSession:
        hello_received = True

        async def send_snapshot(self):
            sent.append(loop.latest)

    loop.register(_FakeSession())
    asyncio.run(loop.tick())

    assert loop.latest is not None
    assert len(sent) == 1


def test_pipeline_health_below_backlog_max_is_not_degraded():
    row = {"pendingOutbox": 43, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    assert _pipeline_health(row, db_ok=True, backlog_max=100) == ("Healthy", None)


def test_pipeline_health_above_backlog_max_is_degraded_backlog():
    row = {"pendingOutbox": 143, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    assert _pipeline_health(row, db_ok=True, backlog_max=100) == ("Degraded", "backlog")


def test_pipeline_health_exactly_at_backlog_max_is_not_degraded():
    row = {"pendingOutbox": 100, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    assert _pipeline_health(row, db_ok=True, backlog_max=100) == ("Healthy", None)


def test_pipeline_health_failed_outbox_degrades_regardless_of_threshold():
    row = {"pendingOutbox": 1, "pendingInbox": 0, "failedOutbox": 1, "unresolvedInbox": 0}
    assert _pipeline_health(row, db_ok=True, backlog_max=100) == ("Degraded", "failed_rows")


def test_pipeline_health_db_down_overrides_everything():
    row = {"pendingOutbox": 0, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    assert _pipeline_health(row, db_ok=False, backlog_max=100) == ("Down", None)


def test_pipeline_health_stale_heartbeat_down_is_never_healthy():
    row = {"pendingOutbox": 0, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    health, degraded_by = _pipeline_health(row, db_ok=True, backlog_max=100, heartbeat_class="down")
    assert health != "Healthy"
    assert health == "Down"
    assert degraded_by is None


def test_pipeline_health_blocked_by_relay_heartbeat_is_degraded_not_healthy():
    row = {"pendingOutbox": 0, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    health, degraded_by = _pipeline_health(row, db_ok=True, backlog_max=100, heartbeat_class="blocked_by_relay")
    assert health == "Degraded"
    assert degraded_by == "heartbeat_stale"


def test_pipeline_health_ok_heartbeat_class_falls_through_to_existing_rules():
    row = {"pendingOutbox": 0, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    assert _pipeline_health(row, db_ok=True, backlog_max=100, heartbeat_class="ok") == ("Healthy", None)


def test_pipeline_health_db_down_still_wins_over_a_stale_heartbeat():
    row = {"pendingOutbox": 0, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    assert _pipeline_health(row, db_ok=False, backlog_max=100, heartbeat_class="down") == ("Down", None)


def _alerts_settings() -> SimpleNamespace:
    return SimpleNamespace(
        severity_channels={"3": ["email"], "1": ["sms", "wa_message"]},
        escalated_channels={},
    )


def test_pipeline_alerts_wire_uses_real_queued_and_lost_counts():
    incidents = OpenIncidentIndex()
    incidents.claim(AlertKey("pipeline", "101"), {"pipeline_id": 101, "severity": 3})
    incidents.claim(AlertKey("lost", "202"), {"pipeline_id": 202, "severity": 1})

    out = pipeline_alerts_wire(
        incidents,
        _alerts_settings(),
        pipeline_queue_counts={101: 143, 202: 0},
        lost_by_pipeline={101: 0, 202: 40},
    )
    by_alert = {e["alertId"]: e for e in out}

    assert by_alert["pipeline:101"]["queuedCount"] == 143
    assert "lostCount" not in by_alert["pipeline:101"]
    assert by_alert["lost:202"]["lostCount"] == 40
    assert "queuedCount" not in by_alert["lost:202"]
    assert by_alert["pipeline:101"]["channels"] == ["email"]


def test_pipeline_alerts_wire_object_kind_has_no_queued_or_lost_fields():
    incidents = OpenIncidentIndex()
    incidents.claim(AlertKey("object", "db:mssql:1003:query"), {"object_id": "db:mssql:1003:query", "kind": "db", "severity": 2})

    out = pipeline_alerts_wire(incidents, _alerts_settings(), pipeline_queue_counts={}, lost_by_pipeline={})
    assert "queuedCount" not in out[0]
    assert "lostCount" not in out[0]


def test_pipeline_health_wire_values_are_within_the_dashboard_recognised_set():
    DASHBOARD_HEALTH_STATES = {"Disabled", "Blocked", "Unknown", "Down", "Degraded", "Healthy"}
    row_ok = {"pendingOutbox": 0, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    row_degraded = {"pendingOutbox": 200, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    assert _pipeline_health(row_ok, db_ok=True, backlog_max=100)[0] in DASHBOARD_HEALTH_STATES
    assert _pipeline_health(row_degraded, db_ok=True, backlog_max=100)[0] in DASHBOARD_HEALTH_STATES
    assert _pipeline_health(row_ok, db_ok=False, backlog_max=100)[0] in DASHBOARD_HEALTH_STATES
    assert _pipeline_health(row_ok, db_ok=True, backlog_max=100) == ("Healthy", None)


def test_pipeline_alerts_wire_relay_heartbeat_names_the_pipeline_not_the_raw_id():
    incidents = OpenIncidentIndex()
    incidents.claim(AlertKey("relay_heartbeat", "104"), {"pipeline_id": 104, "heartbeat_class": "down"})
    lookup = {104: {"pipelineId": 104, "displayName": "MySQL 49 → PostgreSQL", "name": "mysql49-out", "heartbeatAgeSeconds": 187200}}

    out = pipeline_alerts_wire(incidents, _alerts_settings(), pipeline_queue_counts={104: 43}, lost_by_pipeline={}, pipeline_lookup=lookup)

    assert len(out) == 1
    entry = out[0]
    assert "relay_heartbeat:104" not in entry["reason"]
    assert "MySQL 49" in entry["reason"] and "PostgreSQL" in entry["reason"]
    assert "queuedCount" not in entry
    assert "lostCount" not in entry
    assert "detail" in entry and "52h" in entry["detail"]


def test_pipeline_alerts_wire_relay_heartbeat_falls_back_without_a_lookup():
    incidents = OpenIncidentIndex()
    incidents.claim(AlertKey("relay_heartbeat", "206"), {"pipeline_id": 206, "heartbeat_class": "down"})

    out = pipeline_alerts_wire(incidents, _alerts_settings(), pipeline_queue_counts={}, lost_by_pipeline={})

    assert out[0]["reason"] != "relay_heartbeat:206"
    assert "206" in out[0]["reason"]
    assert "queuedCount" not in out[0]
    assert "detail" not in out[0]


def test_machine_alerts_wire_uses_real_channels_never_a_placeholder():
    incidents = OpenIncidentIndex()
    incidents.claim(AlertKey("machine", "5"), {"machine_id": 5, "severity": 1})

    out = machine_alerts_wire(incidents, _alerts_settings())
    assert out[0]["channels"] == ["sms", "wa_message"]
    assert "queuedCount" not in out[0]
    assert "lostCount" not in out[0]


def _scope_settings(**overrides) -> SimpleNamespace:
    base = dict(backlog_max=100, lag_max=600, ping_seconds=10, ping_down_multiplier=3, ping_blocked_multiplier=2)
    base.update(overrides)
    return SimpleNamespace(**base)


def _empty_pair_scan() -> SimpleNamespace:
    return SimpleNamespace(lost_by_pipeline={}, queued_countable_by_pipeline={}, matched_recently_by_pipeline={})


def test_build_alert_scope_classifies_a_stale_heartbeat_as_down():
    from _ws.estate_payload import _build_alert_scope

    config = SimpleNamespace(servers=[], service_busses=[])
    attribution = SimpleNamespace(emitted={})
    pipelines_wire = [{"pipelineId": 101, "pendingOutbox": 0, "heartbeatAgeSeconds": 185504, "minSeverity": 3}]

    scope = _build_alert_scope(config, _scope_settings(), attribution, pipelines_wire, _empty_pair_scan())

    assert scope.pipelines[101].heartbeat_class == "down"


def test_build_alert_scope_classifies_a_fresh_heartbeat_as_ok():
    from _ws.estate_payload import _build_alert_scope

    config = SimpleNamespace(servers=[], service_busses=[])
    attribution = SimpleNamespace(emitted={})
    pipelines_wire = [{"pipelineId": 101, "pendingOutbox": 0, "heartbeatAgeSeconds": 5, "minSeverity": 3}]

    scope = _build_alert_scope(config, _scope_settings(), attribution, pipelines_wire, _empty_pair_scan())

    assert scope.pipelines[101].heartbeat_class == "ok"


def test_build_alert_scope_classifies_a_missing_heartbeat_as_ok_not_down():
    from _ws.estate_payload import _build_alert_scope

    config = SimpleNamespace(servers=[], service_busses=[])
    attribution = SimpleNamespace(emitted={})
    pipelines_wire = [{"pipelineId": 101, "pendingOutbox": 0, "heartbeatAgeSeconds": None, "minSeverity": 3}]

    scope = _build_alert_scope(config, _scope_settings(), attribution, pipelines_wire, _empty_pair_scan())

    assert scope.pipelines[101].heartbeat_class == "ok"


def test_build_pairs_per_sender_gate_nulls_only_the_unscanned_senders_own_pairs():
    from _ws.estate_payload import _build_pairs

    mysql = SimpleNamespace(id=1001, name="MySQL", platform="mysql", used_buses=[1])
    mssql = SimpleNamespace(id=1003, name="MSSQL", platform="mssql", used_buses=[1])
    config = SimpleNamespace(servers=[mysql, mssql], service_busses=[])
    settings = SimpleNamespace(pending_yellow_threshold=50)
    attribution = SimpleNamespace(emitted={})
    pipelines_wire = [
        {"sourceSystem": 1001, "targetSystem": 1003, "pendingOutbox": 0, "failedOutbox": 0, "unresolvedInbox": 0, "_hostServerId": 1001},
        {"sourceSystem": 1003, "targetSystem": 1001, "pendingOutbox": 0, "failedOutbox": 0, "unresolvedInbox": 0, "_hostServerId": 1003},
    ]
    pair_scan = SimpleNamespace(
        any_scan_succeeded=True,
        scan_ok_by_server={1001: True, 1003: False},
        queued_by_pair={(1001, 1003): 7, (1003, 1001): 0},
        lost_by_pair={},
        packets_by_pair={},
    )

    pairs = asyncio.run(_build_pairs(config, settings, pipelines_wire, attribution, pair_scan))
    by_id = {p["id"]: p for p in pairs}

    assert by_id["1001->1003"]["queued"] == 7
    assert by_id["1001->1003"]["queuedUnknown"] is None

    assert by_id["1003->1001"]["queued"] is None
    assert by_id["1003->1001"]["queuedUnknown"] is True


def _summary_settings() -> SimpleNamespace:
    return SimpleNamespace(
        pending_yellow_threshold=50, pending_red_threshold=500, material_backlog_factor=2,
    )


def test_summary_cannot_read_good_while_a_pipeline_has_a_stale_heartbeat():
    from _ws.estate_payload import _build_summary
    from _metrics.pair_scan_runner import PairScanResult

    config = SimpleNamespace(servers=[], service_busses=[])
    attribution = SimpleNamespace(emitted={})
    pipelines_wire = [
        {
            "pipelineId": 101, "sourceSystem": 1001, "targetSystem": 1002,
            "health": "Down", "degradedBy": None, "pendingOutbox": 0,
        }
    ]
    result = _build_summary(config, _summary_settings(), attribution, pipelines_wire, pairs_wire=[], pair_scan=PairScanResult())

    assert result["level"] != "good"
    assert "healthy" not in result["headline"].lower()


def test_summary_is_good_when_every_pipeline_is_genuinely_healthy():
    from _ws.estate_payload import _build_summary
    from _metrics.pair_scan_runner import PairScanResult

    config = SimpleNamespace(servers=[], service_busses=[])
    attribution = SimpleNamespace(emitted={})
    pipelines_wire = [
        {
            "pipelineId": 101, "sourceSystem": 1001, "targetSystem": 1002,
            "health": "Healthy", "degradedBy": None, "pendingOutbox": 0,
        }
    ]
    result = _build_summary(config, _summary_settings(), attribution, pipelines_wire, pairs_wire=[], pair_scan=PairScanResult())

    assert result["level"] == "good"


def _half_line_pairs(rows_by_host: dict):
    from _ws.estate_payload import _build_pairs
    from _ws.pipeline_direction import normalize_pipeline_row

    s49 = SimpleNamespace(id=1001, name="49-Server", platform="mysql", used_buses=[])
    tabish = SimpleNamespace(id=4001, name="Tabish-pc", platform="mysql", used_buses=[])
    config = SimpleNamespace(servers=[s49, tabish], service_busses=[])
    settings = SimpleNamespace(pending_yellow_threshold=50)
    attribution = SimpleNamespace(emitted={})
    wire = []
    for host, rows in rows_by_host.items():
        for r in rows:
            base = {"pendingOutbox": 0, "failedOutbox": 0, "unresolvedInbox": 0, "_hostServerId": host}
            wire.append(normalize_pipeline_row({**base, **r}, host))
    pair_scan = SimpleNamespace(
        any_scan_succeeded=True, scan_ok_by_server={1001: True, 4001: True},
        queued_by_pair={}, lost_by_pair={}, packets_by_pair={},
    )
    return asyncio.run(_build_pairs(config, settings, wire, attribution, pair_scan))


def test_flipped_columns_on_the_two_halves_produce_one_pair():
    pairs = _half_line_pairs({
        1001: [{"pipelineId": 1, "sourceSystem": 4001, "targetSystem": 1001, "direction": "incoming"}],
        4001: [{"pipelineId": 2, "sourceSystem": 1001, "targetSystem": 4001, "direction": "outgoing"}],
    })
    assert [p["id"] for p in pairs] == ["4001->1001"]
    assert pairs[0]["missingEnd"] is None and pairs[0]["missingEndReason"] is None


def test_same_column_order_on_both_halves_produce_one_pair():
    pairs = _half_line_pairs({
        4001: [{"pipelineId": 2, "sourceSystem": 4001, "targetSystem": 1001, "direction": "outgoing"}],
        1001: [{"pipelineId": 1, "sourceSystem": 4001, "targetSystem": 1001, "direction": "incoming"}],
    })
    assert [p["id"] for p in pairs] == ["4001->1001"]
    assert pairs[0]["missingEnd"] is None


def test_sender_only_half_marks_receiver_missing():
    pairs = _half_line_pairs({
        4001: [{"pipelineId": 2, "sourceSystem": 1001, "targetSystem": 4001, "direction": "outgoing", "pendingOutbox": 3}],
    })
    assert len(pairs) == 1
    assert pairs[0]["id"] == "4001->1001"
    assert pairs[0]["missingEnd"] == "receiver" and pairs[0]["missingEndReason"] == "unconfigured"
    assert pairs[0]["pendingOutbox"] == 3
    assert pairs[0]["bus"] is None


def test_receiver_only_half_marks_sender_missing():
    pairs = _half_line_pairs({
        1001: [{"pipelineId": 1, "sourceSystem": 4001, "targetSystem": 1001, "direction": "incoming"}],
    })
    assert len(pairs) == 1
    assert pairs[0]["id"] == "4001->1001"
    assert pairs[0]["missingEnd"] == "sender" and pairs[0]["missingEndReason"] == "unconfigured"
    assert pairs[0]["pendingOutbox"] == 0


def test_normalize_falls_back_to_raw_columns_when_host_is_in_neither():
    from _ws.pipeline_direction import normalize_pipeline_row

    row = {"sourceSystem": 7, "targetSystem": 8, "direction": "outgoing"}
    out = normalize_pipeline_row(row, 1001)
    assert (out["sourceSystem"], out["targetSystem"]) == (7, 8)


def _pair_fixture(sender_age, receiver_age, *, with_receiver=True):
    mysql = SimpleNamespace(id=1001, name="MySQL", platform="mysql", used_buses=[])
    pg = SimpleNamespace(id=1002, name="PG", platform="postgres", used_buses=[])
    from _metrics.attribution import ReadStatus

    config = SimpleNamespace(servers=[mysql, pg], service_busses=[])
    settings = SimpleNamespace(
        pending_yellow_threshold=50, ping_seconds=10, ping_down_multiplier=3, ping_blocked_multiplier=2
    )
    attribution = SimpleNamespace(emitted={_db_id: ReadStatus.OK for _db_id in ("db:mysql:1001", "db:postgres:1002")})
    base = {"sourceSystem": 1001, "targetSystem": 1002, "pendingOutbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    rows = [{**base, "_hostServerId": 1001, "heartbeatAgeSeconds": sender_age}]
    if with_receiver:
        rows.append({**base, "_hostServerId": 1002, "heartbeatAgeSeconds": receiver_age})
    pair_scan = SimpleNamespace(scan_ok_by_server={1001: True}, queued_by_pair={}, lost_by_pair={}, packets_by_pair={})
    return config, settings, rows, attribution, pair_scan


def _run_pair(*args, **kw):
    from _ws.estate_payload import _build_pairs

    config, settings, rows, attribution, pair_scan = _pair_fixture(*args, **kw)
    return asyncio.run(_build_pairs(config, settings, rows, attribution, pair_scan))[0]


def test_pair_recent_pings_on_both_halves_is_green(monkeypatch):
    _patch_db_ids(monkeypatch)
    pair = _run_pair(3, 4)
    assert pair["senderOk"] is True and pair["receiverOk"] is True
    assert pair["status"] == "green"


def test_pair_null_ping_sender_half_is_not_ok_and_grey(monkeypatch):
    _patch_db_ids(monkeypatch)
    pair = _run_pair(None, 4)
    assert pair["senderOk"] is False and pair["receiverOk"] is True
    assert pair["status"] == "grey"


def test_pair_null_ping_receiver_half_is_not_ok_and_grey(monkeypatch):
    _patch_db_ids(monkeypatch)
    pair = _run_pair(3, None)
    assert pair["senderOk"] is True and pair["receiverOk"] is False
    assert pair["status"] == "grey"


def test_pair_stale_ping_half_is_grey(monkeypatch):
    _patch_db_ids(monkeypatch)
    pair = _run_pair(185504, 4)
    assert pair["senderOk"] is False and pair["status"] == "grey"


def test_pair_missing_receiver_half_keeps_half_line_contract(monkeypatch):
    _patch_db_ids(monkeypatch)
    pair = _run_pair(3, None, with_receiver=False)
    assert pair["missingEnd"] == "receiver" and pair["missingEndReason"] == "unconfigured"
    assert pair["senderOk"] is True


def test_pipeline_health_never_pinged_is_not_healthy():
    row = {"pendingOutbox": 0, "pendingInbox": 0, "failedOutbox": 0, "unresolvedInbox": 0}
    assert _pipeline_health(row, db_ok=True, backlog_max=100, never_pinged=True) == ("Down", None)
    assert _pipeline_health(row, db_ok=True, backlog_max=100, never_pinged=False) == ("Healthy", None)


def _patch_db_ids(monkeypatch):
    import _ws.estate_payload as ep

    monkeypatch.setattr(ep, "_db_object_id", lambda s: f"db:{s.platform}:{s.id}")


def test_foreign_rows_are_reported_but_never_drawn_counted_or_alerted(write_config, config_dict, monkeypatch):
    from _ws import estate_payload

    config_dict["machines"][0]["nodes"] = [
        {"node_id": f"n{sid}", "node_name": f"srv-{sid}", "node_type": "Database", "node_platform": "mysql", "server_id": sid,
         "node_config": {"host": "127.0.0.1", "port": 1, "user": "u", "password": "p", "database": f"d{sid}", "used_buses": []}}
        for sid in (4001, 4002, 1001)
    ]

    def row(pid, source, target, host, enabled=1):
        return {
            "pipelineId": pid, "name": f"p{pid}", "displayName": f"p{pid}", "sourceSystem": str(source), "targetSystem": str(target),
            "direction": "outgoing", "health": "Healthy", "degradedBy": None, "pendingOutbox": 0, "failedOutbox": 0,
            "pendingInbox": 0, "unresolvedInbox": 0, "failedInbox": 0, "sendLagSeconds": None, "applyLagSeconds": None,
            "heartbeatAgeSeconds": 1, "minSeverity": None, "_hostServerId": host, "_isEnabled": enabled,
        }

    rows = [row(1, 4001, 1001, 4001), row(1, 4001, 1001, 4002), row(2, 4001, 2001, 4002), row(3, 4001, 1001, 4002, enabled=0)]

    async def fake_build(config, settings, attribution):
        return rows, {}

    monkeypatch.setattr(estate_payload, "_build_pipelines_and_triggers", fake_build)
    config_store = ConfigStore(write_config(config_dict))
    config_store.load_startup()
    app_state = _FakeAppState(config_store)

    sections = asyncio.run(build_estate_sections(app_state))

    assert [(p["pipelineId"], p["sourceSystem"]) for p in sections["pipelines"]] == [(1, "4001"), (3, "4001")]
    foreign = [i for i in sections["configIssues"] if i["code"] == "pipeline_names_other_server"]
    assert [(i["severity"], i["where"]["serverId"], i["where"]["pipelineIds"]) for i in foreign] == [("error", "4002", [1]), ("error", "4002", [2])]
    assert len([p for p in sections["pairs"] if p["from"] == "4001" and p["to"] == "1001"]) <= 1
    assert all(a.get("kind") != "pipeline" or "2" not in a["affects"] for a in sections["activeAlerts"])


def test_pipeline_ids_are_server_qualified_so_two_servers_with_pipelines_1_to_4_stay_distinct(write_config, config_dict, monkeypatch):
    from _ws import estate_payload

    config_dict["machines"][0]["nodes"] = [
        {"node_id": f"n{sid}", "node_name": f"srv-{sid}", "node_type": "Database", "node_platform": "mysql", "server_id": sid,
         "node_config": {"host": "127.0.0.1", "port": 1, "user": "u", "password": "p", "database": f"d{sid}", "used_buses": []}}
        for sid in (4001, 4002)
    ]

    def row(pid, host):
        return {
            "pipelineId": pid, "name": f"p{pid}", "sourceSystem": str(host), "targetSystem": str(8001 + pid),
            "direction": "outgoing", "pendingOutbox": 0, "failedOutbox": 0, "pendingInbox": 0, "unresolvedInbox": 0,
            "sendLagSeconds": None, "applyLagSeconds": None, "heartbeatAgeSeconds": 1,
        }

    async def fake_fetch(server, settings):
        return [row(pid, server.id) for pid in (1, 2, 3, 4)]

    async def fake_triggers(server, settings):
        return []

    monkeypatch.setattr(estate_payload, "_fetch_pipelines_for", fake_fetch)
    monkeypatch.setattr(estate_payload, "_fetch_triggers_for", fake_triggers)
    config_store = ConfigStore(write_config(config_dict))
    config_store.load_startup()

    sections = asyncio.run(build_estate_sections(_FakeAppState(config_store)))

    ids = [p["pipelineId"] for p in sections["pipelines"]]
    assert sorted(ids) == sorted(f"{host}:{pid}" for host in (4001, 4002) for pid in (1, 2, 3, 4))
    assert len(set(ids)) == 8
    assert sorted(p["pipelineNo"] for p in sections["pipelines"]) == [1, 1, 2, 2, 3, 3, 4, 4]
    nodes = {n["id"]: n for n in sections["topology"]["nodes"] if n["kind"] == "platform"}
    assert nodes["4001"]["pipelines"] == [f"4001:{pid}" for pid in (1, 2, 3, 4)]
    assert nodes["4002"]["pipelines"] == [f"4002:{pid}" for pid in (1, 2, 3, 4)]
    assert "4001:1" not in nodes["4002"]["pipelines"]


def test_alerts_for_the_same_pipeline_number_on_two_servers_stay_separate():
    from _alerts.dedup import AlertKey, OpenIncidentIndex
    from _contract.schema import AlertsSettings
    from _ws import estate_payload

    incidents = OpenIncidentIndex()
    for server in (4001, 4002):
        incidents.claim(AlertKey("pipeline", f"{server}:4"), {"pipeline_id": f"{server}:4", "severity": 2})
    lookup = {f"{s}:4": {"pipelineId": f"{s}:4", "pipelineNo": 4, "displayName": f"pipe 4 on {s}"} for s in (4001, 4002)}
    wire = estate_payload.pipeline_alerts_wire(incidents, AlertsSettings(), {}, {}, lookup)
    assert sorted(a["alertId"] for a in wire) == ["pipeline:4001:4", "pipeline:4002:4"]
    assert {a["kind"] for a in wire} == {"pipeline"}
    assert {a["affects"][0] for a in wire} == {"4001:4", "4002:4"}
    assert "pipe 4 on 4002" in next(a for a in wire if a["alertId"] == "pipeline:4002:4")["reason"]


def test_service_flow_keeps_only_pairs_and_pipelines_with_a_services_end():
    from types import SimpleNamespace

    from _ws.estate_payload import service_flow

    config = SimpleNamespace(services=[SimpleNamespace(server_id=9001), SimpleNamespace(server_id=9002)])
    pipelines = [
        {"pipelineId": 1, "sourceSystem": "9001", "targetSystem": "9002"},
        {"pipelineId": 2, "sourceSystem": "1001", "targetSystem": "9001"},
        {"pipelineId": 3, "sourceSystem": "1001", "targetSystem": "1002"},
    ]
    pairs = [
        {"id": "9001-9002", "from": "9001", "to": "9002"},
        {"id": "1001-9001", "from": "1001", "to": "9001"},
        {"id": "1001-1002", "from": "1001", "to": "1002"},
    ]
    out_pairs, out_pipelines = service_flow(config, pipelines, pairs)
    assert [p["id"] for p in out_pairs] == ["9001-9002", "1001-9001"]
    assert [p["pipelineId"] for p in out_pipelines] == [1, 2]
    assert all(p["cross_machine"] is None for p in out_pipelines)
    assert service_flow(SimpleNamespace(services=[]), pipelines, pairs) == ([], [])
