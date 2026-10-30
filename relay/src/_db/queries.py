from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    sql: str
    params: tuple


class MySQLQueries:

    platform = "mysql"

    def select_1(self) -> Query:
        return Query("SELECT 1", ())

    def registry_signature(self) -> Query:
        return Query(
            "SELECT COUNT(*) cnt, MAX(pipeline_id) maxId FROM sync_pipeline",
            (),
        )

    def registry_signature_triggers(self) -> Query:
        return Query(
            "SELECT COUNT(*) cnt, MAX(pipeline_id) maxId FROM sync_triggers",
            (),
        )

    def pipelines_with_metrics(self) -> Query:
        return Query(
            "SELECT "
            "pipeline_id AS pipelineId, name, source_system AS sourceSystem, "
            "target_system AS targetSystem, entity_type AS entityType, "
            "queue_or_topic AS queueOrTopic, direction, is_enabled AS isEnabled, "
            "owner, last_ping_at AS lastPingAt, "
            "CASE WHEN last_ping_at IS NULL THEN NULL "
            "ELSE TIMESTAMPDIFF(SECOND, last_ping_at, UTC_TIMESTAMP(3)) END AS heartbeatAgeSeconds, "
            "(SELECT COUNT(*) FROM sync_outbox o WHERE o.pipeline_id = p.pipeline_id "
            "AND o.status='Pending') AS pendingOutbox, "
            "(SELECT COUNT(*) FROM sync_outbox o WHERE o.pipeline_id = p.pipeline_id "
            "AND o.status='Failed') AS failedOutbox, "
            "(SELECT COUNT(*) FROM sync_inbox i WHERE i.pipeline_id = p.pipeline_id "
            "AND i.status='Received') AS pendingInbox, "
            "(SELECT COUNT(*) FROM sync_inbox i WHERE i.pipeline_id = p.pipeline_id "
            "AND i.status IN ('Error','Failed')) AS unresolvedInbox, "
            "(SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, sent_at)) FROM sync_outbox o "
            "WHERE o.pipeline_id = p.pipeline_id AND o.sent_at IS NOT NULL "
            "AND o.sent_at >= UTC_TIMESTAMP(3) - INTERVAL 15 MINUTE) AS sendLagSeconds, "
            "(SELECT AVG(TIMESTAMPDIFF(SECOND, received_at, applied_at)) FROM sync_inbox i "
            "WHERE i.pipeline_id = p.pipeline_id AND i.applied_at IS NOT NULL "
            "AND i.applied_at >= UTC_TIMESTAMP(3) - INTERVAL 15 MINUTE) AS applyLagSeconds, "
            "(SELECT TIMESTAMPDIFF(SECOND, MIN(created_at), UTC_TIMESTAMP(3)) FROM sync_outbox o "
            "WHERE o.pipeline_id = p.pipeline_id AND o.status='Pending') AS oldestPendingOutboxSeconds, "
            "(SELECT TIMESTAMPDIFF(SECOND, MIN(received_at), UTC_TIMESTAMP(3)) FROM sync_inbox i "
            "WHERE i.pipeline_id = p.pipeline_id AND i.status='Received') AS oldestPendingInboxSeconds "
            "FROM sync_pipeline p "
            "WHERE p.is_enabled IS NULL OR p.is_enabled <> 0",
            (),
        )

    def triggers(self) -> Query:
        return Query(
            "SELECT pipeline_id, entity, direction, operation, `function`, format, severity "
            "FROM sync_triggers",
            (),
        )

    def outbox_sent(self, scan_window_seconds: int) -> Query:
        return Query(
            "SELECT pipeline_id, message_id, origin, entity_type, sent_at, created_at "
            "FROM sync_outbox WHERE status='Sent' AND sent_at >= "
            "UTC_TIMESTAMP(3) - INTERVAL %s SECOND",
            (scan_window_seconds,),
        )

    def outbox_entity_agg(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = (
            "SELECT entity_type, "
            "SUM(CASE WHEN status='Pending' THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN status='Sent' THEN 1 ELSE 0 END) AS sent, "
            "SUM(CASE WHEN status='Failed' THEN 1 ELSE 0 END) AS failed "
            "FROM sync_outbox"
        )
        params: tuple = ()
        if pipeline_ids:
            placeholders = ",".join(["%s"] * len(pipeline_ids))
            sql += f" WHERE pipeline_id IN ({placeholders})"
            params = tuple(pipeline_ids)
        sql += " GROUP BY entity_type"
        return Query(sql, params)

    def inbox_entity_agg(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = (
            "SELECT entity_type, "
            "SUM(CASE WHEN status='Received' THEN 1 ELSE 0 END) AS received, "
            "SUM(CASE WHEN status='Applied' THEN 1 ELSE 0 END) AS applied, "
            "SUM(CASE WHEN status IN ('Error','Failed') THEN 1 ELSE 0 END) AS unresolved "
            "FROM sync_inbox"
        )
        params: tuple = ()
        if pipeline_ids:
            placeholders = ",".join(["%s"] * len(pipeline_ids))
            sql += f" WHERE pipeline_id IN ({placeholders})"
            params = tuple(pipeline_ids)
        sql += " GROUP BY entity_type"
        return Query(sql, params)

    def inbox_entity_last_error(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = (
            "SELECT entity_type, message_id, received_at, error "
            "FROM sync_inbox WHERE status IN ('Error','Failed')"
        )
        params: tuple = ()
        if pipeline_ids:
            placeholders = ",".join(["%s"] * len(pipeline_ids))
            sql += f" AND pipeline_id IN ({placeholders})"
            params = tuple(pipeline_ids)
        sql += " ORDER BY received_at DESC"
        return Query(sql, params)

    def inbox_index(self, message_ids: tuple[str, ...]) -> Query:
        placeholders = ",".join(["%s"] * len(message_ids))
        return Query(
            f"SELECT message_id, applied_at FROM sync_inbox WHERE message_id IN ({placeholders})",
            tuple(message_ids),
        )

    def inbox_since(self, window_seconds: int) -> Query:
        return Query(
            "SELECT message_id, pipeline_id, received_at, applied_at, status "
            "FROM sync_inbox WHERE received_at >= UTC_TIMESTAMP(3) - INTERVAL %s SECOND",
            (window_seconds,),
        )

    def error_rows(self, limit: int) -> Query:
        return Query(
            "SELECT * FROM ("
            "SELECT CONCAT('outbox:', outbox_id) AS id, pipeline_id, message_id, "
            "last_error AS error, created_at AS at FROM sync_outbox WHERE status='Failed' "
            "UNION ALL "
            "SELECT CONCAT('inbox:', inbox_id) AS id, pipeline_id, message_id, "
            "error, received_at AS at FROM sync_inbox WHERE status IN ('Error','Failed')"
            ") u ORDER BY at DESC LIMIT %s",
            (limit,),
        )

    def existing_routine_names(self, routine_schema: str) -> Query:
        return Query(
            "SELECT ROUTINE_NAME FROM information_schema.routines WHERE ROUTINE_SCHEMA=%s",
            (routine_schema,),
        )


class PostgreSQLQueries:

    platform = "postgresql"

    def __init__(self, schema: str = "sync"):
        self.schema = schema

    def _t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def select_1(self) -> Query:
        return Query("SELECT 1", ())

    def registry_signature(self) -> Query:
        return Query(f"SELECT COUNT(*) cnt, MAX(pipeline_id) \"maxId\" FROM {self._t('sync_pipeline')}", ())

    def registry_signature_triggers(self) -> Query:
        return Query(f"SELECT COUNT(*) cnt, MAX(pipeline_id) \"maxId\" FROM {self._t('sync_triggers')}", ())

    def pipelines_with_metrics(self) -> Query:
        p = self._t("sync_pipeline")
        o = self._t("sync_outbox")
        i = self._t("sync_inbox")
        return Query(
            f"SELECT p.pipeline_id AS \"pipelineId\", p.name, p.source_system AS \"sourceSystem\", "
            f"p.target_system AS \"targetSystem\", p.entity_type AS \"entityType\", "
            f"p.queue_or_topic AS \"queueOrTopic\", p.direction, p.is_enabled AS \"isEnabled\", "
            f"p.owner, p.last_ping_at AS \"lastPingAt\", "
            f"CASE WHEN p.last_ping_at IS NULL THEN NULL "
            f"ELSE EXTRACT(EPOCH FROM (now() - p.last_ping_at))::int END AS \"heartbeatAgeSeconds\", "
            f"(SELECT COUNT(*) FROM {o} o WHERE o.pipeline_id=p.pipeline_id AND o.status='Pending') AS \"pendingOutbox\", "
            f"(SELECT COUNT(*) FROM {o} o WHERE o.pipeline_id=p.pipeline_id AND o.status='Failed') AS \"failedOutbox\", "
            f"(SELECT COUNT(*) FROM {i} i WHERE i.pipeline_id=p.pipeline_id AND i.status='Received') AS \"pendingInbox\", "
            f"(SELECT COUNT(*) FROM {i} i WHERE i.pipeline_id=p.pipeline_id AND i.status IN ('Error','Failed')) AS \"unresolvedInbox\", "
            f"(SELECT AVG(EXTRACT(EPOCH FROM (o.sent_at - o.created_at))) FROM {o} o "
            f"WHERE o.pipeline_id=p.pipeline_id AND o.sent_at IS NOT NULL AND o.sent_at >= now() - interval '15 minutes') AS \"sendLagSeconds\", "
            f"(SELECT AVG(EXTRACT(EPOCH FROM (i.applied_at - i.received_at))) FROM {i} i "
            f"WHERE i.pipeline_id=p.pipeline_id AND i.applied_at IS NOT NULL AND i.applied_at >= now() - interval '15 minutes') AS \"applyLagSeconds\", "
            f"(SELECT EXTRACT(EPOCH FROM (now() - MIN(o.created_at)))::int FROM {o} o "
            f"WHERE o.pipeline_id=p.pipeline_id AND o.status='Pending') AS \"oldestPendingOutboxSeconds\", "
            f"(SELECT EXTRACT(EPOCH FROM (now() - MIN(i.received_at)))::int FROM {i} i "
            f"WHERE i.pipeline_id=p.pipeline_id AND i.status='Received') AS \"oldestPendingInboxSeconds\" "
            f"FROM {p} p "
            "WHERE p.is_enabled IS NOT FALSE",
            (),
        )

    def triggers(self) -> Query:
        return Query(
            f"SELECT pipeline_id, entity, direction, operation, function, format, severity "
            f"FROM {self._t('sync_triggers')}",
            (),
        )

    def outbox_sent(self, scan_window_seconds: int) -> Query:
        return Query(
            f"SELECT pipeline_id, message_id, origin, entity_type, sent_at, created_at "
            f"FROM {self._t('sync_outbox')} WHERE status='Sent' AND sent_at >= now() - (%s * interval '1 second')",
            (scan_window_seconds,),
        )

    def outbox_entity_agg(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = (
            f"SELECT entity_type, "
            f"SUM(CASE WHEN status='Pending' THEN 1 ELSE 0 END) AS pending, "
            f"SUM(CASE WHEN status='Sent' THEN 1 ELSE 0 END) AS sent, "
            f"SUM(CASE WHEN status='Failed' THEN 1 ELSE 0 END) AS failed "
            f"FROM {self._t('sync_outbox')}"
        )
        params: tuple = ()
        if pipeline_ids:
            sql += " WHERE pipeline_id = ANY(%s)"
            params = (list(pipeline_ids),)
        sql += " GROUP BY entity_type"
        return Query(sql, params)

    def inbox_entity_agg(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = (
            f"SELECT entity_type, "
            f"SUM(CASE WHEN status='Received' THEN 1 ELSE 0 END) AS received, "
            f"SUM(CASE WHEN status='Applied' THEN 1 ELSE 0 END) AS applied, "
            f"SUM(CASE WHEN status IN ('Error','Failed') THEN 1 ELSE 0 END) AS unresolved "
            f"FROM {self._t('sync_inbox')}"
        )
        params: tuple = ()
        if pipeline_ids:
            sql += " WHERE pipeline_id = ANY(%s)"
            params = (list(pipeline_ids),)
        sql += " GROUP BY entity_type"
        return Query(sql, params)

    def inbox_entity_last_error(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = f"SELECT entity_type, message_id, received_at, error FROM {self._t('sync_inbox')} WHERE status IN ('Error','Failed')"
        params: tuple = ()
        if pipeline_ids:
            sql += " AND pipeline_id = ANY(%s)"
            params = (list(pipeline_ids),)
        sql += " ORDER BY received_at DESC"
        return Query(sql, params)

    def inbox_index(self, message_ids: tuple[str, ...]) -> Query:
        return Query(
            f"SELECT message_id, applied_at FROM {self._t('sync_inbox')} WHERE message_id = ANY(%s)",
            (list(message_ids),),
        )

    def inbox_since(self, window_seconds: int) -> Query:
        return Query(
            f"SELECT message_id, pipeline_id, received_at, applied_at, status "
            f"FROM {self._t('sync_inbox')} WHERE received_at >= now() - (%s * interval '1 second')",
            (window_seconds,),
        )

    def error_rows(self, limit: int) -> Query:
        o = self._t("sync_outbox")
        i = self._t("sync_inbox")
        return Query(
            f"SELECT * FROM ("
            f"SELECT 'outbox:' || outbox_id AS id, pipeline_id, message_id, last_error AS error, created_at AS at "
            f"FROM {o} WHERE status='Failed' "
            f"UNION ALL "
            f"SELECT 'inbox:' || inbox_id AS id, pipeline_id, message_id, error, received_at AS at "
            f"FROM {i} WHERE status IN ('Error','Failed')"
            f") u ORDER BY at DESC LIMIT %s",
            (limit,),
        )

    def existing_routine_names(self) -> Query:
        return Query(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')",
            (),
        )


class MSSQLQueries:

    platform = "mssql"

    def select_1(self) -> Query:
        return Query("SELECT 1 AS ok", ())

    def registry_signature(self) -> Query:
        return Query("SELECT COUNT(*) cnt, MAX(PipelineId) maxId FROM dbo.SyncPipeline", ())

    def registry_signature_triggers(self) -> Query:
        return Query("SELECT COUNT(*) cnt, MAX(PipelineId) maxId FROM dbo.SyncTriggers", ())

    def pipelines_with_metrics(self) -> Query:
        return Query(
            "SELECT p.PipelineId AS pipelineId, p.Name AS name, p.SourceSystem AS sourceSystem, "
            "p.TargetSystem AS targetSystem, p.EntityType AS entityType, "
            "p.QueueOrTopic AS queueOrTopic, p.Direction AS direction, p.IsEnabled AS isEnabled, "
            "p.Owner AS owner, p.LastPingAt AS lastPingAt, "
            "CASE WHEN p.LastPingAt IS NULL THEN NULL "
            "ELSE DATEDIFF(SECOND, p.LastPingAt, SYSUTCDATETIME()) END AS heartbeatAgeSeconds, "
            "(SELECT COUNT(*) FROM dbo.SyncOutbox o WHERE o.PipelineId=p.PipelineId AND o.Status='Pending') AS pendingOutbox, "
            "(SELECT COUNT(*) FROM dbo.SyncOutbox o WHERE o.PipelineId=p.PipelineId AND o.Status='Failed') AS failedOutbox, "
            "(SELECT COUNT(*) FROM dbo.SyncInbox i WHERE i.PipelineId=p.PipelineId AND i.Status='Received') AS pendingInbox, "
            "(SELECT COUNT(*) FROM dbo.SyncInbox i WHERE i.PipelineId=p.PipelineId AND i.Status IN ('Error','Failed')) AS unresolvedInbox, "
            "(SELECT AVG(DATEDIFF(SECOND, o.CreatedAt, o.SentAt)*1.0) FROM dbo.SyncOutbox o "
            "WHERE o.PipelineId=p.PipelineId AND o.SentAt IS NOT NULL AND o.SentAt >= DATEADD(MINUTE,-15,SYSUTCDATETIME())) AS sendLagSeconds, "
            "(SELECT AVG(DATEDIFF(SECOND, i.ReceivedAt, i.AppliedAt)*1.0) FROM dbo.SyncInbox i "
            "WHERE i.PipelineId=p.PipelineId AND i.AppliedAt IS NOT NULL AND i.AppliedAt >= DATEADD(MINUTE,-15,SYSUTCDATETIME())) AS applyLagSeconds, "
            "(SELECT DATEDIFF(SECOND, MIN(o.CreatedAt), SYSUTCDATETIME()) FROM dbo.SyncOutbox o "
            "WHERE o.PipelineId=p.PipelineId AND o.Status='Pending') AS oldestPendingOutboxSeconds, "
            "(SELECT DATEDIFF(SECOND, MIN(i.ReceivedAt), SYSUTCDATETIME()) FROM dbo.SyncInbox i "
            "WHERE i.PipelineId=p.PipelineId AND i.Status='Received') AS oldestPendingInboxSeconds "
            "FROM dbo.SyncPipeline p "
            "WHERE p.IsEnabled IS NULL OR p.IsEnabled <> 0",
            (),
        )

    def triggers(self) -> Query:
        return Query(
            "SELECT PipelineId AS pipeline_id, Entity AS entity, Direction AS direction, Operation AS operation, "
            "FunctionName AS [function], PayloadFormat AS format, Severity AS severity "
            "FROM dbo.SyncTriggers",
            (),
        )

    def outbox_sent(self, scan_window_seconds: int) -> Query:
        return Query(
            "SELECT PipelineId AS pipeline_id, MessageId AS message_id, Origin AS origin, EntityType AS entity_type, "
            "SentAt AS sent_at, CreatedAt AS created_at "
            "FROM dbo.SyncOutbox WHERE Status='Sent' AND SentAt >= DATEADD(SECOND, -?, SYSUTCDATETIME())",
            (scan_window_seconds,),
        )

    def outbox_sent_literal(self, scan_window_seconds: int) -> Query:
        n = int(scan_window_seconds)
        return Query(
            f"SELECT PipelineId AS pipeline_id, MessageId AS message_id, Origin AS origin, EntityType AS entity_type, "
            f"SentAt AS sent_at, CreatedAt AS created_at "
            f"FROM dbo.SyncOutbox WHERE Status='Sent' AND SentAt >= DATEADD(SECOND, -{n}, SYSUTCDATETIME())",
            (),
        )

    def outbox_entity_agg(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = (
            "SELECT EntityType AS entity_type, "
            "SUM(CASE WHEN Status='Pending' THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN Status='Sent' THEN 1 ELSE 0 END) AS sent, "
            "SUM(CASE WHEN Status='Failed' THEN 1 ELSE 0 END) AS failed "
            "FROM dbo.SyncOutbox"
        )
        params: tuple = ()
        if pipeline_ids:
            placeholders = ",".join("?" * len(pipeline_ids))
            sql += f" WHERE PipelineId IN ({placeholders})"
            params = tuple(pipeline_ids)
        sql += " GROUP BY EntityType"
        return Query(sql, params)

    def inbox_entity_agg(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = (
            "SELECT EntityType AS entity_type, "
            "SUM(CASE WHEN Status='Received' THEN 1 ELSE 0 END) AS received, "
            "SUM(CASE WHEN Status='Applied' THEN 1 ELSE 0 END) AS applied, "
            "SUM(CASE WHEN Status IN ('Error','Failed') THEN 1 ELSE 0 END) AS unresolved "
            "FROM dbo.SyncInbox"
        )
        params: tuple = ()
        if pipeline_ids:
            placeholders = ",".join("?" * len(pipeline_ids))
            sql += f" WHERE PipelineId IN ({placeholders})"
            params = tuple(pipeline_ids)
        sql += " GROUP BY EntityType"
        return Query(sql, params)

    def inbox_entity_last_error(self, pipeline_ids: tuple[int, ...] | None) -> Query:
        sql = (
            "SELECT EntityType AS entity_type, MessageId AS message_id, ReceivedAt AS received_at, Error AS error "
            "FROM dbo.SyncInbox WHERE Status IN ('Error','Failed')"
        )
        params: tuple = ()
        if pipeline_ids:
            placeholders = ",".join("?" * len(pipeline_ids))
            sql += f" AND PipelineId IN ({placeholders})"
            params = tuple(pipeline_ids)
        sql += " ORDER BY ReceivedAt DESC"
        return Query(sql, params)

    def inbox_index_chunks(self, message_ids: tuple[str, ...], chunk_size: int = 1000) -> list[Query]:
        chunks: list[Query] = []
        ids = list(message_ids)
        for start in range(0, len(ids), chunk_size):
            batch = ids[start : start + chunk_size]
            placeholders = ",".join("?" * len(batch))
            chunks.append(
                Query(
                    f"SELECT MessageId AS message_id, AppliedAt AS applied_at FROM dbo.SyncInbox WHERE MessageId IN ({placeholders})",
                    tuple(batch),
                )
            )
        return chunks or [Query("SELECT MessageId AS message_id, AppliedAt AS applied_at FROM dbo.SyncInbox WHERE 1=0", ())]

    def inbox_index_unchunked(self, message_ids: tuple[str, ...]) -> Query:
        escaped = ",".join("'" + mid.replace("'", "''") + "'" for mid in message_ids)
        return Query(
            f"SELECT MessageId AS message_id, AppliedAt AS applied_at FROM dbo.SyncInbox WHERE MessageId IN ({escaped})", ()
        )

    def inbox_since(self, window_seconds: int) -> Query:
        return Query(
            "SELECT MessageId AS message_id, PipelineId AS pipeline_id, ReceivedAt AS received_at, "
            "AppliedAt AS applied_at, Status AS status "
            "FROM dbo.SyncInbox WHERE ReceivedAt >= DATEADD(SECOND, -?, SYSUTCDATETIME())",
            (window_seconds,),
        )

    def error_rows(self, limit: int) -> Query:
        return Query(
            "SELECT TOP (?) * FROM ("
            "SELECT 'outbox:' + CAST(OutboxId AS varchar(32)) AS id, PipelineId AS pipeline_id, MessageId AS message_id, "
            "LastError AS error, CreatedAt AS at FROM dbo.SyncOutbox WHERE Status='Failed' "
            "UNION ALL "
            "SELECT 'inbox:' + CAST(InboxId AS varchar(32)) AS id, PipelineId AS pipeline_id, MessageId AS message_id, "
            "Error AS error, ReceivedAt AS at FROM dbo.SyncInbox WHERE Status IN ('Error','Failed')"
            ") u ORDER BY at DESC",
            (limit,),
        )

    def error_rows_batch_partitioned(self, per_pipeline_limit: int) -> Query:
        return Query(
            "SELECT id, pipeline_id, message_id, error, at FROM ("
            "SELECT 'outbox:' + CAST(OutboxId AS varchar(32)) AS id, PipelineId AS pipeline_id, MessageId AS message_id, "
            "LastError AS error, CreatedAt AS at, 'outbox' AS tbl, "
            "ROW_NUMBER() OVER (PARTITION BY PipelineId, 'outbox' ORDER BY CreatedAt DESC) AS rn "
            "FROM dbo.SyncOutbox WHERE Status='Failed' "
            "UNION ALL "
            "SELECT 'inbox:' + CAST(InboxId AS varchar(32)) AS id, PipelineId AS pipeline_id, MessageId AS message_id, "
            "Error AS error, ReceivedAt AS at, 'inbox' AS tbl, "
            "ROW_NUMBER() OVER (PARTITION BY PipelineId, 'inbox' ORDER BY ReceivedAt DESC) AS rn "
            "FROM dbo.SyncInbox WHERE Status IN ('Error','Failed')"
            f") u WHERE rn <= {int(per_pipeline_limit)} ORDER BY at DESC",
            (),
        )

    def existing_routine_names(self) -> Query:
        return Query("SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES", ())
