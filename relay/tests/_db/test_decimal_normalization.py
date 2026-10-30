from __future__ import annotations

import json
from decimal import Decimal

from _db.mssql import MSSQLDriver
from _db.mysql import MySQLDriver
from _db.postgresql import PostgreSQLDriver
from _db.queries import Query


class FakeServer:
    id = 9001
    platform = "test"
    host = "127.0.0.1"
    port = 1
    user = "u"
    password = "p"
    database = "db"
    schema_name = None
    timeout_ms = None


class FakeSettings:
    query_timeout_ms_default = 4000
    tcp_timeout_ms = 2500
    auth_probe_interval_ms = 30000


_RAW_ROW = (Decimal("43"), Decimal("0"), Decimal("12.5"), None, "sync")


class _FakeCursor:
    description = [("pending",), ("failed",), ("sendLagSeconds",), ("applyLagSeconds",), ("entity_type",)]

    def execute(self, sql, params):
        pass

    def fetchall(self):
        return [_RAW_ROW]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    closed = False


def _assert_row_normalized_and_json_safe(rows):
    assert len(rows) == 1
    row = rows[0]
    assert row["pending"] == 43 and isinstance(row["pending"], int)
    assert row["failed"] == 0 and isinstance(row["failed"], int)
    assert row["sendLagSeconds"] == 12.5 and isinstance(row["sendLagSeconds"], float)
    assert row["applyLagSeconds"] is None
    json.dumps(row)


def test_mysql_blocking_dict_query_normalizes_decimal_rows():
    driver = MySQLDriver(FakeServer(), FakeSettings())
    driver._pooled_conn = _FakeConn()
    rows = driver._blocking_dict_query(Query("SELECT 1", ()))
    _assert_row_normalized_and_json_safe(rows)


def test_postgresql_blocking_dict_query_normalizes_decimal_rows():
    driver = PostgreSQLDriver(FakeServer(), FakeSettings())
    driver._pooled_conn = _FakeConn()
    rows = driver._blocking_dict_query(Query("SELECT 1", ()))
    _assert_row_normalized_and_json_safe(rows)


class _FakeMssqlCursor(_FakeCursor):
    def execute(self, sql, params=()):
        pass


class _FakeMssqlConn:
    def cursor(self):
        return _FakeMssqlCursor()


def test_mssql_blocking_dict_query_normalizes_decimal_rows():
    driver = MSSQLDriver(FakeServer(), FakeSettings())
    driver._pooled_conn = _FakeMssqlConn()
    rows = driver._blocking_dict_query(Query("SELECT 1", ()))
    _assert_row_normalized_and_json_safe(rows)
