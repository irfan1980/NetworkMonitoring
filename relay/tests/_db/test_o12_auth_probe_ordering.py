from __future__ import annotations

import pytest

from _db.mssql import MSSQLDriver
from _db.mysql import MySQLDriver
from _db.postgresql import PostgreSQLDriver
from _metrics.readability import ReadStatus


class FakeServer:
    id = 1
    platform = "mysql"
    host = "127.0.0.1"
    port = 3306
    user = "u"
    password = "p"
    database = "d"
    schema_name = None
    timeout_ms = None


class FakeSettings:
    query_timeout_ms_default = 4000
    query_timeout_ms_odbc = 8000
    tcp_timeout_ms = 2500
    probe_timeout_ms_odbc = 6000
    auth_probe_interval_ms = 30000


DRIVER_CLASSES = [MySQLDriver, PostgreSQLDriver, MSSQLDriver]


async def _fake_tcp_ok(*args, **kwargs):
    return ReadStatus.OK


@pytest.mark.parametrize("driver_cls", DRIVER_CLASSES, ids=[c.__name__ for c in DRIVER_CLASSES])
async def test_auth_probe_runs_before_pooled_select_1_when_due(monkeypatch, driver_cls):
    order: list[str] = []
    module = __import__(driver_cls.__module__, fromlist=["tcp_check"])
    monkeypatch.setattr(module, "tcp_check", _fake_tcp_ok)

    d = driver_cls(FakeServer(), FakeSettings())
    assert d._auth_probe.is_due() is True

    def fake_auth_probe():
        order.append("auth")
        return True

    def fake_pooled_select_1():
        order.append("pooled")

    monkeypatch.setattr(d, "_blocking_auth_probe", fake_auth_probe)
    monkeypatch.setattr(d, "_blocking_pooled_select_1", fake_pooled_select_1)

    status = await d.check_chain()

    assert order == ["auth", "pooled"]
    assert status == ReadStatus.OK


@pytest.mark.parametrize("driver_cls", DRIVER_CLASSES, ids=[c.__name__ for c in DRIVER_CLASSES])
async def test_a_failing_auth_probe_short_circuits_before_pooled_is_ever_called(monkeypatch, driver_cls):
    module = __import__(driver_cls.__module__, fromlist=["tcp_check"])
    monkeypatch.setattr(module, "tcp_check", _fake_tcp_ok)

    d = driver_cls(FakeServer(), FakeSettings())
    pooled_called = {"n": 0}

    monkeypatch.setattr(d, "_blocking_auth_probe", lambda: False)

    def fake_pooled_select_1():
        pooled_called["n"] += 1

    monkeypatch.setattr(d, "_blocking_pooled_select_1", fake_pooled_select_1)

    status = await d.check_chain()

    assert status == ReadStatus.DOWN
    assert pooled_called["n"] == 0


@pytest.mark.parametrize("driver_cls", DRIVER_CLASSES, ids=[c.__name__ for c in DRIVER_CLASSES])
async def test_auth_probe_not_due_yet_skips_straight_to_pooled(monkeypatch, driver_cls):
    module = __import__(driver_cls.__module__, fromlist=["tcp_check"])
    monkeypatch.setattr(module, "tcp_check", _fake_tcp_ok)

    d = driver_cls(FakeServer(), FakeSettings())
    d._auth_probe.record(True)

    order: list[str] = []
    monkeypatch.setattr(d, "_blocking_auth_probe", lambda: order.append("auth") or True)
    monkeypatch.setattr(d, "_blocking_pooled_select_1", lambda: order.append("pooled"))

    status = await d.check_chain()

    assert order == ["pooled"]
    assert status == ReadStatus.OK
