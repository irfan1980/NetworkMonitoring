from __future__ import annotations

import pytest
from pydantic import ValidationError

from _contract.schema import RelayConfig


def test_service_bus_is_top_level_not_owned_by_any_machine(config_dict):
    config_dict["service_busses"] = [
        {"id": 1, "name": "bus-1", "health_url": "http://127.0.0.1:5300/health", "amqp_port": 5672, "topic": "sync"},
    ]
    config = RelayConfig.model_validate(config_dict)
    assert [b.id for b in config.service_busses] == [1]
    assert config.machines[0].nodes == []


def test_service_bus_node_type_is_refused_under_a_machine(config_dict):
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "bad-bus-node",
            "node_type": "ServiceBus",
            "node_platform": "service_bus",
            "server_id": 1,
            "node_config": {"health_url": "http://127.0.0.1:5300/health", "amqp_port": 5672, "topic": "sync"},
        }
    ]
    with pytest.raises(ValidationError):
        RelayConfig.model_validate(config_dict)


def test_standalone_service_is_accepted_and_composed_into_servers(config_dict):
    config_dict["services"] = [
        {
            "node_id": "svc-managed-pg",
            "node_name": "Managed PostgreSQL (hosted)",
            "node_type": "Database",
            "node_platform": "postgresql",
            "server_id": 2001,
            "node_config": {
                "host": "managed-pg.example.internal",
                "port": 5432,
                "user": "app",
                "password": "secret",
                "database": "app_db",
                "used_buses": [],
            },
        }
    ]
    config = RelayConfig.model_validate(config_dict)
    assert config.services[0].node_id == "svc-managed-pg"
    ids = {s.id for s in config.servers}
    assert 2001 in ids
    assert all(n.server_id != 2001 for m in config.machines for n in m.nodes)


def test_standalone_service_server_id_must_be_globally_unique_with_machine_nodes(config_dict):
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "n1", "node_type": "Database", "node_platform": "mysql", "server_id": 1001,
            "node_config": {"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db"},
        }
    ]
    config_dict["services"] = [
        {
            "node_id": "svc-collide", "node_type": "Database", "node_platform": "postgresql", "server_id": 1001,
            "node_config": {"host": "h", "port": 5432, "user": "u", "password": "p", "database": "d"},
        }
    ]
    with pytest.raises(ValidationError):
        RelayConfig.model_validate(config_dict)


def test_standalone_service_used_buses_validated_against_top_level_service_busses(config_dict):
    config_dict["service_busses"] = [
        {"id": 1, "name": "bus-1", "health_url": "http://127.0.0.1:5300/health", "amqp_port": 5672, "topic": "sync"},
    ]
    config_dict["services"] = [
        {
            "node_id": "svc-ok", "node_type": "Database", "node_platform": "mysql", "server_id": 3001,
            "node_config": {
                "host": "h", "port": 3306, "user": "u", "password": "p", "database": "d", "used_buses": [1],
            },
        }
    ]
    config = RelayConfig.model_validate(config_dict)
    assert config.servers[0].used_buses == [1]

    config_dict["services"][0]["node_config"]["used_buses"] = [999]
    with pytest.raises(ValidationError):
        RelayConfig.model_validate(config_dict)
