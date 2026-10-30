from __future__ import annotations

import asyncio
from types import SimpleNamespace

import _metrics.pair_scan_runner as runner_mod
from _metrics.pair_scan import InboxAskTracker
from _metrics.pair_scan_runner import PairScanRunner


def test_ask_window_rotates_so_no_id_is_starved_past_the_cap():
    tracker = InboxAskTracker(pair_inbox_ask_max=3)
    ids = [f"m{i}" for i in range(8)]
    seen: set[str] = set()
    for _ in range(3):
        seen.update(tracker.ids_to_ask(ids, key=1))
    assert seen == set(ids)


def test_ask_window_unchanged_at_or_under_cap():
    tracker = InboxAskTracker(pair_inbox_ask_max=3)
    assert tracker.ids_to_ask(["a", "b", "c"], key=1) == ["a", "b", "c"]


def test_prune_matched_drops_ids_outside_every_window():
    tracker = InboxAskTracker()
    for mid in ("a", "b", "c"):
        tracker.mark_matched(mid)
    tracker.prune_matched({"b"})
    assert tracker.is_matched("b") and not tracker.is_matched("a")


def _mid(n: int, pipeline: int = 106) -> str:
    return f"MYSQL:order:{n}:1:p{pipeline}:uuid{n}"


class _FakeDriver:
    def __init__(self, inbox_ids: set[str]):
        self.inbox_ids = inbox_ids

    async def fetch_inbox_index(self, ids):
        return [{"message_id": i, "applied_at": None} for i in ids if i in self.inbox_ids]


def _run_ticks(monkeypatch, outbox_ids: list[str], inbox_by_tick: list[set[str]]):
    servers = [SimpleNamespace(id=1001, platform="mysql"), SimpleNamespace(id=1004, platform="mysql")]
    config = SimpleNamespace(servers=servers)
    settings = SimpleNamespace(
        queued_max_age_seconds=3600, e2e_window_seconds=150, overlap_seconds=5, lost_quiet_seconds=60,
        pair_inbox_ask_max=2000, pair_full_rescan_seconds=60, max_full_rescan_backoff=10,
    )
    pipelines_wire = [{"pipelineId": "1001:106", "sourceSystem": 1001, "targetSystem": 1004, "_hostServerId": 1001}]
    tick_state = {"i": 0}
    now_iso = "2026-09-30T10:00:00Z"

    def fake_build_driver(server, _settings):
        return _FakeDriver(inbox_by_tick[tick_state["i"]])

    async def fake_poll(self, **_kw):
        return [{"message_id": m, "pipeline_id": 106, "sent_at": now_iso} for m in outbox_ids] if self.server_id == "1001" else []

    monkeypatch.setattr(runner_mod, "build_driver", fake_build_driver)
    monkeypatch.setattr(runner_mod.OutboxScanCache, "poll", fake_poll)
    monkeypatch.setattr(runner_mod, "obj_status_for_reads", lambda *_a, **_k: runner_mod.ReadStatus.OK)

    async def go():
        runner = PairScanRunner()
        out = []
        for i in range(len(inbox_by_tick)):
            tick_state["i"] = i
            out.append(await runner.tick(config, settings, object(), pipelines_wire))
        return out

    return asyncio.run(go())


def test_packets_count_ids_newly_matched_between_ticks_and_skip_startup_history(monkeypatch):
    ids = [_mid(n) for n in range(10)]
    results = _run_ticks(monkeypatch, ids, [set(ids[:4]), set(ids[:4]), set(ids[:9]), set(ids)])
    assert [r.packets_by_pair.get((1001, 1004), 0) for r in results] == [0, 0, 5, 1]
    assert [r.packets_by_pipeline.get("1001:106", 0) for r in results] == [0, 0, 5, 1]


def test_a_row_ageing_out_of_the_window_is_not_a_delivery(monkeypatch):
    ids = [_mid(n) for n in range(4)]
    results = _run_ticks(monkeypatch, ids, [set(), set(), set()])
    assert all(r.packets_by_pair == {} for r in results)


def test_outbox_cache_evicts_rows_that_aged_out_of_the_window():
    from datetime import datetime, timedelta, timezone

    from _metrics.pair_scan import OutboxScanCache

    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    old_row = {"message_id": "old", "sent_at": iso(now - timedelta(seconds=5000))}
    new_row = {"message_id": "new", "sent_at": iso(now - timedelta(seconds=10))}
    cache = OutboxScanCache(server_id="1")

    async def fetch(_window):
        return [old_row, new_row]

    async def go():
        return await cache.poll(now=now, queued_max_age_seconds=3600, e2e_window_seconds=150, fetch_outbox_sent=fetch, now_monotonic=1000.0)

    rows = asyncio.run(go())
    assert [r["message_id"] for r in rows] == ["new"]
    assert set(cache._by_message_id) == {"new"}


def test_outbox_cache_evicts_even_when_the_fetch_fails():
    from datetime import datetime, timedelta, timezone

    from _metrics.pair_scan import OutboxScanCache

    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    cache = OutboxScanCache(server_id="1")

    async def ok(_w):
        return [{"message_id": "m", "sent_at": iso(now - timedelta(seconds=100))}]

    async def boom(_w):
        raise TimeoutError

    async def go():
        await cache.poll(now=now, queued_max_age_seconds=3600, e2e_window_seconds=150, fetch_outbox_sent=ok, now_monotonic=1000.0)
        later = now + timedelta(seconds=5000)
        return await cache.poll(now=later, queued_max_age_seconds=3600, e2e_window_seconds=150, fetch_outbox_sent=boom, now_monotonic=1010.0)

    assert asyncio.run(go()) == []
    assert cache._by_message_id == {}
