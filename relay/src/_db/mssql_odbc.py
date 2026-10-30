from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from _db import queries
from _db.common import MonotonicAuthProbeClock, tcp_check, to_iso_utc
from _db.errors import classify_mssql_odbc_error
from _db.registry import RegistryRow
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _contract.schema import ServerEntry, Settings

logger = logging.getLogger("relay.db.mssql_odbc")

_ISOLATION_PREFIX = "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;\n"

_FULL_BATCH_FAILURES_BEFORE_COOLDOWN = 2
_FULL_BATCH_COOLDOWN_MS = 60000


class MssqlOdbcError(RuntimeError):
    pass


class MSSQLOdbcDriver:
    platform = "mssql"
    variant = "odbc"

    def __init__(self, server: "ServerEntry", settings: "Settings"):
        self.server = server
        self.settings = settings
        self.q = queries.MSSQLQueries()
        self.query_timeout_ms = server.timeout_ms or settings.query_timeout_ms_odbc
        self._auth_probe = MonotonicAuthProbeClock(settings.auth_probe_interval_ms)

        self._cycle_started = False
        self._batch_ran_this_cycle = False
        self._full_batch_consecutive_failures = 0
        self._full_batch_cooldown_until_monotonic: Optional[float] = None


    def begin_poll_cycle(self) -> None:
        self._cycle_started = True
        self._batch_ran_this_cycle = False

    def end_poll_cycle(self) -> None:
        self._cycle_started = False

    def _require_cycle_started(self) -> None:
        if not self._cycle_started:
            raise RuntimeError(
                "MSSQLOdbcDriver: begin_poll_cycle() must be called before any other "
                "method each poll cycle (O11) — stale-cycle data would otherwise leak "
                "across cycles."
            )


    def _build_argv(self, sql_arg: tuple[str, str]) -> list[str]:
        return [
            "sqlcmd",
            "-S",
            f"{self.server.host},{self.server.port}",
            "-d",
            self.server.database,
            "-U",
            self.server.user,
            "-C",
            "-b",
            "-h",
            "-1",
            "-W",
            "-y",
            "0",
            sql_arg[0],
            sql_arg[1],
        ]

    def _subprocess_env(self) -> dict:
        env = dict(os.environ)
        env["SQLCMDPASSWORD"] = self.server.password
        return env

    def _run_sqlcmd(self, sql: str, timeout_ms: Optional[int] = None) -> str:
        full_sql = _ISOLATION_PREFIX + sql
        timeout_s = (timeout_ms or self.query_timeout_ms) / 1000

        tmp_path: Optional[Path] = None
        try:
            if len(full_sql) > self.settings.inline_query_max_chars:
                fd, tmp_name = tempfile.mkstemp(suffix=".sql")
                tmp_path = Path(tmp_name)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(full_sql)
                argv = self._build_argv(("-i", str(tmp_path)))
            else:
                argv = self._build_argv(("-Q", full_sql))

            try:
                result = subprocess.run(
                    argv,
                    env=self._subprocess_env(),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired:
                raise MssqlOdbcError("sqlcmd timed out") from None

            if result.returncode != 0:
                stderr_text = (result.stderr or "").strip()
                raise MssqlOdbcError(stderr_text or "sqlcmd exited non-zero with no stderr output")

            return result.stdout
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _run_json_rows(self, select_sql: str, timeout_ms: Optional[int] = None) -> list[dict]:
        wrapped = (
            "SET NOCOUNT ON;\n"
            f"SELECT ({select_sql} FOR JSON PATH, INCLUDE_NULL_VALUES) AS [json_result];"
        )
        stdout = self._run_sqlcmd(wrapped, timeout_ms=timeout_ms)
        text = "".join(line for line in stdout.splitlines() if line.strip()).strip()
        if not text:
            return []
        return json.loads(text)

    def _run_json_scalar_pair(self, select_sql: str, timeout_ms: Optional[int] = None) -> tuple:
        rows = self._run_json_rows(select_sql, timeout_ms=timeout_ms)
        if not rows:
            return (0, None)
        row = rows[0]
        return tuple(row.values())


    def _auth_probe_sync(self) -> bool:
        try:
            self._run_sqlcmd(self.q.select_1().sql, timeout_ms=self.settings.probe_timeout_ms_odbc)
            return True
        except MssqlOdbcError:
            return False

    async def check_chain(self) -> ReadStatus:
        import asyncio

        self._require_cycle_started()

        tcp_status = await tcp_check(self.server.host, self.server.port, self.settings.tcp_timeout_ms)
        if tcp_status != ReadStatus.OK:
            return tcp_status

        loop = asyncio.get_event_loop()
        if self._auth_probe.is_due():
            ok = await loop.run_in_executor(None, self._auth_probe_sync)
            self._auth_probe.record(ok)
        if not self._auth_probe.last_ok:
            return ReadStatus.DOWN

        if self._batch_ran_this_cycle:
            return ReadStatus.OK

        try:
            await loop.run_in_executor(None, self._run_sqlcmd, self.q.select_1().sql, self.settings.probe_timeout_ms_odbc)
        except MssqlOdbcError as exc:
            classification = classify_mssql_odbc_error(str(exc))
            return ReadStatus.DOWN if classification in ("auth", "network") else ReadStatus.UNREAD
        except Exception:
            logger.error("mssql_odbc check_chain: unexpected failure probing server %s", self.server.id, exc_info=True)
            return ReadStatus.UNREAD
        return ReadStatus.OK


    async def fetch_registry_signature(self) -> str:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        p = await loop.run_in_executor(None, self._run_json_scalar_pair, self.q.registry_signature().sql)
        t = await loop.run_in_executor(None, self._run_json_scalar_pair, self.q.registry_signature_triggers().sql)
        return f"{p[0]}:{p[1]}|{t[0]}:{t[1]}"

    async def fetch_registry_rows(self) -> list[RegistryRow]:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, self._run_json_rows, "SELECT PipelineId AS pipeline_id, SourceSystem AS source_system, TargetSystem AS target_system, Name AS name FROM dbo.SyncPipeline"
        )
        return [RegistryRow(pipeline_id=r["pipeline_id"], source_system=r["source_system"], target_system=r["target_system"], name=r.get("name") or "") for r in rows]


    def _full_batch_in_cooldown(self) -> bool:
        if self._full_batch_cooldown_until_monotonic is None:
            return False
        return time.monotonic() < self._full_batch_cooldown_until_monotonic

    def _record_full_batch_result(self, ok: bool) -> None:
        if ok:
            self._full_batch_consecutive_failures = 0
            self._full_batch_cooldown_until_monotonic = None
            return
        self._full_batch_consecutive_failures += 1
        if self._full_batch_consecutive_failures >= _FULL_BATCH_FAILURES_BEFORE_COOLDOWN:
            self._full_batch_cooldown_until_monotonic = time.monotonic() + _FULL_BATCH_COOLDOWN_MS / 1000

    def _blocking_full_batch(self) -> dict:
        combined_sql = (
            "SET NOCOUNT ON;\n"
            f"SELECT ({self.q.pipelines_with_metrics().sql} FOR JSON PATH, INCLUDE_NULL_VALUES) AS a;\n"
            f"SELECT ({self.q.registry_signature().sql} FOR JSON PATH, INCLUDE_NULL_VALUES) AS b;\n"
            f"SELECT ({self.q.registry_signature_triggers().sql} FOR JSON PATH, INCLUDE_NULL_VALUES) AS c;\n"
            f"SELECT ({self.q.triggers().sql} FOR JSON PATH, INCLUDE_NULL_VALUES) AS d;"
        )
        stdout = self._run_sqlcmd(combined_sql)
        lines = [line for line in stdout.splitlines() if line.strip()]
        if len(lines) < 4:
            raise MssqlOdbcError("full batch: expected 4 JSON result lines, sqlcmd returned fewer")
        pipelines = json.loads(lines[0]) if lines[0].strip() else []
        sig_p = json.loads(lines[1])[0] if lines[1].strip() else {"cnt": 0, "maxId": None}
        sig_t = json.loads(lines[2])[0] if lines[2].strip() else {"cnt": 0, "maxId": None}
        triggers = json.loads(lines[3]) if lines[3].strip() else []
        return {"pipelines": pipelines, "signature": f"{sig_p['cnt']}:{sig_p['maxId']}|{sig_t['cnt']}:{sig_t['maxId']}", "triggers": triggers}

    def _blocking_essentials_batch(self) -> dict:
        combined_sql = (
            "SET NOCOUNT ON;\n"
            f"SELECT ({self.q.pipelines_with_metrics().sql} FOR JSON PATH, INCLUDE_NULL_VALUES) AS a;\n"
            f"SELECT ({self.q.registry_signature().sql} FOR JSON PATH, INCLUDE_NULL_VALUES) AS b;"
        )
        stdout = self._run_sqlcmd(combined_sql)
        lines = [line for line in stdout.splitlines() if line.strip()]
        if len(lines) < 2:
            raise MssqlOdbcError("essentials batch: expected 2 JSON result lines, sqlcmd returned fewer")
        pipelines = json.loads(lines[0]) if lines[0].strip() else []
        sig_p = json.loads(lines[1])[0] if lines[1].strip() else {"cnt": 0, "maxId": None}
        return {"pipelines": pipelines, "signature": f"{sig_p['cnt']}:{sig_p['maxId']}|0:None", "triggers": None}

    def _blocking_per_query_batch(self) -> dict:
        pipelines = self._run_json_rows(self.q.pipelines_with_metrics().sql)
        sig_p = self._run_json_scalar_pair(self.q.registry_signature().sql)
        sig_t = self._run_json_scalar_pair(self.q.registry_signature_triggers().sql)
        triggers = self._run_json_rows(self.q.triggers().sql)
        return {"pipelines": pipelines, "signature": f"{sig_p[0]}:{sig_p[1]}|{sig_t[0]}:{sig_t[1]}", "triggers": triggers}

    async def fetch_poll_batch(self) -> dict:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()

        if not self._full_batch_in_cooldown():
            try:
                result = await loop.run_in_executor(None, self._blocking_full_batch)
                self._record_full_batch_result(True)
                for row in result["pipelines"]:
                    row["lastPingAt"] = to_iso_utc(row.get("lastPingAt"))
                self._batch_ran_this_cycle = True
                return result
            except MssqlOdbcError:
                self._record_full_batch_result(False)

        try:
            result = await loop.run_in_executor(None, self._blocking_essentials_batch)
            for row in result["pipelines"]:
                row["lastPingAt"] = to_iso_utc(row.get("lastPingAt"))
            self._batch_ran_this_cycle = True
            return result
        except MssqlOdbcError:
            pass

        result = await loop.run_in_executor(None, self._blocking_per_query_batch)
        for row in result["pipelines"]:
            row["lastPingAt"] = to_iso_utc(row.get("lastPingAt"))
        self._batch_ran_this_cycle = True
        return result


    async def fetch_pipelines_with_metrics(self) -> list[dict]:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, self._run_json_rows, self.q.pipelines_with_metrics().sql)
        for row in rows:
            row["lastPingAt"] = to_iso_utc(row.get("lastPingAt"))
        return rows

    async def fetch_triggers(self) -> list[dict]:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run_json_rows, self.q.triggers().sql)

    async def fetch_outbox_sent(self, scan_window_seconds: int) -> list[dict]:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        query = self.q.outbox_sent_literal(scan_window_seconds)
        rows = await loop.run_in_executor(None, self._run_json_rows, query.sql)
        for row in rows:
            row["origin"] = (row.get("origin") or "").lower()
            row["sent_at"] = to_iso_utc(row.get("sent_at"))
            row["created_at"] = to_iso_utc(row.get("created_at"))
        return rows

    async def fetch_inbox_index(self, message_ids: tuple[str, ...]) -> list[dict]:
        import asyncio

        self._require_cycle_started()
        if not message_ids:
            return []
        loop = asyncio.get_event_loop()
        select_sql = self.q.inbox_index_unchunked(message_ids).sql
        rows = await loop.run_in_executor(None, self._run_json_rows, select_sql)
        for row in rows:
            row["applied_at"] = to_iso_utc(row.get("applied_at"))
        return rows

    async def fetch_inbox_since(self, window_seconds: int) -> list[dict]:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        n = int(window_seconds)
        select_sql = (
            "SELECT MessageId AS message_id, PipelineId AS pipeline_id, ReceivedAt AS received_at, "
            "AppliedAt AS applied_at, Status AS status "
            f"FROM dbo.SyncInbox WHERE ReceivedAt >= DATEADD(SECOND, -{n}, SYSUTCDATETIME())"
        )
        rows = await loop.run_in_executor(None, self._run_json_rows, select_sql)
        for row in rows:
            row["received_at"] = to_iso_utc(row.get("received_at"))
            row["applied_at"] = to_iso_utc(row.get("applied_at"))
        return rows

    async def fetch_error_rows(self, limit: int = 100) -> list[dict]:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        query = self.q.error_rows(limit)
        select_sql = query.sql.replace("TOP (?)", f"TOP ({int(limit)})")
        rows = await loop.run_in_executor(None, self._run_json_rows, select_sql)
        for row in rows:
            row["at"] = to_iso_utc(row.get("at"))
        return rows

    async def fetch_error_rows_batch_partitioned(self, per_pipeline_limit: int = 20) -> list[dict]:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        query = self.q.error_rows_batch_partitioned(per_pipeline_limit)
        rows = await loop.run_in_executor(None, self._run_json_rows, query.sql)
        for row in rows:
            row["at"] = to_iso_utc(row.get("at"))
        return rows

    async def fetch_existing_routine_names(self) -> set[str]:
        import asyncio

        self._require_cycle_started()
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, self._run_json_rows, self.q.existing_routine_names().sql)
        return {r["ROUTINE_NAME"] for r in rows}
