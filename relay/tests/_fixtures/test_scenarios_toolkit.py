from __future__ import annotations

import socket
import time

import pytest

from _fixtures import connections, rows


def _count_by_tag(platform: str, which: str, tag: str) -> int:
    conn_ctx = {"mysql": connections.mysql, "postgresql": connections.postgresql, "mssql": connections.mssql}[platform]
    with conn_ctx() as conn:
        return rows.count_by_tag(conn, platform, which, tag)


def test_fix01_matched_pair(mysql_scenario):
    facts = mysql_scenario("FIX01")
    assert _count_by_tag("mysql", "outbox", facts["tag"]) == 1
    assert _count_by_tag("postgresql", "inbox", facts["tag"]) == 1


def test_fix02_unmatched_within_window(postgresql_scenario):
    facts = postgresql_scenario("FIX02")
    assert _count_by_tag("postgresql", "outbox", facts["tag"]) == 1
    assert _count_by_tag("mysql", "inbox", facts["tag"]) == 0


def test_fix03_unmatched_past_window_queued(mssql_scenario):
    facts = mssql_scenario("FIX03")
    assert _count_by_tag("mssql", "outbox", facts["tag"]) == 1
    assert _count_by_tag("mysql", "inbox", facts["tag"]) == 0


def test_fix04_past_max_age_lost(mysql_scenario):
    facts = mysql_scenario("FIX04")
    assert _count_by_tag("mysql", "outbox", facts["tag"]) == 1
    assert _count_by_tag("mssql", "inbox", facts["tag"]) == 0


def test_fix05_degraded_backlog(postgresql_scenario):
    facts = postgresql_scenario("FIX05")
    assert facts["row_count"] == 120
    assert _count_by_tag("postgresql", "outbox", facts["tag"]) == 120


def test_fix06_down_stale_heartbeat(mssql_scenario):
    facts = mssql_scenario("FIX06")
    with connections.mssql() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT DATEDIFF(SECOND, LastPingAt, SYSUTCDATETIME()) FROM dbo.SyncPipeline "
            "WHERE PipelineId = ?",
            (facts["pipeline_id"],),
        )
        age_seconds = cur.fetchone()[0]
    assert age_seconds >= 30


@pytest.mark.elevated
def test_fix07a_db_down(mysql_scenario):
    facts = mysql_scenario("FIX07A")
    assert facts["service"] == "MySQL80"
    with pytest.raises(Exception):
        connections.mysql_connect()


def test_fix07b_bus_down(service_bus_scenario):
    import httpx

    facts = service_bus_scenario("FIX07B")
    assert facts["bus"] == 1
    with pytest.raises(httpx.HTTPError):
        httpx.get("http://localhost:5300/health", timeout=2.0)


def test_fix08_unread_timeout_vs_refused(mysql_scenario):
    facts = mysql_scenario("FIX08")
    refused = facts["refused_entry"]
    blackhole = facts["timeout_entry"]
    assert refused["host"] == "127.0.0.1"
    assert blackhole["host"] == "192.0.2.1"

    start = time.monotonic()
    with pytest.raises(ConnectionRefusedError):
        with socket.create_connection((refused["host"], refused["port"]), timeout=2.5):
            pass
    refused_elapsed = time.monotonic() - start
    assert refused_elapsed < 2.5, "refused connect should complete, not hang to the connect timeout"

    start = time.monotonic()
    with pytest.raises((socket.timeout, TimeoutError, OSError)):
        with socket.create_connection((blackhole["host"], blackhole["port"]), timeout=1.5):
            pass
    blackhole_elapsed = time.monotonic() - start
    assert blackhole_elapsed >= 1.0, "black-hole connect should block until the client timeout, not fail fast"


def test_fix09_quiet_but_unmatched(postgresql_scenario):
    facts = postgresql_scenario("FIX09")
    assert _count_by_tag("postgresql", "outbox", facts["tag"]) == 2
    assert _count_by_tag("mysql", "inbox", facts["tag"]) == 1


def test_fix10_two_bus_independence(mysql_scenario):
    import httpx

    facts = mysql_scenario("FIX10")
    assert httpx.get("http://localhost:5300/health", timeout=3.0).json()["status"] == "healthy"
    with pytest.raises(httpx.HTTPError):
        httpx.get("http://localhost:5301/health", timeout=2.0)
    assert _count_by_tag("mysql", "outbox", facts["tag"]) == 1
    assert _count_by_tag("mssql", "outbox", facts["tag"]) == 1


@pytest.mark.elevated
def test_fix11_bus_incident_then_receiver_down(mssql_scenario):
    import httpx

    from _fixtures import scenarios as scenarios_module

    facts = mssql_scenario("FIX11")

    with pytest.raises(httpx.HTTPError):
        httpx.get("http://localhost:5301/health", timeout=2.0)
    with connections.mysql() as conn:
        conn.ping(reconnect=False)

    scenarios_module.step2_stop_receiver(facts, min_wait_seconds=1.0)
    with pytest.raises(Exception):
        connections.mysql_connect()
    with pytest.raises(httpx.HTTPError):
        httpx.get("http://localhost:5301/health", timeout=2.0)


def test_row_counts_unchanged_by_a_full_toolkit_pass():
    from _fixtures import scenarios as scenarios_module

    def counts():
        with connections.mysql() as conn:
            m = (rows.count_all(conn, rows.MYSQL, "outbox"), rows.count_all(conn, rows.MYSQL, "inbox"))
        with connections.postgresql() as conn:
            p = (rows.count_all(conn, rows.POSTGRESQL, "outbox"), rows.count_all(conn, rows.POSTGRESQL, "inbox"))
        with connections.mssql() as conn:
            s = (rows.count_all(conn, rows.MSSQL, "outbox"), rows.count_all(conn, rows.MSSQL, "inbox"))
        return m, p, s

    before = counts()

    non_elevated = [c for c in scenarios_module.all_codes() if not scenarios_module.get(c).elevated]
    seeded = []
    try:
        for code in non_elevated:
            scn = scenarios_module.get(code)
            seeded.append((scn, scn.seed()))
    finally:
        for scn, facts in reversed(seeded):
            scn.teardown(facts)

    after = counts()
    assert before == after, f"row counts changed after a full seed+teardown pass: before={before} after={after}"
