from __future__ import annotations

import json
import uuid

MYSQL = "mysql"
POSTGRESQL = "postgresql"
MSSQL = "mssql"

_TABLES = {
    MYSQL: {"outbox": "sync_outbox", "inbox": "sync_inbox", "pipeline": "sync_pipeline"},
    POSTGRESQL: {
        "outbox": "sync.sync_outbox",
        "inbox": "sync.sync_inbox",
        "pipeline": "sync.sync_pipeline",
    },
    MSSQL: {"outbox": "dbo.SyncOutbox", "inbox": "dbo.SyncInbox", "pipeline": "dbo.SyncPipeline"},
}

_T_MINUS = {
    MYSQL: lambda n: f"DATE_SUB(UTC_TIMESTAMP(3), INTERVAL {n} SECOND)",
    POSTGRESQL: lambda n: f"(now() - interval '{n} seconds')",
    MSSQL: lambda n: f"DATEADD(SECOND, -{n}, SYSUTCDATETIME())",
}

_MSSQL_COLUMNS = {
    "pipeline_id": "PipelineId", "entity_type": "EntityType", "entity_key": "EntityKey", "operation": "Operation",
    "row_version": "RowVersion", "message_id": "MessageId", "session_id": "SessionId", "payload": "Payload",
    "origin": "Origin", "status": "Status", "created_at": "CreatedAt", "sent_at": "SentAt",
    "received_at": "ReceivedAt", "applied_at": "AppliedAt",
}


def _cols(platform: str, names: str) -> str:
    if platform != MSSQL:
        return names
    return ", ".join(_MSSQL_COLUMNS[n.strip()] for n in names.split(","))


_PLACEHOLDER = {MYSQL: "%s", POSTGRESQL: "%s", MSSQL: "?"}


def table(platform: str, name: str) -> str:
    return _TABLES[platform][name]


def t_minus(platform: str, seconds: float) -> str:
    return _T_MINUS[platform](seconds)


def new_uuid() -> str:
    return str(uuid.uuid4())


def message_id(origin: str, entity_type: str, entity_key: str, row_version: int, pipeline_id: int) -> str:
    return f"{origin}:{entity_type}:{entity_key}:{row_version}:p{pipeline_id}:{new_uuid()}"


def _payload_placeholder(platform: str) -> str:
    if platform == POSTGRESQL:
        return "%s::jsonb"
    return _PLACEHOLDER[platform]


def insert_outbox(
    conn,
    platform: str,
    *,
    pipeline_id: int,
    entity_type: str,
    entity_key: str,
    operation: str,
    row_version: int,
    message_id: str,
    session_id: str,
    payload: dict,
    origin: str,
    status: str,
    created_at_seconds_ago: float,
    sent_at_seconds_ago: float | None,
) -> None:
    ph = _PLACEHOLDER[platform]
    created_expr = t_minus(platform, created_at_seconds_ago)
    sent_expr = t_minus(platform, sent_at_seconds_ago) if sent_at_seconds_ago is not None else "NULL"
    sql = (
        f"INSERT INTO {table(platform, 'outbox')} "
        f"({_cols(platform, 'pipeline_id, entity_type, entity_key, operation, row_version, message_id, session_id, payload, origin, status, created_at, sent_at')}) VALUES "
        f"({ph},{ph},{ph},{ph},{ph},{ph},{ph},{_payload_placeholder(platform)},{ph},{ph},{created_expr},{sent_expr})"
    )
    params = (
        pipeline_id,
        entity_type,
        entity_key,
        operation,
        row_version,
        message_id,
        session_id,
        json.dumps(payload),
        origin,
        status,
    )
    _execute(conn, platform, sql, params)


def insert_inbox(
    conn,
    platform: str,
    *,
    pipeline_id: int,
    message_id: str,
    entity_type: str,
    entity_key: str,
    operation: str,
    row_version: int,
    payload: dict,
    status: str,
    received_at_seconds_ago: float,
    applied_at_seconds_ago: float | None,
) -> None:
    ph = _PLACEHOLDER[platform]
    received_expr = t_minus(platform, received_at_seconds_ago)
    applied_expr = t_minus(platform, applied_at_seconds_ago) if applied_at_seconds_ago is not None else "NULL"
    sql = (
        f"INSERT INTO {table(platform, 'inbox')} "
        f"({_cols(platform, 'pipeline_id, message_id, entity_type, entity_key, operation, row_version, payload, received_at, applied_at, status')}) VALUES "
        f"({ph},{ph},{ph},{ph},{ph},{ph},{_payload_placeholder(platform)},{received_expr},{applied_expr},{ph})"
    )
    params = (pipeline_id, message_id, entity_type, entity_key, operation, row_version, json.dumps(payload), status)
    _execute(conn, platform, sql, params)


def delete_by_tag(conn, platform: str, which: str, tag: str) -> int:
    ph = _PLACEHOLDER[platform]
    like = f"{tag}-%"
    sql = f"DELETE FROM {table(platform, which)} WHERE {_cols(platform, 'entity_key')} LIKE {ph}"
    cur = _execute(conn, platform, sql, (like,))
    return cur.rowcount if cur is not None else -1


def count_all(conn, platform: str, which: str) -> int:
    cur = _execute(conn, platform, f"SELECT COUNT(*) FROM {table(platform, which)}", ())
    row = cur.fetchone()
    return int(row[0])


def count_by_tag(conn, platform: str, which: str, tag: str) -> int:
    ph = _PLACEHOLDER[platform]
    like = f"{tag}-%"
    cur = _execute(conn, platform, f"SELECT COUNT(*) FROM {table(platform, which)} WHERE {_cols(platform, 'entity_key')} LIKE {ph}", (like,))
    return int(cur.fetchone()[0])


def _execute(conn, platform: str, sql: str, params: tuple):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur
