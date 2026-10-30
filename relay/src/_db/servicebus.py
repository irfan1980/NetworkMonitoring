from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from _db.common import tcp_check
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _contract.schema import ServiceBusEntry, Settings


class ServiceBusDriver:
    def __init__(self, bus: "ServiceBusEntry", settings: "Settings"):
        self.bus = bus
        self.settings = settings

    async def check_health(self) -> ReadStatus:
        try:
            async with httpx.AsyncClient(timeout=self.settings.emulator_timeout_ms / 1000) as client:
                resp = await client.get(self.bus.health_url)
        except httpx.HTTPError:
            return ReadStatus.DOWN
        return ReadStatus.OK if resp.status_code == 200 else ReadStatus.DOWN

    async def check_amqp_endpoint(self, host: str) -> ReadStatus:
        return await tcp_check(host, self.bus.amqp_port, self.settings.amqp_timeout_ms)
