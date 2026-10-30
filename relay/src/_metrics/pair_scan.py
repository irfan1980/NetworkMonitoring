from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from _metrics.readability import ReadStatus


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class PairClassification:
    message_id: str
    pipeline_id: int
    matched: bool
    age_seconds: float
    queued_trend: bool
    queued_countable: bool
    reported_on_pipeline_id: int


def classify_pair(
    message_id: str,
    pipeline_id: int,
    sent_at: datetime,
    matched: bool,
    target_read_status: ReadStatus,
    now: datetime,
    e2e_window_seconds: int,
    overlap_seconds: int,
) -> PairClassification:
    age_seconds = (now - sent_at).total_seconds()
    past_window = age_seconds > (e2e_window_seconds + overlap_seconds)
    queued_trend = (not matched) and past_window
    queued_countable = queued_trend and target_read_status == ReadStatus.OK
    return PairClassification(
        message_id=message_id,
        pipeline_id=pipeline_id,
        matched=matched,
        age_seconds=age_seconds,
        queued_trend=queued_trend,
        queued_countable=queued_countable,
        reported_on_pipeline_id=pipeline_id,
    )


def is_lost(
    age_seconds: float,
    matched: bool,
    last_pipeline_delivery_age_seconds: Optional[float],
    queued_max_age_seconds: int,
    lost_quiet_seconds: int,
    target_read_status: ReadStatus = ReadStatus.OK,
) -> bool:
    if target_read_status != ReadStatus.OK:
        return False
    if matched:
        return False
    if age_seconds <= queued_max_age_seconds:
        return False
    if last_pipeline_delivery_age_seconds is None:
        return True
    return last_pipeline_delivery_age_seconds >= lost_quiet_seconds


def match_outbox_against_inbox(
    outbox_rows: list[dict],
    inbox_index: dict[str, Optional[str]],
) -> dict[str, bool]:
    return {row["message_id"]: (row["message_id"] in inbox_index) for row in outbox_rows}


@dataclass(frozen=True)
class PairView:

    message_id: str
    pipeline_id: int
    direction: str
    platform: str
    matched: bool
    age_seconds: float
    queued_trend: bool
    queued_countable: bool


def build_pair_views(classification: PairClassification, sender_platform: str, target_platform: str) -> tuple[PairView, PairView]:
    from _metrics.message_id import parse_message_id

    parsed = parse_message_id(classification.message_id)
    sender_pipeline_id = parsed.pipeline_id if parsed is not None else classification.pipeline_id

    sender_view = PairView(
        message_id=classification.message_id,
        pipeline_id=sender_pipeline_id,
        direction="outgoing",
        platform=sender_platform,
        matched=classification.matched,
        age_seconds=classification.age_seconds,
        queued_trend=classification.queued_trend,
        queued_countable=classification.queued_countable,
    )
    target_view = PairView(
        message_id=classification.message_id,
        pipeline_id=classification.reported_on_pipeline_id,
        direction="incoming",
        platform=target_platform,
        matched=classification.matched,
        age_seconds=classification.age_seconds,
        queued_trend=classification.queued_trend,
        queued_countable=classification.queued_countable,
    )
    return sender_view, target_view


def inbox_ids_to_query(outbox_rows: list[dict]) -> tuple[str, ...]:
    return tuple(row["message_id"] for row in outbox_rows)


def scan_window_seconds(queued_max_age_seconds: int, e2e_window_seconds: int) -> int:
    return max(queued_max_age_seconds, e2e_window_seconds) + 300


@dataclass
class _OutboxCacheEntry:
    row: dict
    sent_at: datetime


@dataclass
class OutboxScanCache:

    server_id: str
    pair_full_rescan_seconds: int = 60
    max_full_rescan_backoff: int = 10
    stagger_offset_seconds: float = 0.0
    _INCREMENTAL_OVERLAP_SECONDS = 5.0

    _by_message_id: dict[str, _OutboxCacheEntry] = field(default_factory=dict)
    _cursor: Optional[datetime] = None
    _last_full_rescan_monotonic: Optional[float] = None
    _last_poll_was_full_rescan: bool = False
    _consecutive_failures: int = 0

    @classmethod
    def from_settings(cls, settings, server_id: str, stagger_offset_seconds: float = 0.0) -> "OutboxScanCache":
        return cls(
            server_id=server_id,
            pair_full_rescan_seconds=settings.pair_full_rescan_seconds,
            max_full_rescan_backoff=settings.max_full_rescan_backoff,
            stagger_offset_seconds=stagger_offset_seconds,
        )

    def _effective_rescan_interval(self) -> float:
        backoff = min(2**self._consecutive_failures, self.max_full_rescan_backoff)
        return self.pair_full_rescan_seconds * backoff

    def _due_for_full_rescan(self, now_monotonic: float) -> bool:
        if self._last_poll_was_full_rescan:
            return False
        if self._last_full_rescan_monotonic is None:
            return now_monotonic >= self.stagger_offset_seconds
        elapsed = now_monotonic - self._last_full_rescan_monotonic
        return elapsed >= self._effective_rescan_interval()

    async def poll(
        self,
        now: datetime,
        queued_max_age_seconds: int,
        e2e_window_seconds: int,
        fetch_outbox_sent: Callable[[int], Awaitable[list[dict]]],
        now_monotonic: Optional[float] = None,
    ) -> list[dict]:
        window = scan_window_seconds(queued_max_age_seconds, e2e_window_seconds)
        mono_now = now_monotonic if now_monotonic is not None else time.monotonic()
        do_full = self._due_for_full_rescan(mono_now)
        treat_as_full = do_full or self._cursor is None

        if treat_as_full:
            fetch_window_seconds = window
        else:
            since_cursor = (now - self._cursor).total_seconds() + self._INCREMENTAL_OVERLAP_SECONDS
            fetch_window_seconds = math.ceil(min(max(since_cursor, 0.0), window))

        try:
            rows = await fetch_outbox_sent(fetch_window_seconds)
        except Exception:
            self._consecutive_failures += 1
            self._last_poll_was_full_rescan = False
            return self._within_window(now, window)

        self._consecutive_failures = 0
        seen_ids: set[str] = set()
        for row in rows:
            sent_at = _parse_iso(row["sent_at"])
            if sent_at is None:
                continue
            seen_ids.add(row["message_id"])
            self._by_message_id[row["message_id"]] = _OutboxCacheEntry(row=row, sent_at=sent_at)
            if self._cursor is None or sent_at > self._cursor:
                self._cursor = sent_at

        if treat_as_full:
            cutoff = now - timedelta(seconds=window)
            for mid, entry in list(self._by_message_id.items()):
                if entry.sent_at >= cutoff and mid not in seen_ids:
                    del self._by_message_id[mid]

        if do_full:
            self._last_full_rescan_monotonic = mono_now

        self._last_poll_was_full_rescan = do_full
        return self._within_window(now, window)

    def _within_window(self, now: datetime, window: int) -> list[dict]:
        cutoff = now - timedelta(seconds=window)
        for mid in [m for m, e in self._by_message_id.items() if e.sent_at < cutoff]:
            del self._by_message_id[mid]
        return [e.row for e in self._by_message_id.values() if e.sent_at >= cutoff]


@dataclass
class ReconcileRangeCache:

    _covered: list[tuple[datetime, datetime]] = field(default_factory=list)

    def mark_covered(self, start: datetime, end: datetime) -> None:
        merged = [(start, end)]
        for existing_start, existing_end in self._covered:
            if existing_end < start or existing_start > end:
                merged.append((existing_start, existing_end))
            else:
                s, e = merged[0]
                merged[0] = (min(s, existing_start), max(e, existing_end))
        self._covered = merged

    def is_covered(self, at: datetime) -> bool:
        return any(start <= at <= end for start, end in self._covered)

    def covered_ranges(self) -> list[tuple[datetime, datetime]]:
        return list(self._covered)


class InboxAskTracker:

    def __init__(self, pair_inbox_ask_max: int = 2000):
        self.pair_inbox_ask_max = pair_inbox_ask_max
        self._matched: set[str] = set()
        self._ask_cursor: dict[object, int] = {}

    @classmethod
    def from_settings(cls, settings) -> "InboxAskTracker":
        return cls(pair_inbox_ask_max=settings.pair_inbox_ask_max)

    def mark_matched(self, message_id: str) -> None:
        self._matched.add(message_id)

    def is_matched(self, message_id: str) -> bool:
        return message_id in self._matched

    def ids_to_ask(self, candidate_ids: list[str], key: object = None) -> list[str]:
        pending = [mid for mid in candidate_ids if mid not in self._matched]
        cap = self.pair_inbox_ask_max
        if len(pending) <= cap:
            self._ask_cursor.pop(key, None)
            return pending
        start = self._ask_cursor.get(key, 0) % len(pending)
        window = pending[start : start + cap]
        if len(window) < cap:
            window += pending[: cap - len(window)]
        self._ask_cursor[key] = (start + cap) % len(pending)
        return window

    def prune_matched(self, live_ids: set[str]) -> None:
        self._matched &= live_ids


@dataclass
class PacketsDiff:
    added: frozenset
    removed: frozenset
    still_in_flight: frozenset


class PacketsTracker:

    def __init__(self) -> None:
        self._previous: dict[int, frozenset] = {}

    def has_previous(self, pipeline_id) -> bool:
        return pipeline_id in self._previous

    def diff(self, pipeline_id: int, current_unmatched_ids: set) -> PacketsDiff:
        previous = self._previous.get(pipeline_id, frozenset())
        current = frozenset(current_unmatched_ids)
        result = PacketsDiff(
            added=current - previous,
            removed=previous - current,
            still_in_flight=current & previous,
        )
        self._previous[pipeline_id] = current
        return result


def clamp_packet_count(raw_count: float) -> int:
    return max(0, min(50, round(raw_count)))


def diff_pairs(previous: Optional[dict], current: dict) -> dict:
    patch: dict = {"id": current["id"], "packets": current["packets"]}
    prev = previous or {}
    for key, value in current.items():
        if key in ("id", "packets"):
            continue
        if key not in prev or prev[key] != value:
            patch[key] = value
    return patch
