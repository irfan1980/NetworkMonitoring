from __future__ import annotations

from _contract.schema import RelayConfig
from _ws.machine_scope import (
    build_machine_sections,
    hosted_server_ids,
    machines_wire,
    scope_pairs_for_machine,
    scope_pipelines_for_machine,
    server_machine_map,
    used_bus_ids,
)


def _config() -> RelayConfig:
    return RelayConfig.model_validate(
        {
            "settings": {
                "bind_host": "127.0.0.1",
                "port": 8085,
                "auth": {
                    "web_client_id": "w", "web_client_secret": "ws",
                    "app_client_id": "a", "app_client_secret": "as",
                },
                "ack_pin": "1234",
            },
            "alerts": {},
            "service_busses": [
                {"id": 1, "name": "bus1", "health_url": "http://h/health", "amqp_port": 5672, "topic": "t"},
                {"id": 2, "name": "bus2", "health_url": "http://h/health", "amqp_port": 5673, "topic": "t2"},
            ],
            "machines": [
                {
                    "machine_id": 1, "machine_ip": "127.0.0.1", "machine_port": 5300,
                    "machine_name": "local-lab", "machine_type": "LOCAL", "machine_os": "Windows",
                    "nodes": [
                        {"node_id": "n1", "node_name": "MySQL", "node_type": "Database", "node_platform": "mysql", "server_id": 1001,
                         "node_config": {"host": "h", "port": 3306, "user": "u", "password": "p", "database": "d", "used_buses": [1]}},
                        {"node_id": "n2", "node_name": "PostgreSQL", "node_type": "Database", "node_platform": "postgresql", "server_id": 1002,
                         "node_config": {"host": "h", "port": 5432, "user": "u", "password": "p", "database": "d", "used_buses": [1]}},
                    ],
                },
                {
                    "machine_id": 2, "machine_ip": "10.101.8.49", "machine_port": 5300,
                    "machine_name": "remote-mssql-lab", "machine_type": "VPS", "machine_os": "Linux",
                    "nodes": [
                        {"node_id": "n4", "node_name": "MSSQL", "node_type": "Database", "node_platform": "mssql", "server_id": 1003,
                         "node_config": {"host": "h", "port": 1433, "user": "u", "password": "p", "database": "d", "used_buses": [2]}},
                    ],
                },
            ],
        }
    )


def _pipelines_wire() -> list[dict]:
    return [
        {"pipelineId": 1, "sourceSystem": "1001", "targetSystem": "1002", "health": "Healthy", "pendingOutbox": 0},
        {"pipelineId": 2, "sourceSystem": "1001", "targetSystem": "1003", "health": "Degraded", "pendingOutbox": 40},
    ]


def _pairs_wire() -> list[dict]:
    return [
        {"id": "1001->1002", "from": "1001", "to": "1002", "bus": "1", "pendingOutbox": 0},
        {"id": "1001->1003", "from": "1001", "to": "1003", "bus": "2", "pendingOutbox": 40},
    ]


def test_hosted_server_ids_and_used_bus_ids():
    config = _config()
    machine1, machine2 = config.machines
    assert hosted_server_ids(machine1) == {1001, 1002}
    assert hosted_server_ids(machine2) == {1003}
    assert used_bus_ids(config, machine1) == {1}
    assert used_bus_ids(config, machine2) == {2}


def test_server_machine_map():
    config = _config()
    mapping = server_machine_map(config)
    assert mapping == {1001: 1, 1002: 1, 1003: 2}


def test_cross_machine_pipeline_appears_on_both_machines_with_identical_fields():
    config = _config()
    pipelines = _pipelines_wire()
    pairs = _pairs_wire()
    server_machine = server_machine_map(config)

    machine1_pipelines = scope_pipelines_for_machine(pipelines, 1, server_machine, pairs)
    machine2_pipelines = scope_pipelines_for_machine(pipelines, 2, server_machine, pairs)

    assert {p["pipelineId"] for p in machine1_pipelines} == {1, 2}
    assert {p["pipelineId"] for p in machine2_pipelines} == {2}

    m1_cross = next(p for p in machine1_pipelines if p["pipelineId"] == 2)
    m2_cross = next(p for p in machine2_pipelines if p["pipelineId"] == 2)

    for key in ("pipelineId", "sourceSystem", "targetSystem", "health", "pendingOutbox"):
        assert m1_cross[key] == m2_cross[key]

    assert m1_cross["cross_machine"] == {"direction": "to", "counterpart_machine_id": 2, "service_bus_id": "2"}
    assert m2_cross["cross_machine"] == {"direction": "from", "counterpart_machine_id": 1, "service_bus_id": "2"}

    same_machine_pipeline = next(p for p in machine1_pipelines if p["pipelineId"] == 1)
    assert same_machine_pipeline["cross_machine"] is None


def test_cross_machine_pair_appears_on_both_machines_unchanged():
    config = _config()
    pairs = _pairs_wire()
    server_machine = server_machine_map(config)

    machine1_pairs = scope_pairs_for_machine(pairs, 1, server_machine)
    machine2_pairs = scope_pairs_for_machine(pairs, 2, server_machine)

    m1_cross = next(p for p in machine1_pairs if p["id"] == "1001->1003")
    m2_cross = next(p for p in machine2_pairs if p["id"] == "1001->1003")
    assert m1_cross == m2_cross


def test_build_machine_sections_scopes_pipelines_objects_and_bus():
    config = _config()
    machine1, machine2 = config.machines
    estate_sections = {
        "pipelines": _pipelines_wire(),
        "pairs": _pairs_wire(),
        "objects": [
            {"id": "db:mysql:1001:query", "kind": "db_query", "status": "ok", "label": "MySQL"},
            {"id": "db:postgresql:1002:query", "kind": "db_query", "status": "ok", "label": "PostgreSQL"},
            {"id": "db:mssql:1003:query", "kind": "db_query", "status": "down", "label": "MSSQL"},
            {"id": "bus:1:health", "kind": "bus_health", "status": "ok", "label": "bus1 health"},
            {"id": "bus:2:endpoint", "kind": "bus_endpoint", "status": "down", "label": "bus2 endpoint"},
        ],
        "buses": [
            {"id": "1", "name": "bus1", "ok": True},
            {"id": "2", "name": "bus2", "ok": False},
        ],
        "topology": {"nodes": [{"id": "1001", "kind": "platform"}, {"id": "1003", "kind": "platform"}, {"id": "bus:2", "kind": "service_bus"}]},
    }

    class _AppState:
        pipeline_alert_incidents = {}
        last_pipeline_queue_counts = {}
        last_lost_by_pipeline = {}

    machine1_sections = build_machine_sections(_AppState(), config, machine1, estate_sections)
    machine2_sections = build_machine_sections(_AppState(), config, machine2, estate_sections)

    assert {o["id"] for o in machine1_sections["objects"]} == {"db:mysql:1001:query", "db:postgresql:1002:query", "bus:1:health"}
    assert {b["id"] for b in machine1_sections["buses"]} == {"1"}

    assert {o["id"] for o in machine2_sections["objects"]} == {"db:mssql:1003:query", "bus:2:endpoint"}
    assert {b["id"] for b in machine2_sections["buses"]} == {"2"}

    assert machine2_sections["summary"]["level"] != "good"


def test_machines_wire_never_drops_an_offline_or_never_seen_machine():
    from _machines.registry import MachineRegistry

    config = _config()
    registry = MachineRegistry()
    registry.state_for(1).status = "online"
    registry.state_for(1).ping_ms = 4.2

    wire = machines_wire(config, registry)
    by_id = {m["machine_id"]: m for m in wire}

    assert by_id[1]["status"] == "online"
    assert by_id[1]["ping"] == {"rtt_ms": 4.2}
    assert by_id[2]["status"] == "pending"
    assert by_id[2]["ping"] is None
    assert by_id[2]["last_active_ts"] is None
    assert by_id[2]["machine_ip"] == "10.101.8.49"


def test_machines_wire_carries_hosted_platform_identity_even_when_offline():
    from _machines.registry import MachineRegistry

    config = _config()
    registry = MachineRegistry()
    registry.state_for(1).status = "online"

    wire = machines_wire(config, registry)
    by_id = {m["machine_id"]: m for m in wire}

    assert by_id[1]["status"] == "online"
    online_ids = {p["id"] for p in by_id[1]["platforms"]}
    assert online_ids == {"1001", "1002"}

    assert by_id[2]["status"] != "online"
    offline_platforms = by_id[2]["platforms"]
    assert offline_platforms == [{"id": "1003", "label": "MSSQL", "type": "mssql"}]


def _summary_settings():
    from types import SimpleNamespace
    return SimpleNamespace(pending_yellow_threshold=50, pending_red_threshold=500, material_backlog_factor=2)


def test_machine_summary_cannot_read_good_while_a_pipeline_has_a_stale_heartbeat():
    from _ws.machine_scope import machine_summary

    machine_pipelines = [
        {"pipelineId": 101, "sourceSystem": 1001, "targetSystem": 1002, "health": "Down", "pendingOutbox": 0}
    ]
    machine_objects = [{"kind": "db_query", "status": "ok"}]
    machine_buses = [{"ok": True}]

    result = machine_summary(_summary_settings(), machine_pipelines, machine_pairs=[], machine_objects=machine_objects, machine_buses=machine_buses, lost_by_pipeline={})

    assert result["level"] != "good"
    assert "healthy" not in result["headline"].lower()


def test_machine_summary_is_good_when_every_pipeline_is_genuinely_healthy():
    from _ws.machine_scope import machine_summary

    machine_pipelines = [
        {"pipelineId": 101, "sourceSystem": 1001, "targetSystem": 1002, "health": "Healthy", "pendingOutbox": 0}
    ]
    machine_objects = [{"kind": "db_query", "status": "ok"}]
    machine_buses = [{"ok": True}]

    result = machine_summary(_summary_settings(), machine_pipelines, machine_pairs=[], machine_objects=machine_objects, machine_buses=machine_buses, lost_by_pipeline={})

    assert result["level"] == "good"
