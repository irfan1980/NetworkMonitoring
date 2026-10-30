from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional

from _scheduler.utc import now_utc, parse_iso

logger = logging.getLogger("relay.scheduler.runlog")

SUCCESS = "success"
FAIL_OUTCOMES = frozenset({"failed", "timeout", "script_not_found", "lost"})
UNCOUNTED_OUTCOMES = frozenset({"skipped_locked", "missed", "auth_rejected", "cancelled"})


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
        self._dir = Path(state_dir) / "scheduler" / "runs"
        self._settings = settings_getter
        self._states: dict[int, RunState] = {}
        self._counts: dict[int, int] = {}
        self._seq: dict[int, int] = {}

    def reset_cache(self) -> None:
        self._states.clear()
        self._counts.clear()
        self._seq.clear()

    def _path(self, schedule_id: int) -> Path:
        return self._dir / f"{schedule_id}.jsonl"

    def _read_all(self, schedule_id: int) -> list[dict]:
        path = self._path(schedule_id)
        if not path.exists():
            return []
        rows: list[dict] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("skipping unreadable run log line in %s", path)
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

        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path(schedule_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
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
        path = self._path(schedule_id)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8")
        os.replace(tmp, path)
        self._counts[schedule_id] = len(kept)

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
        self._path(schedule_id).unlink(missing_ok=True)


def _row_time(row: dict):
    try:
        return parse_iso(row.get("ended_at") or row.get("started_at"))
    except (TypeError, ValueError):
        return now_utc()
