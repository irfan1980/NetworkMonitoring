from __future__ import annotations

import socket

import pytest

from _contract.schema import Machine, MachineNode, Settings
from _machines.node_probe import _parse_pos_connection_string, probe_node
from _metrics.readability import ReadStatus


def _settings(**overrides) -> Settings:
    base = dict(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
        tcp_timeout_ms=3000,
        query_timeout_ms_default=500,
        emulator_timeout_ms=800,
        amqp_timeout_ms=500,
    )
    base.update(overrides)
    return Settings(**base)


def _machine(ip: str = "127.0.0.1", port: int = 1) -> Machine:
    return Machine(machine_id=1, machine_ip=ip, machine_port=port, machine_name="m1", machine_type="LOCAL", machine_os="Windows")


@pytest.fixture
def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_database_node_refused_port_is_down(unused_port):
    node = MachineNode(
        node_id="n1", node_type="Database", node_platform="mysql", server_id=1001,
        node_config={"host": "127.0.0.1", "port": unused_port, "user": "u", "password": "p", "database": "d"},
    )
    status = await probe_node(_machine(), node, _settings())
    assert status == ReadStatus.DOWN


async def test_database_node_blackhole_ip_is_down():
    node = MachineNode(
        node_id="n1", node_type="Database", node_platform="mysql", server_id=1001,
        node_config={"host": "192.0.2.1", "port": 1, "user": "u", "password": "p", "database": "d"},
    )
    status = await probe_node(_machine(ip="192.0.2.1"), node, _settings(tcp_timeout_ms=200))
    assert status == ReadStatus.DOWN


async def test_pos_node_malformed_connection_string_is_unread_not_a_crash():
    node = MachineNode(
        node_id="n3", node_type="POS", node_platform="lightspeed", server_id=2001,
        node_config={"connection_string": "not a valid connection string at all"},
    )
    status = await probe_node(_machine(), node, _settings())
    assert status == ReadStatus.UNREAD


async def test_pos_node_well_formed_connection_string_refused_port_is_down(unused_port):
    node = MachineNode(
        node_id="n3", node_type="POS", node_platform="lightspeed", server_id=2001,
        node_config={
            "connection_string": f"Server=127.0.0.1,{unused_port};Database=db1;User Id=sa;Password=pw;",
        },
    )
    status = await probe_node(_machine(), node, _settings())
    assert status == ReadStatus.DOWN


def test_parse_pos_connection_string_extracts_host_port_and_credentials():
    parsed = _parse_pos_connection_string("Server=10.0.0.5,1433;Database=lightspeed;User Id=sa;Password=hunter2;")
    assert parsed == {"host": "10.0.0.5", "port": 1433, "user": "sa", "password": "hunter2", "database": "lightspeed"}


def test_parse_pos_connection_string_missing_required_field_returns_none():
    assert _parse_pos_connection_string("Database=lightspeed;User Id=sa;") is None
