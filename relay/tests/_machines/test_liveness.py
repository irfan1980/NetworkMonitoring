from __future__ import annotations

import socket

import pytest

from _contract.schema import RelayConfig, Settings
from _machines.liveness import MachineLivenessLoop
from _machines.registry import MachineRegistry
from _metrics.down_confirm import DownConfirm


def _settings(**overrides) -> Settings:
    base = dict(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
        machine_poll_seconds=1,
        machine_poll_timeout_seconds=1,
    )
    base.update(overrides)
    return Settings(**base)


def _config(machine_ip: str, machine_port: int, settings: Settings) -> RelayConfig:
    return RelayConfig.model_validate({
        "settings": settings.model_dump(by_alias=True),
        "alerts": {},
        "machines": [{
            "machine_id": 1, "machine_ip": machine_ip, "machine_port": machine_port,
            "machine_name": "m1", "machine_type": "LOCAL", "machine_os": "Windows", "nodes": [],
        }],
    })


class _FakeConfigStore:
    def __init__(self, config: RelayConfig):
        self._config = config

    def get(self) -> RelayConfig:
        return self._config


class _FakeSupervisor:
    def __init__(self):
        self.spawned: list[int] = []
        self.killed: list[int] = []

    def spawn(self, machine):
        self.spawned.append(machine.machine_id)

    def kill(self, machine_id):
        self.killed.append(machine_id)

    def check_crashes(self, machines_by_id):
        pass

    def drain(self):
        pass


@pytest.fixture
def open_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    yield port
    s.close()


@pytest.fixture
def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_reachable_machine_confirms_online_and_spawns(open_port):
    settings = _settings()
    config = _config("127.0.0.1", open_port, settings)
    registry = MachineRegistry()
    supervisor = _FakeSupervisor()
    loop = MachineLivenessLoop(_FakeConfigStore(config), registry, supervisor, down_confirm=DownConfirm.from_settings(settings))

    await loop.tick()

    state = registry.get(1)
    assert state.status == "online"
    assert state.last_active_ts is not None
    assert state.ping_ms is not None
    assert supervisor.spawned == [1]
    assert supervisor.killed == []


async def test_refused_port_confirms_offline_immediately_and_kills(unused_port, monkeypatch):
    from _machines import liveness
    from _metrics.readability import ReadStatus

    async def icmp_blocked(*_a, **_k):
        return ReadStatus.DOWN

    monkeypatch.setattr(liveness, "icmp_check", icmp_blocked)
    settings = _settings(machine_poll_timeout_seconds=3)
    config = _config("127.0.0.1", unused_port, settings)
    registry = MachineRegistry()
    supervisor = _FakeSupervisor()
    loop = MachineLivenessLoop(_FakeConfigStore(config), registry, supervisor, down_confirm=DownConfirm.from_settings(settings))

    await loop.tick()

    state = registry.get(1)
    assert state.status == "offline"
    assert state.last_active_ts is None
    assert supervisor.killed == [1]
    assert supervisor.spawned == []


async def test_on_transition_fires_once_on_genuine_transition_not_every_tick(open_port):
    settings = _settings()
    config = _config("127.0.0.1", open_port, settings)
    registry = MachineRegistry()
    supervisor = _FakeSupervisor()
    transitions: list[tuple[int, bool]] = []
    loop = MachineLivenessLoop(
        _FakeConfigStore(config), registry, supervisor,
        down_confirm=DownConfirm.from_settings(settings), on_transition=lambda mid, online: transitions.append((mid, online)),
    )

    await loop.tick()
    await loop.tick()
    await loop.tick()

    assert transitions == [(1, True)]


async def test_removed_machine_is_killed_and_dropped_from_registry(open_port):
    settings = _settings()
    config = _config("127.0.0.1", open_port, settings)
    registry = MachineRegistry()
    supervisor = _FakeSupervisor()
    loop = MachineLivenessLoop(_FakeConfigStore(config), registry, supervisor, down_confirm=DownConfirm.from_settings(settings))
    await loop.tick()
    assert registry.get(1) is not None

    empty_config = RelayConfig.model_validate({
        "settings": settings.model_dump(by_alias=True), "alerts": {}, "machines": [],
    })
    loop._config_store = _FakeConfigStore(empty_config)
    supervisor.killed.clear()
    await loop.tick()

    assert registry.get(1) is None
    assert supervisor.killed == [1]


async def test_c1_p7_machine_poll_seconds_reaches_run_forever_scheduling(open_port):
    import asyncio

    settings = _settings(machine_poll_seconds=1)
    config = _config("127.0.0.1", open_port, settings)
    registry = MachineRegistry()
    supervisor = _FakeSupervisor()
    tick_count = 0

    class _CountingLoop(MachineLivenessLoop):
        async def tick(self):
            nonlocal tick_count
            tick_count += 1
            await super().tick()

    loop = _CountingLoop(_FakeConfigStore(config), registry, supervisor, down_confirm=DownConfirm.from_settings(settings))
    task = asyncio.create_task(loop.run_forever())
    await asyncio.sleep(2.5)
    loop.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert tick_count >= 2
