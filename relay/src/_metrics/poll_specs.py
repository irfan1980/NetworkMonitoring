from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from _db.factory import build_driver
from _db.servicebus import ServiceBusDriver
from _metrics.poller import PollSpec

if TYPE_CHECKING:
    from _contract.schema import RelayConfig


def build_server_poll_spec(server, settings) -> PollSpec:
    driver = build_driver(server, settings)

    async def query_stage():
        has_cycle_contract = hasattr(driver, "begin_poll_cycle")
        if has_cycle_contract:
            driver.begin_poll_cycle()
        try:
            return await driver.check_chain()
        finally:
            if has_cycle_contract:
                driver.end_poll_cycle()

    return PollSpec(object_id_prefix=f"db:{server.platform}:{server.id}", stages=[("query", query_stage)])


def build_bus_poll_spec(bus, settings) -> PollSpec:
    driver = ServiceBusDriver(bus, settings)
    host = urlparse(bus.health_url).hostname or "127.0.0.1"

    async def health_stage():
        return await driver.check_health()

    async def amqp_stage():
        return await driver.check_amqp_endpoint(host)

    return PollSpec(object_id_prefix=f"bus:{bus.id}", stages=[("health", health_stage), ("endpoint", amqp_stage)])


def build_all_poll_specs(config: "RelayConfig", settings) -> list[PollSpec]:
    specs = [build_server_poll_spec(s, settings) for s in config.servers]
    specs += [build_bus_poll_spec(b, settings) for b in config.service_busses]
    return specs
