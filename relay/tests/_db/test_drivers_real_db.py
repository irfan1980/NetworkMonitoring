from __future__ import annotations

import socket

import pytest

from _contract.schema import ServerEntry, Settings
from _db.mssql import MSSQLDriver
from _db.mysql import MySQLDriver
from _db.postgresql import PostgreSQLDriver
from _fixtures import env
from _metrics.readability import ReadStatus


def _settings(**overrides) -> Settings:
    base = dict(
        auth={
            "web_client_id": "w",
            "web_client_secret": "w",
            "app_client_id": "a",
            "app_client_secret": "a",
        },
        ack_pin="1234",
    )
    base.update(overrides)
    return Settings(**base)


def _mysql_server() -> ServerEntry:
    c = env.mysql_credentials()
    return ServerEntry(
        id=1001, name="MySQL", platform="mysql", host=c["host"], port=c["port"],
        user=c["user"], password=c["password"], database=c["database"],
    )


def _postgresql_server() -> ServerEntry:
    c = env.postgresql_credentials()
    return ServerEntry(
        id=1002, name="PostgreSQL", platform="postgresql", host=c["host"], port=c["port"],
        user=c["user"], password=c["password"], database=c["dbname"], schema="sync",
    )


def _mssql_tcp_reachable() -> bool:
    try:
        with socket.create_connection(("localhost", 1433), timeout=1.0):
            return True
    except OSError:
        return False


async def test_mysql_check_chain_is_ok_against_the_real_server():
    driver = MySQLDriver(_mysql_server(), _settings())
    status = await driver.check_chain()
    assert status == ReadStatus.OK


async def test_mysql_registry_signature_and_rows_match_the_documented_base_data():
    driver = MySQLDriver(_mysql_server(), _settings())
    rows = await driver.fetch_registry_rows()
    assert len(rows) == 6
    assert {r.pipeline_id for r in rows} == {101, 102, 103, 104, 105, 106}
    sig = await driver.fetch_registry_signature()
    assert sig.split("|")[0] == "6:106"


async def test_mysql_pipelines_with_metrics_excludes_disabled_pipelines_real_data():
    driver = MySQLDriver(_mysql_server(), _settings())
    rows = await driver.fetch_pipelines_with_metrics()
    assert rows == []


async def test_mysql_triggers_returns_the_documented_18_rows():
    driver = MySQLDriver(_mysql_server(), _settings())
    rows = await driver.fetch_triggers()
    assert len(rows) == 18


async def test_mysql_driver_satisfies_registrydriver_protocol_p5_p6_end_to_end():
    from _metrics.registry import RegistryCache

    cache = RegistryCache()
    driver = MySQLDriver(_mysql_server(), _settings())
    result = await cache.refresh("1001", driver)
    assert result.changed is True
    assert len(result.pairs) == 6

    result2 = await cache.refresh("1001", driver)
    assert result2.changed is False


async def test_mysql_existing_routine_names_s11_schema_filtered():
    driver = MySQLDriver(_mysql_server(), _settings())
    names = await driver.fetch_existing_routine_names()
    assert isinstance(names, set)


async def test_mysql_c27_per_server_timeout_override_reaches_the_driver():
    server = _mysql_server().model_copy(update={"timeout_ms": 777})
    driver = MySQLDriver(server, _settings())
    assert driver.query_timeout_ms == 777


async def test_mysql_c27_absurdly_small_timeout_override_actually_times_out_a_real_query():
    server = _mysql_server().model_copy(update={"timeout_ms": 1})
    driver = MySQLDriver(server, _settings())
    status = await driver.check_chain()
    assert status == ReadStatus.UNREAD


async def test_postgresql_check_chain_is_ok_against_the_real_server():
    driver = PostgreSQLDriver(_postgresql_server(), _settings())
    status = await driver.check_chain()
    assert status == ReadStatus.OK


async def test_postgresql_registry_rows_match_the_documented_base_data():
    driver = PostgreSQLDriver(_postgresql_server(), _settings())
    rows = await driver.fetch_registry_rows()
    assert len(rows) == 6
    assert {r.pipeline_id for r in rows} == {201, 202, 203, 204, 205, 206}


async def test_postgresql_pipelines_with_metrics_s3_shape_against_real_data():
    driver = PostgreSQLDriver(_postgresql_server(), _settings())
    rows = await driver.fetch_pipelines_with_metrics()
    assert len(rows) == 6


async def test_postgresql_triggers_returns_the_documented_18_rows():
    driver = PostgreSQLDriver(_postgresql_server(), _settings())
    rows = await driver.fetch_triggers()
    assert len(rows) == 18


async def test_mysql_fetch_inbox_since_returns_normalised_iso_strings_s13():
    driver = MySQLDriver(_mysql_server(), _settings())
    rows = await driver.fetch_inbox_since(3600 * 24 * 365)
    assert rows, "expected at least some real sync_inbox rows within a year"
    for row in rows[:5]:
        assert isinstance(row["received_at"], str) and row["received_at"].endswith("Z")
        if row["applied_at"] is not None:
            assert isinstance(row["applied_at"], str) and row["applied_at"].endswith("Z")


async def test_postgresql_fetch_inbox_since_returns_normalised_iso_strings_s13():
    driver = PostgreSQLDriver(_postgresql_server(), _settings())
    rows = await driver.fetch_inbox_since(3600 * 24 * 365)
    assert rows, "expected at least some real sync_inbox rows within a year"
    for row in rows[:5]:
        assert isinstance(row["received_at"], str) and row["received_at"].endswith("Z")
        if row["applied_at"] is not None:
            assert isinstance(row["applied_at"], str) and row["applied_at"].endswith("Z")


async def test_mysql_and_postgresql_fetch_inbox_since_agree_on_wire_shape_s13():
    mysql_driver = MySQLDriver(_mysql_server(), _settings())
    pg_driver = PostgreSQLDriver(_postgresql_server(), _settings())
    mysql_rows = await mysql_driver.fetch_inbox_since(3600 * 24 * 365)
    pg_rows = await pg_driver.fetch_inbox_since(3600 * 24 * 365)
    assert mysql_rows and pg_rows
    assert type(mysql_rows[0]["received_at"]) is type(pg_rows[0]["received_at"]) is str


async def test_mysql_fetch_error_rows_returns_normalised_iso_strings_s13():
    from _fixtures import connections, rows as fixture_rows

    tag = "S13TEST"
    mid = fixture_rows.message_id("1001", "ORDER", f"{tag}-1", 1, 101)
    with connections.mysql() as conn:
        fixture_rows.insert_outbox(
            conn, fixture_rows.MYSQL, pipeline_id=101, entity_type="ORDER", entity_key=f"{tag}-1",
            operation="Insert", row_version=1, message_id=mid, session_id=f"ORDER:{tag}-1",
            payload={"id": f"{tag}-1"}, origin="1001", status="Failed",
            created_at_seconds_ago=10, sent_at_seconds_ago=None,
        )
    try:
        driver = MySQLDriver(_mysql_server(), _settings())
        rows = await driver.fetch_error_rows(limit=200)
        matching = [r for r in rows if r["message_id"] == mid]
        assert matching, "the seeded throwaway Failed row must itself be returned"
        for row in matching:
            assert isinstance(row["at"], str) and row["at"].endswith("Z")
    finally:
        with connections.mysql() as conn:
            fixture_rows.delete_by_tag(conn, fixture_rows.MYSQL, "outbox", tag)


@pytest.fixture
def mssql_tcp_server() -> ServerEntry:
    if not _mssql_tcp_reachable():
        pytest.skip(
            "MSSQL TCP/IP is not enabled on this dev machine (localhost:1433 "
            "unreachable) — see test_drivers_real_db.py's own module docstring. "
            "mssql.py/mssql_odbc.py's own logic is still fully covered by "
            "test_mssql_odbc.py and test_o12_auth_probe_ordering.py via mocking."
        )
    from _fixtures.connections import MSSQL_DATABASE

    return ServerEntry(
        id=1003, name="MSSQL", platform="mssql", host="localhost", port=1433,
        user="sb_monitor", password=_sb_monitor_password(), database=MSSQL_DATABASE,
    )


def _sb_monitor_password() -> str:
    from _fixtures.env import parse_env_file, CONFIG_MAKER_DIR

    return parse_env_file(CONFIG_MAKER_DIR / "monitor.env")["MONITOR_DB_PASSWORD"]


async def test_mssql_tedious_check_chain_ok_if_tcp_reachable(mssql_tcp_server):
    driver = MSSQLDriver(mssql_tcp_server, _settings())
    status = await driver.check_chain()
    assert status == ReadStatus.OK


async def test_mssql_odbc_check_chain_ok_if_tcp_reachable(mssql_tcp_server):
    from _db.mssql_odbc import MSSQLOdbcDriver

    server = mssql_tcp_server.model_copy(update={"driver": "odbc"})
    driver = MSSQLOdbcDriver(server, _settings())
    driver.begin_poll_cycle()
    status = await driver.check_chain()
    driver.end_poll_cycle()
    assert status == ReadStatus.OK
