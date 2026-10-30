from __future__ import annotations

import asyncio
import json
import time

import pytest

from _machines.registry import MachineRegistry
from _machines.supervisor import Supervisor


def _write_config(tmp_path, node_poll_seconds: int = 1) -> str:
    config = {
        "settings": {
            "auth": {"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
            "ack_pin": "1234",
            "node_poll_seconds": node_poll_seconds,
        },
        "alerts": {},
        "machines": [{
            "machine_id": 1, "machine_ip": "127.0.0.1", "machine_port": 29101,
            "machine_name": "m1", "machine_type": "LOCAL", "machine_os": "Windows", "nodes": [],
        }],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


class _Machine:
    def __init__(self, machine_id: int):
        self.machine_id = machine_id


def test_spawn_starts_a_real_process_and_is_idempotent(tmp_path):
    config_path = _write_config(tmp_path)
    registry = MachineRegistry()
    supervisor = Supervisor(config_path, registry)
    try:
        supervisor.spawn(_Machine(1))
        assert supervisor.is_running(1)
        pid_first = supervisor._children[1].process.pid

        supervisor.spawn(_Machine(1))
        assert supervisor._children[1].process.pid == pid_first
    finally:
        supervisor.shutdown_all()


def test_kill_stops_the_process_and_clears_active_services(tmp_path):
    config_path = _write_config(tmp_path)
    registry = MachineRegistry()
    supervisor = Supervisor(config_path, registry)
    supervisor.spawn(_Machine(1))
    assert supervisor.is_running(1)
    state = registry.state_for(1)
    state.active_services = [{"node_id": "stale"}]

    supervisor.kill(1)

    assert not supervisor.is_running(1)
    assert registry.state_for(1).active_services == []


async def test_kill_closes_every_registered_socket_client_for_that_machine(tmp_path):
    config_path = _write_config(tmp_path)
    registry = MachineRegistry()
    supervisor = Supervisor(config_path, registry)
    supervisor.spawn(_Machine(1))

    closed = []

    async def _close_cb():
        closed.append(True)

    state = registry.state_for(1)
    state.register_connection(_close_cb)
    state.register_connection(_close_cb)

    supervisor.kill(1)
    await asyncio.sleep(0.2)

    assert len(closed) == 2


def test_drain_picks_up_real_child_reported_active_services(tmp_path):
    config_path = _write_config(tmp_path, node_poll_seconds=1)
    registry = MachineRegistry()
    supervisor = Supervisor(config_path, registry)
    try:
        supervisor.spawn(_Machine(1))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            supervisor.drain()
            state = registry.get(1)
            if state is not None and state.active_services == []:
                break
            time.sleep(0.2)
        else:
            pytest.fail("child process never reported an active_services tick within 10s")
    finally:
        supervisor.shutdown_all()


def test_shutdown_all_stops_every_child(tmp_path):
    config_path = _write_config(tmp_path)
    registry = MachineRegistry()
    supervisor = Supervisor(config_path, registry)
    supervisor.spawn(_Machine(1))
    assert supervisor.is_running(1)

    supervisor.shutdown_all()

    assert not supervisor.is_running(1)
