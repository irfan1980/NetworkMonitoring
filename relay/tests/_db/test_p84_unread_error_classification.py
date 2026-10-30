from __future__ import annotations

import pytest

from _db.mysql import MySQLDriver
from _metrics.readability import ReadStatus


class FakeServer:
    id = 1001
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
    tcp_timeout_ms = 2500
    auth_probe_interval_ms = 30000


async def _fake_tcp_ok(*args, **kwargs):
    return ReadStatus.OK


def _driver(monkeypatch) -> MySQLDriver:
    import _db.mysql as mysql_module

    monkeypatch.setattr(mysql_module, "tcp_check", _fake_tcp_ok)
    d = MySQLDriver(FakeServer(), FakeSettings())
    d._auth_probe.record(True)
    return d


class _QueryLevelError(Exception):

    args = (9999, "some transient query-level problem")


class _AuthError(Exception):
    args = (3118, "ER_ACCOUNT_HAS_BEEN_LOCKED")


class _NetworkError(Exception):
    args = (2003, "CR_CONN_HOST_ERROR")


async def test_a_query_level_pooled_check_failure_classifies_unread_not_down_p84(monkeypatch):
    d = _driver(monkeypatch)

    def fake_pooled_select_1():
        raise _QueryLevelError()

    monkeypatch.setattr(d, "_blocking_pooled_select_1", fake_pooled_select_1)

    status = await d.check_chain()
    assert status == ReadStatus.UNREAD


async def test_an_auth_classified_pooled_check_failure_still_classifies_down_p84(monkeypatch):
    d = _driver(monkeypatch)

    def fake_pooled_select_1():
        raise _AuthError()

    monkeypatch.setattr(d, "_blocking_pooled_select_1", fake_pooled_select_1)

    status = await d.check_chain()
    assert status == ReadStatus.DOWN


async def test_a_network_classified_pooled_check_failure_still_classifies_down_p84(monkeypatch):
    d = _driver(monkeypatch)

    def fake_pooled_select_1():
        raise _NetworkError()

    monkeypatch.setattr(d, "_blocking_pooled_select_1", fake_pooled_select_1)

    status = await d.check_chain()
    assert status == ReadStatus.DOWN


async def test_a_pooled_check_timeout_classifies_unread_not_down_p84(monkeypatch):
    import _db.mysql as mysql_module

    d = _driver(monkeypatch)

    async def fake_run_blocking(fn, *args, timeout_ms):
        if fn is d._blocking_pooled_select_1:
            raise TimeoutError()
        return await mysql_module.run_blocking(fn, *args, timeout_ms=timeout_ms)

    monkeypatch.setattr(mysql_module, "run_blocking", fake_run_blocking)

    status = await d.check_chain()
    assert status == ReadStatus.UNREAD
