from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable, Optional

from _metrics.attribution import AttributionResult, build_object_inventory, compute_attribution
from _metrics.down_confirm import DownConfirm
from _metrics.poll_specs import build_all_poll_specs
from _metrics.poll_stats import PollStats
from _metrics.poller import Poller
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _lib.config import ConfigStore
    from _machines.registry import MachineRegistry

logger = logging.getLogger("relay.metrics.parity_loop")


class ParityLoop:
    def __init__(
        self,
        config_store: "ConfigStore",
        down_confirm: DownConfirm,
        poller: Poller,
        poll_stats: Optional[PollStats] = None,
        machine_registry: Optional["MachineRegistry"] = None,
    ):
        self._config_store = config_store
        self.down_confirm = down_confirm
        self.poller = poller
        self._poll_stats = poll_stats
        self._machine_registry = machine_registry
        self.latest: Optional[AttributionResult] = None
        self._running = False

    def _offline_machine_ids(self, config) -> frozenset[int]:
        if self._machine_registry is None:
            return frozenset()
        offline: set[int] = set()
        for machine in config.machines:
            state = self._machine_registry.get(machine.machine_id)
            status = state.status if state is not None else "pending"
            self.down_confirm.observe(f"machine:{machine.machine_id}", ReadStatus.DOWN if status == "offline" else ReadStatus.OK)
            if status == "offline":
                offline.add(machine.machine_id)
        return frozenset(offline)

    async def tick(self) -> AttributionResult:
        config = self._config_store.get()
        specs = build_all_poll_specs(config, config.settings)
        await self.poller.run_cycle(specs)
        offline_machine_ids = self._offline_machine_ids(config)
        inventory = build_object_inventory(config, offline_machine_ids=offline_machine_ids)
        self.latest = compute_attribution(inventory, self.down_confirm)
        return self.latest

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("parity poll cycle failed")
            settings = self._config_store.get().settings
            await asyncio.sleep(max(0.1, settings.poll_seconds))

    def stop(self) -> None:
        self._running = False
