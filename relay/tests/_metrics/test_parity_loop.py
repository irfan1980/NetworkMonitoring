from __future__ import annotations

import pytest

import asyncio

from _contract.schema import RelayConfig, ServiceBusEntry, Settings
from _metrics.down_confirm import DownConfirm
from _metrics.parity_loop import ParityLoop
from _metrics.poller import Poller
from _metrics.readability import ReadStatus


def _settings(**overrides) -> Settings:
    base = dict(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
        poll_seconds=1,
    )
    base.update(overrides)
    return Settings(**base)


def _config(settings: Settings) -> RelayConfig:
    return RelayConfig.model_validate({
        "settings": settings.model_dump(by_alias=True),
        "alerts": {},
        "service_busses": [
            {"id": 1, "name": "bus-1", "health_url": "http://localhost:5300/health", "amqp_port": 5672, "topic": "sync"},
        ],
        "machines": [
            {
                "machine_id": 1, "machine_ip": "127.0.0.1", "machine_port": 5300,
                "machine_name": "m1", "machine_type": "LOCAL", "machine_os": "Windows",
                "nodes": [],
            }
        ],
    })


class _FakeConfigStore:
    def __init__(self, config: RelayConfig):
        self._config = config

    def get(self) -> RelayConfig:
        return self._config


@pytest.mark.live_lab
async def test_tick_produces_a_real_attribution_result_and_caches_it():
    settings = _settings()
    config = _config(settings)
    store = _FakeConfigStore(config)
    down_confirm = DownConfirm.from_settings(settings)
    poller = Poller.from_settings(settings, down_confirm)
    loop = ParityLoop(store, down_confirm, poller)

    assert loop.latest is None
    result = await loop.tick()
    assert loop.latest is result
    assert "bus:1:health" in result.emitted


@pytest.mark.live_lab
async def test_c1_p7_poll_seconds_genuinely_paces_run_forever():
    settings = _settings(poll_seconds=1)
    config = _config(settings)
    store = _FakeConfigStore(config)
    down_confirm = DownConfirm.from_settings(settings)
    poller = Poller.from_settings(settings, down_confirm)

    tick_count = 0

    class _CountingLoop(ParityLoop):
        async def tick(self):
            nonlocal tick_count
            tick_count += 1
            return await super().tick()

    loop = _CountingLoop(store, down_confirm, poller)
    task = asyncio.create_task(loop.run_forever())
    await asyncio.sleep(2.5)
    loop.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert tick_count >= 2


class _FakeMachineState:
    def __init__(self, status: str):
        self.status = status


class _FakeMachineRegistry:
    def __init__(self, statuses: dict[int, str]):
        self._statuses = statuses

    def get(self, machine_id: int):
        status = self._statuses.get(machine_id)
        return None if status is None else _FakeMachineState(status)


def _config_with_machine_hosted_server(settings: Settings) -> RelayConfig:
    return RelayConfig.model_validate({
        "settings": settings.model_dump(by_alias=True),
        "alerts": {},
        "machines": [
            {
                "machine_id": 5, "machine_ip": "127.0.0.1", "machine_port": 29105,
                "machine_name": "m5", "machine_type": "LOCAL", "machine_os": "Windows",
                "nodes": [
                    {
                        "node_id": "n1", "node_type": "Database", "node_platform": "mysql",
                        "node_config": {"host": "h", "port": 3306, "user": "u", "password": "p", "database": "d"},
                        "server_id": 1001,
                    }
                ],
            }
        ],
    })


async def test_tick_attributes_hosted_db_object_blocked_when_machine_confirmed_offline_task_36():
    settings = _settings()
    config = _config_with_machine_hosted_server(settings)
    store = _FakeConfigStore(config)
    down_confirm = DownConfirm.from_settings(settings)
    poller = Poller.from_settings(settings, down_confirm)
    registry = _FakeMachineRegistry({5: "offline"})
    loop = ParityLoop(store, down_confirm, poller, machine_registry=registry)

    result = await loop.tick()

    assert result.emitted["machine:5"] == ReadStatus.DOWN
    assert result.emitted["db:mysql:1001:query"] == ReadStatus.BLOCKED
    assert result.blocked_by["db:mysql:1001:query"] == "machine:5"


async def test_tick_leaves_hosted_db_object_unblocked_when_machine_online_task_36():
    settings = _settings()
    config = _config_with_machine_hosted_server(settings)
    store = _FakeConfigStore(config)
    down_confirm = DownConfirm.from_settings(settings)
    poller = Poller.from_settings(settings, down_confirm)
    registry = _FakeMachineRegistry({5: "online"})
    loop = ParityLoop(store, down_confirm, poller, machine_registry=registry)

    result = await loop.tick()

    assert "machine:5" not in {k for k, v in result.blocked_by.items()}
    assert "db:mysql:1001:query" not in result.blocked_by


async def test_tick_pending_machine_is_not_treated_as_offline_task_36():
    settings = _settings()
    config = _config_with_machine_hosted_server(settings)
    store = _FakeConfigStore(config)
    down_confirm = DownConfirm.from_settings(settings)
    poller = Poller.from_settings(settings, down_confirm)
    registry = _FakeMachineRegistry({5: "pending"})
    loop = ParityLoop(store, down_confirm, poller, machine_registry=registry)

    result = await loop.tick()

    assert "db:mysql:1001:query" not in result.blocked_by


async def test_tick_without_a_machine_registry_is_unchanged_from_pre_task_36():
    settings = _settings()
    config = _config_with_machine_hosted_server(settings)
    store = _FakeConfigStore(config)
    down_confirm = DownConfirm.from_settings(settings)
    poller = Poller.from_settings(settings, down_confirm)
    loop = ParityLoop(store, down_confirm, poller)

    result = await loop.tick()

    assert "machine:5" not in result.emitted
