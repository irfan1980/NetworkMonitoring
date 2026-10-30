from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from _db import queries
from _db.common import MonotonicAuthProbeClock, normalize_row, run_blocking, tcp_check, to_iso_utc
from _db.errors import classify_postgresql_error
from _db.registry import RegistryRow
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _contract.schema import ServerEntry, Settings


class PostgreSQLDriver:
    platform = "postgresql"

    def __init__(self, server: "ServerEntry", settings: "Settings"):
        self.server = server
        self.settings = settings
        self.q = queries.PostgreSQLQueries(schema=server.schema_name or "sync")
        self.query_timeout_ms = server.timeout_ms or settings.query_timeout_ms_default
        self._pooled_conn = None
        self._auth_probe = MonotonicAuthProbeClock(settings.auth_probe_interval_ms)

    def _connect(self):
        import psycopg2

        conn = psycopg2.connect(
            host=self.server.host,
            port=self.server.port,
            user=self.server.user,
            password=self.server.password,
            dbname=self.server.database,
            connect_timeout=max(1, int(self.settings.tcp_timeout_ms / 1000)),
            keepalives=1,
            keepalives_idle=5,
            keepalives_interval=2,
            keepalives_count=3,
        )
        conn.autocommit = True
        return conn

    def _pooled(self):
        if self._pooled_conn is None or self._pooled_conn.closed:
            self._pooled_conn = self._connect()
        return self._pooled_conn

    def _reset_pool(self) -> None:
        try:
            if self._pooled_conn is not None:
                self._pooled_conn.close()
        except Exception:
            pass
        self._pooled_conn = None

    def _blocking_auth_probe(self) -> bool:
        try:
            conn = self._connect()
        except Exception:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(self.q.select_1().sql)
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def _blocking_pooled_select_1(self) -> None:
        conn = self._pooled()
        with conn.cursor() as cur:
            cur.execute(self.q.select_1().sql)

    async def check_chain(self) -> ReadStatus:
        tcp_status = await tcp_check(self.server.host, self.server.port, self.settings.tcp_timeout_ms)
        if tcp_status != ReadStatus.OK:
            return tcp_status

        if self._auth_probe.is_due():
            try:
                ok = await run_blocking(self._blocking_auth_probe, timeout_ms=self.query_timeout_ms)
            except TimeoutError:
                return ReadStatus.UNREAD
            self._auth_probe.record(ok)
        if not self._auth_probe.last_ok:
            return ReadStatus.DOWN

        try:
            await run_blocking(self._blocking_pooled_select_1, timeout_ms=self.query_timeout_ms)
        except TimeoutError:
            return ReadStatus.UNREAD
        except Exception as exc:
            self._reset_pool()
            classification = classify_postgresql_error(exc)
            return ReadStatus.DOWN if classification in ("auth", "network") else ReadStatus.UNREAD
        return ReadStatus.OK

    def _blocking_signature(self) -> str:
        conn = self._pooled()
        with conn.cursor() as cur:
            cur.execute(self.q.registry_signature().sql)
            p_cnt, p_max = cur.fetchone()
            cur.execute(self.q.registry_signature_triggers().sql)
            t_cnt, t_max = cur.fetchone()
        return f"{p_cnt}:{p_max}|{t_cnt}:{t_max}"

    async def fetch_registry_signature(self) -> str:
        return await run_blocking(self._blocking_signature, timeout_ms=self.query_timeout_ms)

    def _blocking_rows(self) -> list[RegistryRow]:
        conn = self._pooled()
        with conn.cursor() as cur:
            cur.execute(f"SELECT pipeline_id, source_system, target_system, name FROM {self.q.schema}.sync_pipeline")
            return [RegistryRow(pipeline_id=r[0], source_system=r[1], target_system=r[2], name=r[3]) for r in cur.fetchall()]

    async def fetch_registry_rows(self) -> list[RegistryRow]:
        return await run_blocking(self._blocking_rows, timeout_ms=self.query_timeout_ms)

    def _blocking_dict_query(self, query: queries.Query) -> list[dict]:
        conn = self._pooled()
        with conn.cursor() as cur:
            cur.execute(query.sql, query.params)
            cols = [d[0] for d in cur.description]
            return [normalize_row(dict(zip(cols, row))) for row in cur.fetchall()]

    async def fetch_pipelines_with_metrics(self) -> list[dict]:
        rows = await run_blocking(self._blocking_dict_query, self.q.pipelines_with_metrics(), timeout_ms=self.query_timeout_ms)
        for row in rows:
            row["lastPingAt"] = to_iso_utc(row["lastPingAt"])
        return rows

    async def fetch_triggers(self) -> list[dict]:
        return await run_blocking(self._blocking_dict_query, self.q.triggers(), timeout_ms=self.query_timeout_ms)

    async def fetch_outbox_sent(self, scan_window_seconds: int) -> list[dict]:
        rows = await run_blocking(
            self._blocking_dict_query, self.q.outbox_sent(scan_window_seconds), timeout_ms=self.query_timeout_ms
        )
        for row in rows:
            row["origin"] = (row["origin"] or "").lower()
            row["sent_at"] = to_iso_utc(row["sent_at"])
            row["created_at"] = to_iso_utc(row["created_at"])
        return rows

    async def fetch_outbox_entity_agg(self, pipeline_ids: Optional[tuple[int, ...]] = None) -> list[dict]:
        return await run_blocking(
            self._blocking_dict_query, self.q.outbox_entity_agg(pipeline_ids), timeout_ms=self.query_timeout_ms
        )

    async def fetch_inbox_entity_agg(self, pipeline_ids: Optional[tuple[int, ...]] = None) -> list[dict]:
        agg = await run_blocking(
            self._blocking_dict_query, self.q.inbox_entity_agg(pipeline_ids), timeout_ms=self.query_timeout_ms
        )
        errors = await run_blocking(
            self._blocking_dict_query, self.q.inbox_entity_last_error(pipeline_ids), timeout_ms=self.query_timeout_ms
        )
        last_error_by_entity: dict[str, dict] = {}
        for row in errors:
            last_error_by_entity.setdefault(row["entity_type"], row)
        for row in agg:
            err = last_error_by_entity.get(row["entity_type"])
            row["lastError"] = err["error"] if err else None
            row["lastErrorAt"] = to_iso_utc(err["received_at"]) if err else None
            row["lastErrorMessageId"] = str(err["message_id"]) if err else None
        return agg

    async def fetch_inbox_index(self, message_ids: tuple[str, ...]) -> list[dict]:
        if not message_ids:
            return []
        rows = await run_blocking(self._blocking_dict_query, self.q.inbox_index(message_ids), timeout_ms=self.query_timeout_ms)
        for row in rows:
            row["applied_at"] = to_iso_utc(row["applied_at"])
        return rows

    async def fetch_inbox_since(self, window_seconds: int) -> list[dict]:
        rows = await run_blocking(self._blocking_dict_query, self.q.inbox_since(window_seconds), timeout_ms=self.query_timeout_ms)
        for row in rows:
            row["received_at"] = to_iso_utc(row["received_at"])
            row["applied_at"] = to_iso_utc(row["applied_at"])
        return rows

    async def fetch_error_rows(self, limit: int = 100) -> list[dict]:
        rows = await run_blocking(self._blocking_dict_query, self.q.error_rows(limit), timeout_ms=self.query_timeout_ms)
        for row in rows:
            row["at"] = to_iso_utc(row["at"])
        return rows

    async def fetch_existing_routine_names(self) -> set[str]:
        rows = await run_blocking(
            self._blocking_dict_query, self.q.existing_routine_names(), timeout_ms=self.query_timeout_ms
        )
        return {r["proname"] for r in rows}
