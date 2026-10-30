from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Optional

from _db.common import icmp_check, tcp_check
from _metrics.down_confirm import DownConfirm
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _lib.config import ConfigStore
    from _machines.registry import MachineRegistry
    from _machines.supervisor import Supervisor

logger = logging.getLogger("relay.machines.liveness")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MachineLivenessLoop:
    def __init__(
        self,
        config_store: "ConfigStore",
        registry: "MachineRegistry",
        supervisor: "Supervisor",
        down_confirm: Optional[DownConfirm] = None,
        on_transition: Optional[Callable[[int, bool], None]] = None,
    ):
        self._config_store = config_store
        self._registry = registry
        self._supervisor = supervisor
        self._down_confirm = down_confirm or DownConfirm.from_settings(config_store.get().settings)
        self._on_transition = on_transition
        self._running = False

    async def tick(self) -> None:
        config = self._config_store.get()
        seen_ids: set[int] = set()

        for machine in config.machines:
            seen_ids.add(machine.machine_id)
            state = self._registry.state_for(machine.machine_id)
            was_online = state.status == "online"

            timeout_ms = config.settings.machine_poll_timeout_seconds * 1000
            started = time.monotonic()
            icmp_result = await icmp_check(machine.machine_ip, timeout_ms)
            raw = icmp_result
            probe_desc = f"icmp {machine.machine_ip}"
            decider = "icmp"
            tcp_result = None
            if raw != ReadStatus.OK and machine.machine_port is not None:
                tcp_result = await tcp_check(machine.machine_ip, machine.machine_port, timeout_ms)
                raw = tcp_result
                probe_desc = f"icmp {machine.machine_ip} + tcp fallback {machine.machine_ip}:{machine.machine_port}"
                decider = "tcp_fallback"
            elif raw != ReadStatus.OK:
                decider = "icmp_only_no_fallback_port"
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.debug(
                "machine %s (%s) liveness tick: icmp=%s tcp=%s decider=%s verdict=%s",
                machine.machine_id,
                machine.machine_ip,
                icmp_result.name,
                tcp_result.name if tcp_result is not None else "not_attempted",
                decider,
                raw.name,
            )

            confirmed = self._down_confirm.observe(f"machine:{machine.machine_id}:liveness", raw)
            is_online = confirmed == ReadStatus.OK

            state.last_probe_ts = _now_iso()
            if raw == ReadStatus.OK:
                state.ping_ms = round(elapsed_ms, 1)
            if is_online:
                state.last_active_ts = state.last_probe_ts
                state.status = "online"
                self._supervisor.spawn(machine)
            else:
                state.status = "offline"
                self._supervisor.kill(machine.machine_id)

            if is_online != was_online:
                if is_online:
                    logger.info("machine %s reachable again (%s)", machine.machine_id, probe_desc)
                else:
                    logger.warning("machine %s offline: %s not responding", machine.machine_id, probe_desc)
                if self._on_transition is not None:
                    self._on_transition(machine.machine_id, is_online)

        for machine_id in self._registry.known_ids() - seen_ids:
            self._supervisor.kill(machine_id)
            self._registry.drop(machine_id)

        self._supervisor.check_crashes({m.machine_id: m for m in config.machines})
        self._supervisor.drain()

    async def run_forever(self) -> None:
        import asyncio

        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("machine liveness tick failed")
            settings = self._config_store.get().settings
            await asyncio.sleep(max(0.1, settings.machine_poll_seconds))

    def stop(self) -> None:
        self._running = False
