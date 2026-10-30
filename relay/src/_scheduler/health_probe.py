from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from _alerts.dedup import AlertKey, OpenIncidentIndex
from _alerts.severity import object_severity
from _scheduler.executor_client import EndpointAuthRejected, EndpointError, ExecutorClient

logger = logging.getLogger("relay.scheduler.health")

AUTH_REJECTED_TEXT = "scheduler auth rejected: check clock / machine IP"
FORBIDDEN_IP_TEXT = (
    "scheduler endpoint refused the relay IP: the endpoint saw the relay from a different IP than allowed "
    "(e.g. via another network adapter) - add that IP to machine_scheduler.allowed_ips"
)
DOWN_TEXT = "scheduler endpoint down"


@dataclass
class EndpointHealth:
    state: str = "unknown"  # unknown | up | down | auth_rejected | off
    misses: int = 0
    detail: Optional[str] = None
    machine_offline: bool = False
    probed: bool = False
    forbidden_ip: bool = False


class HealthProbeLoop:

    def __init__(
        self,
        config_store,
        registry,
        client: ExecutorClient,
        incidents: OpenIncidentIndex,
        on_change: Optional[Callable[[], None]] = None,
    ):
        self._config_store = config_store
        self._registry = registry
        self._client = client
        self._incidents = incidents
        self._on_change = on_change
        self.health: dict[int, EndpointHealth] = {}
        self._running = False

    def state_for(self, machine_id: int) -> EndpointHealth:
        return self.health.setdefault(machine_id, EndpointHealth())

    def wire_state(self, machine) -> str:
        if not machine.machine_scheduler:
            return "off"
        state = self.state_for(machine.machine_id).state
        return state if state in ("up", "auth_rejected") else "down"

    def reason(self, machine) -> Optional[str]:
        if not machine.machine_scheduler:
            return "machine scheduler not enabled"
        entry = self.state_for(machine.machine_id)
        if entry.machine_offline:
            return "machine offline"
        if entry.state == "auth_rejected":
            return FORBIDDEN_IP_TEXT if entry.forbidden_ip else AUTH_REJECTED_TEXT
        if entry.state in ("down", "unknown"):
            return "endpoint down" if entry.state == "down" else "endpoint not checked yet"
        return None

    async def tick(self) -> None:
        config = self._config_store.get()
        machines = {m.machine_id: m for m in config.machines}
        for machine_id in set(self.health) - set(machines):
            self.health.pop(machine_id, None)
            self._release(machine_id)

        before = {mid: (h.state, h.machine_offline) for mid, h in self.health.items()}
        await asyncio.gather(*(self._probe(m, config) for m in machines.values()))
        after = {mid: (h.state, h.machine_offline) for mid, h in self.health.items()}
        if before != after and self._on_change is not None:
            self._on_change()

    async def _probe(self, machine, config) -> None:
        entry = self.state_for(machine.machine_id)
        if not machine.machine_scheduler:
            entry.state, entry.misses, entry.detail, entry.machine_offline = "off", 0, None, False
            self._release(machine.machine_id)
            return

        runtime = self._registry.get(machine.machine_id)
        if runtime is None or runtime.status != "online":
            entry.state, entry.misses, entry.machine_offline = "down", 0, True
            entry.detail = "machine offline"
            self._release(machine.machine_id)
            return

        entry.machine_offline = False
        if entry.state == "off":
            entry.state = "unknown"
        try:
            await self._client.health(machine)
        except EndpointAuthRejected as exc:
            entry.state, entry.misses, entry.detail, entry.forbidden_ip = "auth_rejected", 0, str(exc), exc.forbidden_ip
            self._claim(machine, config, "auth_rejected", str(exc), exc.forbidden_ip)
        except EndpointError as exc:
            entry.misses += 1
            entry.detail = str(exc)
            if entry.misses >= config.scheduler.health_down_after_misses:
                entry.state = "down"
                self._claim(machine, config, "down", str(exc))
        else:
            entry.state, entry.misses, entry.detail, entry.forbidden_ip = "up", 0, None, False
            self._release(machine.machine_id)
        entry.probed = True

    def _claim(self, machine, config, state: str, detail: str, forbidden_ip: bool = False) -> None:
        key = AlertKey("scheduler_endpoint", str(machine.machine_id))
        record = {
            "machine_id": machine.machine_id,
            "severity": object_severity("scheduler_endpoint", config.alerts.object_severities),
            "state": state,
            "detail": detail,
            "forbidden_ip": forbidden_ip,
        }
        if self._incidents.is_open(key):
            self._incidents.get(key).update(state=state, detail=detail, forbidden_ip=forbidden_ip)
            return
        self._incidents.claim(key, record)
        logger.warning("machine %s scheduler endpoint %s: %s", machine.machine_id, state, detail)

    def _release(self, machine_id: int) -> None:
        key = AlertKey("scheduler_endpoint", str(machine_id))
        if self._incidents.is_open(key):
            self._incidents.release(key)
            logger.info("machine %s scheduler endpoint alert released", machine_id)

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("scheduler health probe tick failed")
            await asyncio.sleep(max(0.1, self._config_store.get().scheduler.health_probe_seconds))

    def stop(self) -> None:
        self._running = False
