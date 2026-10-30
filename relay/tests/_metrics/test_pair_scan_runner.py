from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from _metrics.pair_scan_runner import PairScanRunner
from _metrics.readability import ReadStatus

@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _msg_id(entity_key: str, pipeline_id: int, uuid: str = "u1") -> str:
    return f"mysql:ORDER:{entity_key}:1:p{pipeline_id}:{uuid}"


class _FakeDriver:
    def __init__(self, outbox_rows=None, inbox_index_rows=None):
        self._outbox_rows = outbox_rows or []
        self._inbox_index_rows = inbox_index_rows or []
        self.outbox_calls = 0
        self.inbox_index_calls = 0

    async def fetch_outbox_sent(self, scan_window_seconds: int):
        self.outbox_calls += 1
        return list(self._outbox_rows)

    async def fetch_inbox_index(self, message_ids: tuple[str, ...]):
        self.inbox_index_calls += 1
        asked = set(message_ids)
        return [r for r in self._inbox_index_rows if r["message_id"] in asked]


@dataclass
class _FakeServer:
    id: int
    platform: str
    name: str = "s"
    used_buses: list = field(default_factory=list)


@dataclass
class _FakeConfig:
    servers: list


class _FakeSettings(SimpleNamespace):
    pass


def _settings(**overrides) -> _FakeSettings:
    base = dict(
        queued_max_age_seconds=3600,
        e2e_window_seconds=150,
        overlap_seconds=60,
        lost_quiet_seconds=120,
        pair_full_rescan_seconds=60,
        max_full_rescan_backoff=10,
        pair_inbox_ask_max=2000,
    )
    base.update(overrides)
    return _FakeSettings(**base)


class _FakeDownConfirm:

    def is_currently_unread(self, object_id: str) -> bool:
        return False

    def confirmed_status(self, object_id: str) -> ReadStatus:
        return ReadStatus.OK


def _pipelines_wire(pipeline_id: int, from_id: int, to_id: int) -> list[dict]:
    return [{"pipelineId": f"{from_id}:{pipeline_id}", "sourceSystem": from_id, "targetSystem": to_id, "_hostServerId": from_id}]


def _patch_drivers(monkeypatch, drivers: dict[int, _FakeDriver]) -> None:
    def _build(server, settings):
        return drivers[server.id]

    monkeypatch.setattr("_metrics.pair_scan_runner.build_driver", _build)


def test_old_unmatched_message_with_quiet_delivery_is_lost(monkeypatch, now):
    sender, target = _FakeServer(id=1, platform="mysql"), _FakeServer(id=2, platform="postgresql")
    config = _FakeConfig(servers=[sender, target])
    settings = _settings()
    old_sent = now - timedelta(seconds=3700)
    row = {"pipeline_id": 101, "message_id": _msg_id("OLD-1", 101), "sent_at": _iso(old_sent), "created_at": _iso(old_sent)}
    drivers = {1: _FakeDriver(outbox_rows=[row]), 2: _FakeDriver(inbox_index_rows=[])}
    _patch_drivers(monkeypatch, drivers)

    runner = PairScanRunner()
    result = asyncio.run(runner.tick(config, settings, _FakeDownConfirm(), _pipelines_wire(101, 1, 2)))

    assert result.lost_by_pair.get((1, 2), 0) == 1
    assert result.lost_by_pipeline.get("1:101", 0) == 1


def test_old_unmatched_message_with_recent_delivery_on_same_pipeline_is_not_lost(monkeypatch, now):
    sender, target = _FakeServer(id=1, platform="mysql"), _FakeServer(id=2, platform="postgresql")
    config = _FakeConfig(servers=[sender, target])
    settings = _settings()
    old_sent = now - timedelta(seconds=3700)
    recent_sent = now - timedelta(seconds=30)
    row_a = {"pipeline_id": 101, "message_id": _msg_id("OLD-1", 101), "sent_at": _iso(old_sent), "created_at": _iso(old_sent)}
    row_b = {"pipeline_id": 101, "message_id": _msg_id("RECENT-1", 101), "sent_at": _iso(recent_sent), "created_at": _iso(recent_sent)}
    drivers = {
        1: _FakeDriver(outbox_rows=[row_a, row_b]),
        2: _FakeDriver(inbox_index_rows=[{"message_id": row_b["message_id"], "applied_at": _iso(now)}]),
    }
    _patch_drivers(monkeypatch, drivers)

    runner = PairScanRunner()
    result = asyncio.run(runner.tick(config, settings, _FakeDownConfirm(), _pipelines_wire(101, 1, 2)))

    assert result.lost_by_pair.get((1, 2), 0) == 0
    assert result.lost_by_pipeline.get("1:101", 0) == 0
    assert result.queued_by_pair.get((1, 2), 0) == 1


def test_recent_unmatched_message_is_neither_queued_nor_lost(monkeypatch, now):
    sender, target = _FakeServer(id=1, platform="mysql"), _FakeServer(id=2, platform="postgresql")
    config = _FakeConfig(servers=[sender, target])
    settings = _settings()
    recent_sent = now - timedelta(seconds=10)
    row = {"pipeline_id": 101, "message_id": _msg_id("FRESH-1", 101), "sent_at": _iso(recent_sent), "created_at": _iso(recent_sent)}
    drivers = {1: _FakeDriver(outbox_rows=[row]), 2: _FakeDriver(inbox_index_rows=[])}
    _patch_drivers(monkeypatch, drivers)

    runner = PairScanRunner()
    result = asyncio.run(runner.tick(config, settings, _FakeDownConfirm(), _pipelines_wire(101, 1, 2)))

    assert result.lost_by_pair.get((1, 2), 0) == 0
    assert result.queued_by_pair.get((1, 2), 0) == 0


def test_matched_message_is_never_lost_regardless_of_age(monkeypatch, now):
    sender, target = _FakeServer(id=1, platform="mysql"), _FakeServer(id=2, platform="postgresql")
    config = _FakeConfig(servers=[sender, target])
    settings = _settings()
    old_sent = now - timedelta(seconds=3700)
    row = {"pipeline_id": 101, "message_id": _msg_id("OLD-MATCHED", 101), "sent_at": _iso(old_sent), "created_at": _iso(old_sent)}
    drivers = {
        1: _FakeDriver(outbox_rows=[row]),
        2: _FakeDriver(inbox_index_rows=[{"message_id": row["message_id"], "applied_at": _iso(now)}]),
    }
    _patch_drivers(monkeypatch, drivers)

    runner = PairScanRunner()
    result = asyncio.run(runner.tick(config, settings, _FakeDownConfirm(), _pipelines_wire(101, 1, 2)))

    assert result.lost_by_pair.get((1, 2), 0) == 0
    assert result.queued_by_pair.get((1, 2), 0) == 0
    assert result.matched_recently_by_pipeline.get("1:101") is True


def test_outbox_cache_persists_state_across_ticks(monkeypatch, now):
    sender, target = _FakeServer(id=1, platform="mysql"), _FakeServer(id=2, platform="postgresql")
    config = _FakeConfig(servers=[sender, target])
    settings = _settings(pair_full_rescan_seconds=999999)
    row = {"pipeline_id": 101, "message_id": _msg_id("A", 101), "sent_at": _iso(now - timedelta(seconds=10)), "created_at": _iso(now)}
    outbox_driver = _FakeDriver(outbox_rows=[row])
    drivers = {1: outbox_driver, 2: _FakeDriver(inbox_index_rows=[])}
    _patch_drivers(monkeypatch, drivers)

    runner = PairScanRunner()
    asyncio.run(runner.tick(config, settings, _FakeDownConfirm(), _pipelines_wire(101, 1, 2)))
    asyncio.run(runner.tick(config, settings, _FakeDownConfirm(), _pipelines_wire(101, 1, 2)))

    assert outbox_driver.outbox_calls == 2
    assert runner._outbox_caches[1] is runner._outbox_caches[sender.id]


def test_one_unreachable_server_does_not_blank_another_servers_result(monkeypatch, now):
    class _RaisingDriver:
        async def fetch_outbox_sent(self, scan_window_seconds):
            raise ConnectionError("simulated TCP timeout")

    sender_ok, sender_down, target = _FakeServer(id=1, platform="mysql"), _FakeServer(id=3, platform="mssql"), _FakeServer(id=2, platform="postgresql")
    config = _FakeConfig(servers=[sender_ok, sender_down, target])
    settings = _settings()
    old_sent = now - timedelta(seconds=3700)
    row = {"pipeline_id": 101, "message_id": _msg_id("OLD-1", 101), "sent_at": _iso(old_sent), "created_at": _iso(old_sent)}
    drivers = {1: _FakeDriver(outbox_rows=[row]), 3: _RaisingDriver(), 2: _FakeDriver(inbox_index_rows=[])}
    _patch_drivers(monkeypatch, drivers)

    pipelines_wire = _pipelines_wire(101, 1, 2) + [{"pipelineId": "3:201", "sourceSystem": 3, "targetSystem": 2, "_hostServerId": 3}]

    runner = PairScanRunner()
    result = asyncio.run(runner.tick(config, settings, _FakeDownConfirm(), pipelines_wire))

    assert result.any_scan_succeeded is True
    assert result.lost_by_pair.get((1, 2), 0) == 1
    assert result.scan_ok_by_server[1] is True
    assert result.scan_ok_by_server[3] is False


def test_scan_ok_by_server_all_true_when_every_server_succeeds(monkeypatch, now):
    sender, target = _FakeServer(id=1, platform="mysql"), _FakeServer(id=2, platform="postgresql")
    config = _FakeConfig(servers=[sender, target])
    settings = _settings()
    drivers = {1: _FakeDriver(outbox_rows=[]), 2: _FakeDriver(inbox_index_rows=[])}
    _patch_drivers(monkeypatch, drivers)

    runner = PairScanRunner()
    result = asyncio.run(runner.tick(config, settings, _FakeDownConfirm(), _pipelines_wire(101, 1, 2)))

    assert result.scan_ok_by_server == {1: True, 2: True}
