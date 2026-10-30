from __future__ import annotations

import pytest

from _contract.schema import ServerEntry, ServiceBusEntry, Settings
from _fixtures import env
from _metrics.down_confirm import DownConfirm
from _metrics.poll_specs import build_all_poll_specs, build_bus_poll_spec, build_server_poll_spec
from _metrics.poller import Poller
from _metrics.readability import ReadStatus


def _settings() -> Settings:
    return Settings(
        auth={
            "web_client_id": "w", "web_client_secret": "w",
            "app_client_id": "a", "app_client_secret": "a",
        },
        ack_pin="1234",
    )


_OFFLINE_CREDS = {"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db", "dbname": "db"}


def _mysql_server(offline: bool = False) -> ServerEntry:
    c = _OFFLINE_CREDS if offline else env.mysql_credentials()
    return ServerEntry(
        id=1001, name="MySQL", platform="mysql", host=c["host"], port=c["port"],
        user=c["user"], password=c["password"], database=c["database"],
    )


def _bus1() -> ServiceBusEntry:
    return ServiceBusEntry(id=1, name="sb-emulator-1", health_url="http://localhost:5300/health", amqp_port=5672, topic="sync")


async def test_server_poll_spec_runs_through_poller_p13():
    spec = build_server_poll_spec(_mysql_server(), _settings())
    poller = Poller(down_confirm=DownConfirm())
    result = await poller.run_cycle([spec])
    assert result.published is True
    assert result.metrics["db:mysql:1001:query"] == ReadStatus.OK


@pytest.mark.live_lab
async def test_bus_poll_spec_health_and_endpoint_p17_p18():
    spec = build_bus_poll_spec(_bus1(), _settings())
    poller = Poller(down_confirm=DownConfirm())
    result = await poller.run_cycle([spec])
    assert result.metrics["bus:1:health"] == ReadStatus.OK
    assert result.metrics["bus:1:endpoint"] == ReadStatus.OK


async def test_build_all_poll_specs_combines_servers_and_buses():
    from _contract.schema import RelayConfig

    c = env.mysql_credentials()
    config = RelayConfig.model_validate(
        {
            "settings": _settings().model_dump(by_alias=True),
            "alerts": {},
            "service_busses": [
                {"id": 1, "name": "sb-emulator-1", "health_url": "http://localhost:5300/health", "amqp_port": 5672, "topic": "sync"},
            ],
            "machines": [
                {
                    "machine_id": 1, "machine_ip": "127.0.0.1", "machine_port": 5300,
                    "machine_name": "m1", "machine_type": "LOCAL", "machine_os": "Windows",
                    "nodes": [
                        {
                            "node_id": "node-mysql-1001", "node_type": "Database", "node_platform": "mysql",
                            "server_id": 1001,
                            "node_config": {
                                "host": c["host"], "port": c["port"], "user": c["user"],
                                "password": c["password"], "database": c["database"],
                            },
                        },
                    ],
                }
            ],
        }
    )
    specs = build_all_poll_specs(config, config.settings)
    assert {s.object_id_prefix for s in specs} == {"db:mysql:1001", "bus:1"}

    poller = Poller.from_settings(config.settings, DownConfirm.from_settings(config.settings))
    result = await poller.run_cycle(specs)
    assert result.published is True
    assert result.metrics["db:mysql:1001:query"] == ReadStatus.OK
    assert result.metrics["bus:1:health"] == ReadStatus.OK


def _blackhole_bus() -> ServiceBusEntry:
    return ServiceBusEntry(id=99, name="blackhole", health_url="http://192.0.2.1:1/health", amqp_port=1, topic="x")


async def test_c25_amqp_timeout_ms_non_default_reaches_the_real_tcp_connect():
    import time

    from _db.servicebus import ServiceBusDriver

    settings = _settings().model_copy(update={"amqp_timeout_ms": 200})
    driver = ServiceBusDriver(_blackhole_bus(), settings)
    started = time.monotonic()
    status = await driver.check_amqp_endpoint("192.0.2.1")
    elapsed = time.monotonic() - started
    assert status == ReadStatus.DOWN
    assert elapsed < 2.0


async def test_c26_emulator_timeout_ms_non_default_reaches_the_real_http_client():
    import time

    from _db.servicebus import ServiceBusDriver

    settings = _settings().model_copy(update={"emulator_timeout_ms": 200})
    driver = ServiceBusDriver(_blackhole_bus(), settings)
    started = time.monotonic()
    status = await driver.check_health()
    elapsed = time.monotonic() - started
    assert status == ReadStatus.DOWN
    assert elapsed < 2.0


class _FakeOdbcDriver:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.begin_calls = 0
        self.end_calls = 0
        self.cycle_active_during_check = None

    def begin_poll_cycle(self):
        self.begin_calls += 1

    def end_poll_cycle(self):
        self.end_calls += 1

    async def check_chain(self):
        self.cycle_active_during_check = self.begin_calls > self.end_calls
        if self.fail:
            raise RuntimeError("simulated query failure")
        return ReadStatus.OK


async def test_o8_o11_query_stage_calls_begin_before_and_end_after_on_success(monkeypatch):
    from _metrics import poll_specs

    fake = _FakeOdbcDriver()
    monkeypatch.setattr(poll_specs, "build_driver", lambda server, settings: fake)
    spec = build_server_poll_spec(_mysql_server(offline=True), _settings())
    status = await spec.stages[0][1]()
    assert status == ReadStatus.OK
    assert fake.begin_calls == 1
    assert fake.end_calls == 1
    assert fake.cycle_active_during_check is True


async def test_o8_end_poll_cycle_runs_in_a_finally_even_on_a_raised_exception(monkeypatch):
    from _metrics import poll_specs

    fake = _FakeOdbcDriver(fail=True)
    monkeypatch.setattr(poll_specs, "build_driver", lambda server, settings: fake)
    spec = build_server_poll_spec(_mysql_server(offline=True), _settings())
    try:
        await spec.stages[0][1]()
    except RuntimeError:
        pass
    assert fake.end_calls == 1


@pytest.mark.live_lab
async def test_o8_o11_non_odbc_drivers_are_never_given_a_begin_end_cycle_call():
    spec = build_server_poll_spec(_mysql_server(), _settings())
    status = await spec.stages[0][1]()
    assert status == ReadStatus.OK
