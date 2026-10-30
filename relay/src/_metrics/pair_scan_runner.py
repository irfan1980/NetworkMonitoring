from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from _db.factory import build_driver
from _lib.expected_errors import describe_driver_error
from _lib.transition_log import TransitionLogger
from _metrics.attribution import obj_status_for_reads
from _metrics.message_id import parse_message_id
from _metrics.pair_scan import InboxAskTracker, OutboxScanCache, PacketsTracker, classify_pair, is_lost
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _metrics.down_confirm import DownConfirm

logger = logging.getLogger("relay.metrics.pair_scan_runner")

_TRACE = os.environ.get("RELAY_PACKETS_TRACE") == "1"


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class PairScanResult:
    lost_by_pair: dict[tuple[int, int], int] = field(default_factory=dict)
    queued_by_pair: dict[tuple[int, int], int] = field(default_factory=dict)
    lost_by_pipeline: dict[int, int] = field(default_factory=dict)
    queued_countable_by_pipeline: dict[int, int] = field(default_factory=dict)
    matched_recently_by_pipeline: dict[int, bool] = field(default_factory=dict)
    packets_by_pair: dict[tuple[int, int], int] = field(default_factory=dict)
    packets_by_pipeline: dict[int, int] = field(default_factory=dict)
    any_scan_succeeded: bool = False
    scan_ok_by_server: dict[int, bool] = field(default_factory=dict)


class PairScanRunner:

    def __init__(self) -> None:
        self._outbox_caches: dict[int, OutboxScanCache] = {}
        self._inbox_trackers: dict[int, InboxAskTracker] = {}
        self._packets_tracker = PacketsTracker()
        self._tick_packets_by_pipeline: dict[int, int] = {}
        self._source_of_pipeline: dict[int, int] = {}
        self._live_ids: dict[tuple[int, int], set[str]] = {}
        self._build_driver_log = TransitionLogger()
        self._outbox_scan_log = TransitionLogger()
        self._inbox_lookup_log = TransitionLogger()

    def _outbox_cache_for(self, server, settings) -> OutboxScanCache:
        cache = self._outbox_caches.get(server.id)
        if cache is None:
            cache = OutboxScanCache.from_settings(
                settings, server_id=str(server.id), stagger_offset_seconds=float(server.id % 10)
            )
            self._outbox_caches[server.id] = cache
        return cache

    def _inbox_tracker_for(self, server, settings) -> InboxAskTracker:
        tracker = self._inbox_trackers.get(server.id)
        if tracker is None:
            tracker = InboxAskTracker.from_settings(settings)
            self._inbox_trackers[server.id] = tracker
        return tracker

    async def _scan_one_server(
        self, server, config, settings, down_confirm: "DownConfirm", target_of_pipeline: dict[int, int],
        now: datetime,
    ) -> tuple[dict[tuple[int, int], int], dict[tuple[int, int], int], dict[int, int], dict[int, int], dict[int, bool], bool, dict[tuple[int, int], int]]:
        servers_by_id = {s.id: s for s in config.servers}
        lost_by_pair: dict[tuple[int, int], int] = {}
        queued_by_pair: dict[tuple[int, int], int] = {}
        lost_by_pipeline: dict[int, int] = {}
        queued_countable_by_pipeline: dict[int, int] = {}
        matched_recently_by_pipeline: dict[int, bool] = {}
        packets_by_pair: dict[tuple[int, int], int] = {}

        label = f"server {server.id} ({server.platform})"
        cache = self._outbox_cache_for(server, settings)
        try:
            driver = build_driver(server, settings)
        except Exception as exc:
            is_expected, message = describe_driver_error(exc, server.platform, label)
            if is_expected:
                self._build_driver_log.down(logger, f"build_driver:{server.id}", message)
            else:
                logger.error("pair_scan_runner: build_driver failed unexpectedly for %s", label, exc_info=True)
            return lost_by_pair, queued_by_pair, lost_by_pipeline, queued_countable_by_pipeline, matched_recently_by_pipeline, False, packets_by_pair
        self._build_driver_log.ok(logger, f"build_driver:{server.id}", label)

        async def _fetch(window_seconds: int) -> list[dict]:
            return await driver.fetch_outbox_sent(window_seconds)

        try:
            rows = await cache.poll(
                now=now,
                queued_max_age_seconds=settings.queued_max_age_seconds,
                e2e_window_seconds=settings.e2e_window_seconds,
                fetch_outbox_sent=_fetch,
            )
        except Exception:
            logger.error("pair_scan_runner: outbox scan for %s raised unexpectedly (not a driver fetch failure -- see comment)", label, exc_info=True)
            return lost_by_pair, queued_by_pair, lost_by_pipeline, queued_countable_by_pipeline, matched_recently_by_pipeline, False, packets_by_pair
        scan_ok_this_tick = cache._consecutive_failures == 0
        if scan_ok_this_tick:
            self._outbox_scan_log.ok(logger, f"outbox:{server.id}", label)
        else:
            self._outbox_scan_log.down(logger, f"outbox:{server.id}", f"{label}: outbox scan failing (server unreachable or query timing out)")

        def qualified(pid):
            return f"{server.id}:{pid}"

        by_target: dict[int, list[dict]] = {}
        for row in rows:
            pid = row.get("pipeline_id")
            target_id = target_of_pipeline.get(qualified(pid))
            if target_id is None or target_id not in servers_by_id:
                continue
            by_target.setdefault(target_id, []).append(row)

        for target_id, target_rows in by_target.items():
            target_server = servers_by_id[target_id]
            tracker = self._inbox_tracker_for(target_server, settings)
            candidate_ids = [r["message_id"] for r in target_rows if not tracker.is_matched(r["message_id"])]
            ask_ids = tracker.ids_to_ask(candidate_ids, key=server.id)
            target_label = f"target {target_id} ({target_server.platform})"
            if ask_ids:
                try:
                    target_driver = build_driver(target_server, settings)
                    inbox_rows = await target_driver.fetch_inbox_index(tuple(ask_ids))
                    for inbox_row in inbox_rows:
                        tracker.mark_matched(inbox_row["message_id"])
                except Exception as exc:
                    is_expected, message = describe_driver_error(exc, target_server.platform, target_label)
                    if is_expected:
                        self._inbox_lookup_log.down(logger, f"inbox:{target_id}", f"{message} (inbox lookup)")
                    else:
                        logger.error("pair_scan_runner: inbox lookup failed unexpectedly for %s", target_label, exc_info=True)
                else:
                    self._inbox_lookup_log.ok(logger, f"inbox:{target_id}", target_label)

            db_object_id = f"db:{target_server.platform}:{target_server.id}:query"
            target_status = obj_status_for_reads(down_confirm, db_object_id)
            pair_key = (server.id, target_id)

            matched_now = {r["message_id"] for r in target_rows if tracker.is_matched(r["message_id"])}
            had_previous = self._packets_tracker.has_previous(pair_key)
            packets_diff = self._packets_tracker.diff(pair_key, matched_now)
            moved = packets_diff.added if had_previous else frozenset()
            self._live_ids[(server.id, target_id)] = {r["message_id"] for r in target_rows}
            if _TRACE:
                logger.info(
                    "PKTTRACE recv pair=%s->%s rows=%d asked=%d matched_total=%d matched_in_window=%d moved=%d "
                    "baseline=%s target_status=%s",
                    server.id, target_id, len(target_rows), len(ask_ids), len(tracker._matched), len(matched_now),
                    len(moved), not had_previous, target_status,
                )
            if moved:
                packets_by_pair[pair_key] = packets_by_pair.get(pair_key, 0) + len(moved)
                for mid in moved:
                    parsed_mid = parse_message_id(mid)
                    pid = parsed_mid.pipeline_id if parsed_mid is not None else None
                    if pid is None or self._source_of_pipeline.get(qualified(pid)) != server.id:
                        continue
                    key = qualified(pid)
                    self._tick_packets_by_pipeline[key] = self._tick_packets_by_pipeline.get(key, 0) + 1

            last_matched_sent_at: dict[int, datetime] = {}
            parsed_by_message_id: dict[str, object] = {}
            for row in target_rows:
                parsed = parse_message_id(row["message_id"])
                parsed_by_message_id[row["message_id"]] = parsed
                if not tracker.is_matched(row["message_id"]):
                    continue
                pid_for_row = parsed.pipeline_id if parsed is not None else row.get("pipeline_id")
                sent_at_dt = _parse_dt(row.get("sent_at"))
                if sent_at_dt is None:
                    continue
                if pid_for_row not in last_matched_sent_at or sent_at_dt > last_matched_sent_at[pid_for_row]:
                    last_matched_sent_at[pid_for_row] = sent_at_dt
            deliveries = {pid: (now - dt).total_seconds() for pid, dt in last_matched_sent_at.items()}

            for row in target_rows:
                sent_at_dt = _parse_dt(row.get("sent_at"))
                if sent_at_dt is None:
                    continue
                matched = tracker.is_matched(row["message_id"])
                parsed = parsed_by_message_id.get(row["message_id"])
                reported_pipeline_id = parsed.pipeline_id if parsed is not None else row.get("pipeline_id")

                classification = classify_pair(
                    message_id=row["message_id"],
                    pipeline_id=reported_pipeline_id,
                    sent_at=sent_at_dt,
                    matched=matched,
                    target_read_status=target_status,
                    now=now,
                    e2e_window_seconds=settings.e2e_window_seconds,
                    overlap_seconds=settings.overlap_seconds,
                )

                lost = is_lost(
                    age_seconds=classification.age_seconds,
                    matched=matched,
                    last_pipeline_delivery_age_seconds=deliveries.get(reported_pipeline_id),
                    queued_max_age_seconds=settings.queued_max_age_seconds,
                    lost_quiet_seconds=settings.lost_quiet_seconds,
                    target_read_status=target_status,
                )

                if classification.queued_countable:
                    queued_by_pair[pair_key] = queued_by_pair.get(pair_key, 0) + 1
                    key = qualified(reported_pipeline_id)
                    queued_countable_by_pipeline[key] = queued_countable_by_pipeline.get(key, 0) + 1
                if lost:
                    lost_by_pair[pair_key] = lost_by_pair.get(pair_key, 0) + 1
                    key = qualified(reported_pipeline_id)
                    lost_by_pipeline[key] = lost_by_pipeline.get(key, 0) + 1
                if matched:
                    matched_recently_by_pipeline[qualified(reported_pipeline_id)] = True

        return lost_by_pair, queued_by_pair, lost_by_pipeline, queued_countable_by_pipeline, matched_recently_by_pipeline, scan_ok_this_tick, packets_by_pair

    async def tick(self, config, settings, down_confirm: "DownConfirm", pipelines_wire: list[dict]) -> PairScanResult:
        now = datetime.now(timezone.utc)
        result = PairScanResult()

        target_of_pipeline: dict[int, int] = {}
        source_of_pipeline: dict[int, int] = {}
        for row in pipelines_wire:
            try:
                src_id, tgt_id = int(row.get("sourceSystem")), int(row.get("targetSystem"))
            except (TypeError, ValueError):
                continue
            if row.get("_hostServerId") == src_id:
                target_of_pipeline[row["pipelineId"]] = tgt_id
                source_of_pipeline[row["pipelineId"]] = src_id

        self._source_of_pipeline = source_of_pipeline
        self._tick_packets_by_pipeline = {}
        scan_results = await asyncio.gather(
            *(
                self._scan_one_server(server, config, settings, down_confirm, target_of_pipeline, now)
                for server in config.servers
            )
        )
        for server, (lost_by_pair, queued_by_pair, lost_by_pipeline, queued_countable_by_pipeline, matched_recently_by_pipeline, ok, packets_by_pair) in zip(
            config.servers, scan_results
        ):
            result.any_scan_succeeded = result.any_scan_succeeded or ok
            result.scan_ok_by_server[server.id] = ok
            for k, v in lost_by_pair.items():
                result.lost_by_pair[k] = result.lost_by_pair.get(k, 0) + v
            for k, v in queued_by_pair.items():
                result.queued_by_pair[k] = result.queued_by_pair.get(k, 0) + v
            for k, v in lost_by_pipeline.items():
                result.lost_by_pipeline[k] = result.lost_by_pipeline.get(k, 0) + v
            for k, v in queued_countable_by_pipeline.items():
                result.queued_countable_by_pipeline[k] = result.queued_countable_by_pipeline.get(k, 0) + v
            for k, v in matched_recently_by_pipeline.items():
                result.matched_recently_by_pipeline[k] = result.matched_recently_by_pipeline.get(k, False) or v
            for k, v in packets_by_pair.items():
                result.packets_by_pair[k] = result.packets_by_pair.get(k, 0) + v

        result.packets_by_pipeline = dict(self._tick_packets_by_pipeline)
        live_by_target: dict[int, set[str]] = {}
        for (_sender, target_id), ids in self._live_ids.items():
            live_by_target.setdefault(target_id, set()).update(ids)
        for target_id, tracker in self._inbox_trackers.items():
            tracker.prune_matched(live_by_target.get(target_id, set()))
        if _TRACE:
            logger.info(
                "PKTTRACE mem outbox_cache_rows=%s matched_ids=%s tracker_prev_ids=%d",
                {sid: len(c._by_message_id) for sid, c in self._outbox_caches.items()},
                {sid: len(t._matched) for sid, t in self._inbox_trackers.items()},
                sum(len(v) for v in self._packets_tracker._previous.values()),
            )
        return result
