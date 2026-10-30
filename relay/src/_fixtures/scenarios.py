from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable

from . import bus, connections, elevation, env, rows

MYSQL, POSTGRESQL, MSSQL = rows.MYSQL, rows.POSTGRESQL, rows.MSSQL


@dataclasses.dataclass
class Scenario:
    code: str
    title: str
    elevated: bool
    seed: Callable[[], dict[str, Any]]
    teardown: Callable[[dict[str, Any]], None]


_REGISTRY: dict[str, Scenario] = {}


def _guarded(fn: Callable) -> Callable:
    def _wrapper(*args, **kwargs):
        env.require_live_lab()
        return fn(*args, **kwargs)

    _wrapper.__name__ = getattr(fn, "__name__", "guarded")
    return _wrapper


def register(scn: Scenario) -> Scenario:
    scn.seed = _guarded(scn.seed)
    scn.teardown = _guarded(scn.teardown)
    _REGISTRY[scn.code] = scn
    return scn


def get(code: str) -> Scenario:
    return _REGISTRY[code]


def all_codes() -> list[str]:
    return sorted(_REGISTRY.keys())


def _base_payload(entity_key: str) -> dict:
    return {"id": entity_key, "note": "parity fixture toolkit seed row"}


def _seed_fix01() -> dict:
    tag = "FIX01"
    entity_key = f"{tag}-1"
    mid = rows.message_id("1001", "ORDER", entity_key, 1, 101)
    with connections.mysql() as conn:
        rows.insert_outbox(
            conn, MYSQL, pipeline_id=101, entity_type="ORDER", entity_key=entity_key,
            operation="Insert", row_version=1, message_id=mid, session_id=f"ORDER:{entity_key}",
            payload=_base_payload(entity_key), origin="1001", status="Sent",
            created_at_seconds_ago=60, sent_at_seconds_ago=58,
        )
    with connections.postgresql() as conn:
        rows.insert_inbox(
            conn, POSTGRESQL, pipeline_id=203, message_id=mid, entity_type="ORDER",
            entity_key=entity_key, operation="Insert", row_version=1,
            payload=_base_payload(entity_key), status="Applied",
            received_at_seconds_ago=55, applied_at_seconds_ago=54,
        )
    return {"tag": tag, "message_id": mid, "entity_key": entity_key}


def _teardown_fix01(facts: dict) -> None:
    with connections.mysql() as conn:
        rows.delete_by_tag(conn, MYSQL, "outbox", facts["tag"])
    with connections.postgresql() as conn:
        rows.delete_by_tag(conn, POSTGRESQL, "inbox", facts["tag"])


register(Scenario("FIX01", "Matched pair", False, _seed_fix01, _teardown_fix01))


def _seed_fix02() -> dict:
    tag = "FIX02"
    entity_key = f"{tag}-1"
    mid = rows.message_id("1002", "CUSTOMER", entity_key, 1, 201)
    with connections.postgresql() as conn:
        rows.insert_outbox(
            conn, POSTGRESQL, pipeline_id=201, entity_type="CUSTOMER", entity_key=entity_key,
            operation="Insert", row_version=1, message_id=mid, session_id=f"CUSTOMER:{entity_key}",
            payload=_base_payload(entity_key), origin="1002", status="Sent",
            created_at_seconds_ago=40, sent_at_seconds_ago=38,
        )
    return {"tag": tag, "message_id": mid, "entity_key": entity_key}


def _teardown_fix02(facts: dict) -> None:
    with connections.postgresql() as conn:
        rows.delete_by_tag(conn, POSTGRESQL, "outbox", facts["tag"])


register(Scenario("FIX02", "Unmatched, within E2E_WINDOW_SECONDS", False, _seed_fix02, _teardown_fix02))


def _seed_fix03() -> dict:
    tag = "FIX03"
    entity_key = f"{tag}-1"
    mid = rows.message_id("1003", "EMPLOYEE", entity_key, 1, 301)
    with connections.mssql() as conn:
        rows.insert_outbox(
            conn, MSSQL, pipeline_id=301, entity_type="EMPLOYEE", entity_key=entity_key,
            operation="Insert", row_version=1, message_id=mid, session_id=f"EMPLOYEE:{entity_key}",
            payload=_base_payload(entity_key), origin="1003", status="Sent",
            created_at_seconds_ago=400, sent_at_seconds_ago=398,
        )
    return {"tag": tag, "message_id": mid, "entity_key": entity_key}


def _teardown_fix03(facts: dict) -> None:
    with connections.mssql() as conn:
        rows.delete_by_tag(conn, MSSQL, "outbox", facts["tag"])


register(Scenario("FIX03", "Unmatched past window, queued", False, _seed_fix03, _teardown_fix03))


def _seed_fix04() -> dict:
    tag = "FIX04"
    entity_key = f"{tag}-1"
    mid = rows.message_id("1001", "ORDER", entity_key, 1, 102)
    with connections.mysql() as conn:
        rows.insert_outbox(
            conn, MYSQL, pipeline_id=102, entity_type="ORDER", entity_key=entity_key,
            operation="Insert", row_version=1, message_id=mid, session_id=f"ORDER:{entity_key}",
            payload=_base_payload(entity_key), origin="1001", status="Sent",
            created_at_seconds_ago=4000, sent_at_seconds_ago=3998,
        )
    return {"tag": tag, "message_id": mid, "entity_key": entity_key}


def _teardown_fix04(facts: dict) -> None:
    with connections.mysql() as conn:
        rows.delete_by_tag(conn, MYSQL, "outbox", facts["tag"])


register(Scenario("FIX04", "Past QUEUED_MAX_AGE_SECONDS, lost", False, _seed_fix04, _teardown_fix04))


def _seed_fix05() -> dict:
    tag = "FIX05"
    entity_keys = [f"{tag}-{n}" for n in range(1, 121)]
    message_ids = []
    with connections.postgresql() as conn:
        for n, entity_key in enumerate(entity_keys, start=1):
            mid = rows.message_id("1002", "CUSTOMER", entity_key, 1, 202)
            message_ids.append(mid)
            rows.insert_outbox(
                conn, POSTGRESQL, pipeline_id=202, entity_type="CUSTOMER", entity_key=entity_key,
                operation="Insert", row_version=1, message_id=mid, session_id=f"CUSTOMER:{entity_key}",
                payload=_base_payload(entity_key), origin="1002", status="Sent",
                created_at_seconds_ago=250, sent_at_seconds_ago=250,
            )
    return {"tag": tag, "message_ids": message_ids, "row_count": len(entity_keys)}


def _teardown_fix05(facts: dict) -> None:
    with connections.postgresql() as conn:
        rows.delete_by_tag(conn, POSTGRESQL, "outbox", facts["tag"])


register(Scenario("FIX05", "Degraded (120-row backlog)", False, _seed_fix05, _teardown_fix05))


def _seed_fix06() -> dict:
    for needle in ("sender_relay.py", "receiver_relay.py"):
        if elevation.is_process_running(needle):
            raise RuntimeError(
                f"FIX06 precondition failed: a process matching {needle!r} is running "
                "-- it would overwrite sync_pipeline.last_ping_at out from under this "
                "scenario. Stop the lab's mssql sender/receiver before running FIX06."
            )
    with connections.mssql() as conn:
        cur = conn.cursor()
        cur.execute("SELECT LastPingAt FROM dbo.SyncPipeline WHERE PipelineId = 302")
        prior = cur.fetchone()[0]
        cur.execute(
            "UPDATE dbo.SyncPipeline SET LastPingAt = DATEADD(SECOND, -45, SYSUTCDATETIME()) "
            "WHERE PipelineId = 302"
        )
    return {"pipeline_id": 302, "prior_last_ping_at": prior}


def _teardown_fix06(facts: dict) -> None:
    with connections.mssql() as conn:
        cur = conn.cursor()
        if facts.get("prior_last_ping_at") is not None:
            cur.execute(
                "UPDATE dbo.SyncPipeline SET LastPingAt = ? WHERE PipelineId = ?",
                (facts["prior_last_ping_at"], facts["pipeline_id"]),
            )
        else:
            cur.execute(
                "UPDATE dbo.SyncPipeline SET LastPingAt = SYSUTCDATETIME() WHERE PipelineId = ?",
                (facts["pipeline_id"],),
            )


register(Scenario("FIX06", "Down (stale heartbeat)", False, _seed_fix06, _teardown_fix06))


def _seed_fix07a() -> dict:
    elevation.require_elevated("FIX07A (Stop-Service MySQL80)")
    elevation.stop_service("MySQL80")
    return {"service": "MySQL80"}


def _teardown_fix07a(facts: dict) -> None:
    elevation.start_service(facts["service"])

    def _ready() -> bool:
        with connections.mysql() as conn:
            conn.ping(reconnect=False)
        return True

    elevation.wait_until(_ready, timeout_seconds=60, interval_seconds=2, description="MySQL80 real connect")


register(Scenario("FIX07A", "DB-down (Stop-Service MySQL80)", True, _seed_fix07a, _teardown_fix07a))


def _seed_fix07b() -> dict:
    bus.outage_start(1)
    return {"bus": 1}


def _teardown_fix07b(facts: dict) -> None:
    bus.outage_end(facts["bus"])


register(Scenario("FIX07B", "Bus-down (bus 1)", False, _seed_fix07b, _teardown_fix07b))


def _find_unused_loopback_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return port


def _seed_fix08() -> dict:
    from . import blackhole

    refused_port = _find_unused_loopback_port()
    refused_entry = blackhole.unused_loopback_server_entry("mysql", refused_port)
    timeout_entry = blackhole.blackhole_server_entry("mysql")
    return {"refused_entry": refused_entry, "timeout_entry": timeout_entry}


def _teardown_fix08(facts: dict) -> None:
    return None


register(Scenario("FIX08", "unread: timeout vs refused", False, _seed_fix08, _teardown_fix08))


def _seed_fix09() -> dict:
    tag = "FIX09"
    old_key, recent_key = f"{tag}-OLD", f"{tag}-RECENT"
    mid_old = rows.message_id("1002", "CUSTOMER", old_key, 1, 201)
    mid_recent = rows.message_id("1002", "CUSTOMER", recent_key, 1, 201)
    with connections.postgresql() as conn:
        rows.insert_outbox(
            conn, POSTGRESQL, pipeline_id=201, entity_type="CUSTOMER", entity_key=old_key,
            operation="Insert", row_version=1, message_id=mid_old, session_id=f"CUSTOMER:{old_key}",
            payload=_base_payload(old_key), origin="1002", status="Sent",
            created_at_seconds_ago=4000, sent_at_seconds_ago=4000,
        )
        rows.insert_outbox(
            conn, POSTGRESQL, pipeline_id=201, entity_type="CUSTOMER", entity_key=recent_key,
            operation="Insert", row_version=1, message_id=mid_recent, session_id=f"CUSTOMER:{recent_key}",
            payload=_base_payload(recent_key), origin="1002", status="Sent",
            created_at_seconds_ago=30, sent_at_seconds_ago=30,
        )
    with connections.mysql() as conn:
        rows.insert_inbox(
            conn, MYSQL, pipeline_id=103, message_id=mid_recent, entity_type="CUSTOMER",
            entity_key=recent_key, operation="Insert", row_version=1,
            payload=_base_payload(recent_key), status="Applied",
            received_at_seconds_ago=25, applied_at_seconds_ago=24,
        )
    return {"tag": tag, "message_id_old": mid_old, "message_id_recent": mid_recent}


def _teardown_fix09(facts: dict) -> None:
    with connections.postgresql() as conn:
        rows.delete_by_tag(conn, POSTGRESQL, "outbox", facts["tag"])
    with connections.mysql() as conn:
        rows.delete_by_tag(conn, MYSQL, "inbox", facts["tag"])


register(Scenario("FIX09", "Quiet-but-still-unmatched", False, _seed_fix09, _teardown_fix09))


def _seed_fix10() -> dict:
    tag = "FIX10"
    out_key, in_key = f"{tag}-OUT", f"{tag}-IN"
    mid_out = rows.message_id("1001", "ORDER", out_key, 1, 101)
    mid_in = rows.message_id("1003", "EMPLOYEE", in_key, 1, 301)
    with connections.mysql() as conn:
        rows.insert_outbox(
            conn, MYSQL, pipeline_id=101, entity_type="ORDER", entity_key=out_key,
            operation="Insert", row_version=1, message_id=mid_out, session_id=f"ORDER:{out_key}",
            payload=_base_payload(out_key), origin="1001", status="Sent",
            created_at_seconds_ago=30, sent_at_seconds_ago=30,
        )
    with connections.mssql() as conn:
        rows.insert_outbox(
            conn, MSSQL, pipeline_id=301, entity_type="EMPLOYEE", entity_key=in_key,
            operation="Insert", row_version=1, message_id=mid_in, session_id=f"EMPLOYEE:{in_key}",
            payload=_base_payload(in_key), origin="1003", status="Sent",
            created_at_seconds_ago=30, sent_at_seconds_ago=30,
        )
    bus.outage_start(2)
    return {"tag": tag, "message_id_out": mid_out, "message_id_in": mid_in, "bus": 2}


def _teardown_fix10(facts: dict) -> None:
    try:
        bus.outage_end(facts["bus"])
    finally:
        with connections.mysql() as conn:
            rows.delete_by_tag(conn, MYSQL, "outbox", facts["tag"])
        with connections.mssql() as conn:
            rows.delete_by_tag(conn, MSSQL, "outbox", facts["tag"])


register(Scenario("FIX10", "Two-bus independence", False, _seed_fix10, _teardown_fix10))


def _seed_fix11() -> dict:
    elevation.require_elevated("FIX11 step 2 (Stop-Service MySQL80)")
    tag = "FIX11"
    entity_key = f"{tag}-1"
    mid = rows.message_id("1003", "EMPLOYEE", entity_key, 1, 301)
    with connections.mssql() as conn:
        rows.insert_outbox(
            conn, MSSQL, pipeline_id=301, entity_type="EMPLOYEE", entity_key=entity_key,
            operation="Insert", row_version=1, message_id=mid, session_id=f"EMPLOYEE:{entity_key}",
            payload=_base_payload(entity_key), origin="1003", status="Sent",
            created_at_seconds_ago=200, sent_at_seconds_ago=200,
        )
    bus.outage_start(2)
    step1_ts = time.monotonic()
    return {"tag": tag, "message_id": mid, "bus": 2, "service": "MySQL80", "step1_ts": step1_ts, "step2_done": False}


def step2_stop_receiver(facts: dict, min_wait_seconds: float = 30.0) -> dict:
    elapsed = time.monotonic() - facts["step1_ts"]
    if elapsed < min_wait_seconds:
        time.sleep(min_wait_seconds - elapsed)
    elevation.stop_service(facts["service"])
    facts["step2_done"] = True
    return facts


def _teardown_fix11(facts: dict) -> None:
    try:
        if facts.get("step2_done"):
            elevation.start_service(facts["service"])

            def _ready() -> bool:
                with connections.mysql() as conn:
                    conn.ping(reconnect=False)
                return True

            elevation.wait_until(_ready, timeout_seconds=60, interval_seconds=2, description="MySQL80 real connect")
    finally:
        try:
            bus.outage_end(facts["bus"])
        finally:
            with connections.mssql() as conn:
                rows.delete_by_tag(conn, MSSQL, "outbox", facts["tag"])


register(Scenario("FIX11", "Bus-incident-then-receiver-down", True, _seed_fix11, _teardown_fix11))
