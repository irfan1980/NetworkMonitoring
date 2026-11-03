from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional

from _scheduler.utc import now_utc, parse_iso
from _state.database import Database

logger = logging.getLogger("relay.scheduler.runlog")

SUCCESS = "success"
FAIL_OUTCOMES = frozenset({"failed", "timeout", "script_not_found", "lost"})
UNCOUNTED_OUTCOMES = frozenset({"skipped_locked", "missed", "auth_rejected", "cancelled"})
PURGE_BATCH = 100
PURGE_PAUSE_SECONDS = 0.05


def cap_tail(text: Optional[str], kb: int) -> str:
    if not text:
        return ""
    raw = text.encode("utf-8", errors="replace")
    limit = kb * 1024
    if len(raw) <= limit:
        return text
    return raw[-limit:].decode("utf-8", errors="ignore")


@dataclass
class RunState:
    last_run: Optional[str] = None
    last_outcome: Optional[str] = None
    last_reason: Optional[str] = None
    consecutive_failures: int = 0
    has_counted_run: bool = False

    def apply(self, record: dict) -> None:
        outcome = record.get("outcome")
        self.last_run = record.get("ended_at") or record.get("started_at")
        self.last_outcome = outcome
        self.last_reason = record.get("reason")
        if outcome == SUCCESS:
            self.consecutive_failures = 0
            self.has_counted_run = True
        elif outcome in FAIL_OUTCOMES:
            self.consecutive_failures += 1
            self.has_counted_run = True

    def color(self, threshold: int) -> str:
        if self.consecutive_failures >= threshold:
            return "red"
        return "green" if self.has_counted_run else "grey"


class RunLog:

    def __init__(self, state_dir: Path | str, settings_getter: Callable[[], object]):
        self._db = Database.for_dir(state_dir)
        self._settings = settings_getter
        self._states: dict[int, RunState] = {}
        self._counts: dict[int, int] = {}
        self._seq: dict[int, int] = {}

    def reset_cache(self) -> None:
        self._states.clear()
        self._counts.clear()
        self._seq.clear()

    def _read_all(self, schedule_id: int) -> list[dict]:
        rows: list[dict] = []
        for (body,) in self._db.query("SELECT body FROM schedule_runs WHERE schedule_id=? ORDER BY seq", (schedule_id,)):
            try:
                rows.append(json.loads(body))
            except ValueError:
                logger.warning("skipping unreadable run record for schedule %s", schedule_id)
        return rows

    def _ensure(self, schedule_id: int) -> RunState:
        state = self._states.get(schedule_id)
        if state is not None:
            return state
        state = RunState()
        rows = self._read_all(schedule_id)
        for row in rows:
            state.apply(row)
        self._states[schedule_id] = state
        self._counts[schedule_id] = len(rows)
        self._seq[schedule_id] = max((r.get("seq", 0) for r in rows), default=0)
        return state

    def state(self, schedule_id: int) -> RunState:
        return self._ensure(schedule_id)

    def append(self, schedule_id: int, record: dict) -> RunState:
        state = self._ensure(schedule_id)
        settings = self._settings()
        kb = settings.log_output_tail_kb
        row = dict(record)
        self._seq[schedule_id] += 1
        row["seq"] = self._seq[schedule_id]
        row["stdout_tail"] = cap_tail(row.get("stdout_tail"), kb)
        row["stderr_tail"] = cap_tail(row.get("stderr_tail"), kb)

        body, seq = json.dumps(row), row["seq"]
        self._db.write(lambda conn: conn.execute(
            "INSERT OR REPLACE INTO schedule_runs(schedule_id, seq, body) VALUES (?, ?, ?)", (schedule_id, seq, body)))
        state.apply(row)
        self._counts[schedule_id] += 1
        if self._counts[schedule_id] > settings.log_keep_runs:
            self._prune(schedule_id)
        return state

    def _prune(self, schedule_id: int) -> None:
        settings = self._settings()
        rows = self._read_all(schedule_id)
        cutoff = now_utc() - timedelta(days=settings.log_keep_days)
        keep_from = max(0, len(rows) - settings.log_keep_runs)
        kept = [
            row for index, row in enumerate(rows)
            if index >= keep_from or _row_time(row) >= cutoff
        ]
        if len(kept) == len(rows):
            return
        dropped = [(schedule_id, r.get("seq", 0)) for r in rows if r not in kept]
        self._db.write(lambda conn: conn.executemany("DELETE FROM schedule_runs WHERE schedule_id=? AND seq=?", dropped))
        self._counts[schedule_id] = len(kept)

    def purge_expired(self, max_age_days: int, protected_run_ids=(), batch: int = PURGE_BATCH, pause: float = PURGE_PAUSE_SECONDS) -> int:
        """Delete finished runs that ended more than max_age_days ago, in small batches (one transaction each) so busy paths
        are not held up. Kept: the newest row of every schedule (so last-run state and sequence numbers survive a restart), runs
        still in progress, and rows with no outcome or an unparseable end time. Blocking: call it from a worker thread."""
        cutoff = now_utc() - timedelta(days=max_age_days)
        protected = set(protected_run_ids)
        newest = {sid: seq for sid, seq in self._db.query("SELECT schedule_id, MAX(seq) FROM schedule_runs GROUP BY schedule_id")}
        doomed: list[tuple[int, int]] = []
        last_key = (-1, -1)
        while True:
            page = self._db.query(
                "SELECT schedule_id, seq, body FROM schedule_runs WHERE (schedule_id, seq) > (?, ?) ORDER BY schedule_id, seq LIMIT 100",
                last_key,
            )
            if not page:
                break
            for schedule_id, seq, body in page:
                last_key = (schedule_id, seq)
                if seq == newest.get(schedule_id):
                    continue
                try:
                    row = json.loads(body)
                    if not row.get("outcome") or row.get("run_id") in protected:
                        continue
                    ended = parse_iso(row.get("ended_at"))
                except (ValueError, TypeError, AttributeError):
                    continue
                if ended < cutoff:
                    doomed.append((schedule_id, seq))
        removed = 0
        for start in range(0, len(doomed), batch):
            chunk = doomed[start:start + batch]
            self._db.write(lambda conn, chunk=chunk: conn.executemany("DELETE FROM schedule_runs WHERE schedule_id=? AND seq=?", chunk))
            removed += len(chunk)
            for schedule_id, _ in chunk:
                if schedule_id in self._counts:
                    self._counts[schedule_id] -= 1
            if pause and start + batch < len(doomed):
                time.sleep(pause)
        return removed

    def read(self, schedule_id: int, limit: int = 50, before: Optional[int] = None) -> tuple[list[dict], Optional[int]]:
        rows = self._read_all(schedule_id)
        if before is not None:
            rows = [r for r in rows if r.get("seq", 0) < before]
        rows.sort(key=lambda r: r.get("seq", 0), reverse=True)
        page = rows[:limit]
        more = len(rows) > limit
        return page, (page[-1]["seq"] if page and more else None)

    def delete(self, schedule_id: int) -> None:
        self._states.pop(schedule_id, None)
        self._counts.pop(schedule_id, None)
        self._seq.pop(schedule_id, None)
        self._db.write(lambda conn: conn.execute("DELETE FROM schedule_runs WHERE schedule_id=?", (schedule_id,)))


def _row_time(row: dict):
    try:
        return parse_iso(row.get("ended_at") or row.get("started_at"))
    except (TypeError, ValueError):
        return now_utc()
