from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from _alerts.dedup import OpenIncidentIndex
from _lib.config import ConfigStore
from _machines.registry import MachineRegistry
from _scheduler.executor_client import ExecutorClient
from _scheduler.health_probe import HealthProbeLoop
from _scheduler.loop import SchedulerLoop
from _scheduler.model import ScheduleInput
from _scheduler.runlog import RunLog
from _scheduler.store import ScheduleStore

from .fake_endpoint import FakeEndpoint

SCRIPT = r"D:\jobs\ok.cmd"
FIXED_NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, now: datetime = FIXED_NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def schedule_input(**overrides) -> ScheduleInput:
    base = {
        "name": "nightly",
        "machine_id": 1,
        "script": SCRIPT,
        "trigger": {"kind": "interval", "every_seconds": 60},
        "controls": {"retry_delay_s": 0, "retries": 0},
    }
    base.update(overrides)
    return ScheduleInput.model_validate(base)


class Harness:
    def __init__(self, tmp_path: Path, config_dict: dict, state_dir: Path | None = None):
        config_dict["machines"][0]["machine_scheduler"] = True
        config_dict["scheduler"] = {"tick_seconds": 0.01, "health_down_after_misses": 2, **config_dict.get("scheduler", {})}
        path = tmp_path / "relay_config.json"
        path.write_text(json.dumps(config_dict), encoding="utf-8")
        self.config_store = ConfigStore(path)
        self.config_store.load_startup()
        self.state_dir = state_dir or tmp_path / "state"
        self.registry = MachineRegistry()
        self.registry.state_for(1).status = "online"
        self.clock = FakeClock()
        self.endpoint = FakeEndpoint()
        self.endpoint.add_script(SCRIPT)
        self.settings = lambda: self.config_store.get().scheduler
        self.store = ScheduleStore(self.state_dir)
        self.runlog = RunLog(self.state_dir, self.settings)
        self.client = ExecutorClient(self.settings, transport=httpx.MockTransport(self.endpoint.handler))
        self.endpoint_incidents = OpenIncidentIndex()
        self.schedule_incidents = OpenIncidentIndex()
        self.health = HealthProbeLoop(self.config_store, self.registry, self.client, self.endpoint_incidents)
        self.changes = 0
        self.loop = self.new_loop()

    def new_loop(self) -> SchedulerLoop:
        return SchedulerLoop(
            self.config_store, self.registry, self.store, self.runlog, self.client, self.health,
            self.schedule_incidents, self.state_dir, on_change=self._changed, clock=self.clock,
        )

    def _changed(self) -> None:
        self.changes += 1

    def add(self, **overrides):
        return self.store.create(schedule_input(**overrides))

    async def settle(self, loop: SchedulerLoop | None = None, timeout: float = 5.0) -> None:
        loop = loop or self.loop
        deadline = asyncio.get_running_loop().time() + timeout
        while loop.active:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"runs still active: {list(loop.active)}")
            await asyncio.sleep(0.01)

    async def until(self, predicate, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("condition not reached")
            await asyncio.sleep(0.01)

    def runs(self, schedule_id: int) -> list[dict]:
        rows, _ = self.runlog.read(schedule_id, limit=500)
        return list(reversed(rows))


@pytest.fixture
def harness(tmp_path, config_dict) -> Harness:
    return Harness(tmp_path, config_dict)
