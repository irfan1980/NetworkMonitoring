from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _metrics.pair_scan import (
    InboxAskTracker,
    OutboxScanCache,
    PacketsTracker,
    ReconcileRangeCache,
    build_pair_views,
    classify_pair,
    clamp_packet_count,
    diff_pairs,
    inbox_ids_to_query,
    is_lost,
    match_outbox_against_inbox,
    scan_window_seconds,
)
from _metrics.readability import ReadStatus

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def test_match_outbox_against_inbox_p27():
    outbox = [{"message_id": "a"}, {"message_id": "b"}]
    inbox_index = {"a": "2026-01-01T00:00:00Z"}
    result = match_outbox_against_inbox(outbox, inbox_index)
    assert result == {"a": True, "b": False}


def test_matched_pair_never_queued_p28():
    c = classify_pair("m1", 101, _ago(500), matched=True, target_read_status=ReadStatus.OK,
                       now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    assert c.queued_trend is False
    assert c.queued_countable is False


def test_unmatched_within_window_not_queued_p28_fix02_shape():
    c = classify_pair("m2", 201, _ago(38), matched=False, target_read_status=ReadStatus.OK,
                       now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    assert c.queued_trend is False
    assert c.queued_countable is False


def test_unmatched_past_window_is_queued_p28_fix03_shape():
    c = classify_pair("m3", 301, _ago(398), matched=False, target_read_status=ReadStatus.OK,
                       now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    assert c.queued_trend is True
    assert c.queued_countable is True


def test_c19_overlap_seconds_widens_the_p28_boundary_not_a_bare_e2e_window():
    c = classify_pair("m4", 301, _ago(180), matched=False, target_read_status=ReadStatus.OK,
                       now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    assert c.queued_trend is False


def test_p29_trend_variable_includes_pairs_whose_target_is_not_ok_p30_excludes_them():
    ok = classify_pair("m5", 301, _ago(398), matched=False, target_read_status=ReadStatus.OK,
                        now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    down = classify_pair("m6", 301, _ago(398), matched=False, target_read_status=ReadStatus.DOWN,
                          now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    unread = classify_pair("m7", 301, _ago(398), matched=False, target_read_status=ReadStatus.UNREAD,
                            now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    assert ok.queued_trend is True and ok.queued_countable is True
    assert down.queued_trend is True and down.queued_countable is False
    assert unread.queued_trend is True and unread.queued_countable is False


def test_p32_pair_reported_on_targets_own_pipeline_id():
    c = classify_pair("m8", 301, _ago(398), matched=False, target_read_status=ReadStatus.OK,
                       now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    assert c.reported_on_pipeline_id == 301


def test_p33_both_views_share_the_identical_classification_fields():
    from _metrics.pair_scan import build_pair_views

    c = classify_pair("1001:ORDER:K:1:p101:uuid", 203, _ago(398), matched=False,
                       target_read_status=ReadStatus.OK, now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    sender_view, target_view = build_pair_views(c, sender_platform="mysql", target_platform="postgresql")

    for view in (sender_view, target_view):
        assert view.matched == c.matched
        assert view.age_seconds == c.age_seconds
        assert view.queued_trend == c.queued_trend
        assert view.queued_countable == c.queued_countable
        assert view.message_id == c.message_id


def test_p33_sender_and_target_pipeline_ids_are_independently_derived_not_tautological():
    from _metrics.pair_scan import build_pair_views

    c = classify_pair("1001:ORDER:FIX01-1:1:p101:uuid", 203, _ago(398), matched=False,
                       target_read_status=ReadStatus.OK, now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    sender_view, target_view = build_pair_views(c, sender_platform="mysql", target_platform="postgresql")

    assert sender_view.pipeline_id == 101
    assert target_view.pipeline_id == 203
    assert sender_view.pipeline_id != target_view.pipeline_id
    assert sender_view.direction == "outgoing"
    assert target_view.direction == "incoming"
    assert sender_view.platform == "mysql"
    assert target_view.platform == "postgresql"


def test_p33_falls_back_to_classification_pipeline_id_on_a_malformed_message_id():
    from _metrics.pair_scan import build_pair_views

    c = classify_pair("not-a-real-message-id", 203, _ago(398), matched=False,
                       target_read_status=ReadStatus.OK, now=NOW, e2e_window_seconds=150, overlap_seconds=60)
    sender_view, target_view = build_pair_views(c, sender_platform="mysql", target_platform="postgresql")
    assert sender_view.pipeline_id == 203


def test_o2_inbox_ids_to_query_comes_only_from_outbox_rows():
    from _metrics.pair_scan import inbox_ids_to_query

    outbox_rows = [{"message_id": "a"}, {"message_id": "b"}, {"message_id": "c"}]
    assert inbox_ids_to_query(outbox_rows) == ("a", "b", "c")
    assert inbox_ids_to_query([]) == ()


def test_o2_end_to_end_outbox_before_inbox_through_inbox_ask_tracker():
    outbox_rows = [{"message_id": "a"}, {"message_id": "b"}]
    tracker = InboxAskTracker(pair_inbox_ask_max=10)
    ids = inbox_ids_to_query(outbox_rows)
    asked = tracker.ids_to_ask(list(ids))
    assert asked == ["a", "b"]


def test_lost_requires_both_conditions_p31():
    assert is_lost(age_seconds=4000, matched=False, last_pipeline_delivery_age_seconds=10,
                    queued_max_age_seconds=3600, lost_quiet_seconds=120) is False
    assert is_lost(age_seconds=100, matched=False, last_pipeline_delivery_age_seconds=200,
                    queued_max_age_seconds=3600, lost_quiet_seconds=120) is False
    assert is_lost(age_seconds=4000, matched=False, last_pipeline_delivery_age_seconds=200,
                    queued_max_age_seconds=3600, lost_quiet_seconds=120) is True


def test_lost_matched_pair_never_lost():
    assert is_lost(age_seconds=99999, matched=True, last_pipeline_delivery_age_seconds=None,
                    queued_max_age_seconds=3600, lost_quiet_seconds=120) is False


def test_fix09_shape_old_row_stays_out_of_lost_despite_its_own_age_p31_p93_c9():
    row_a_lost = is_lost(
        age_seconds=4000, matched=False, last_pipeline_delivery_age_seconds=24,
        queued_max_age_seconds=3600, lost_quiet_seconds=120,
    )
    assert row_a_lost is False
    row_a_lost_without_b = is_lost(
        age_seconds=4000, matched=False, last_pipeline_delivery_age_seconds=None,
        queued_max_age_seconds=3600, lost_quiet_seconds=120,
    )
    assert row_a_lost_without_b is True


def test_fix04_shape_no_other_traffic_lost_p31():
    assert is_lost(age_seconds=3998, matched=False, last_pipeline_delivery_age_seconds=None,
                    queued_max_age_seconds=3600, lost_quiet_seconds=120) is True


def test_lost_default_target_status_ok_matches_prior_behaviour():
    assert is_lost(age_seconds=4000, matched=False, last_pipeline_delivery_age_seconds=200,
                    queued_max_age_seconds=3600, lost_quiet_seconds=120) is True


def test_lost_excludes_a_down_target_p30_style_gate():
    assert is_lost(
        age_seconds=4000, matched=False, last_pipeline_delivery_age_seconds=200,
        queued_max_age_seconds=3600, lost_quiet_seconds=120,
        target_read_status=ReadStatus.DOWN,
    ) is False


def test_lost_excludes_an_unread_target():
    assert is_lost(
        age_seconds=99999, matched=False, last_pipeline_delivery_age_seconds=None,
        queued_max_age_seconds=3600, lost_quiet_seconds=120,
        target_read_status=ReadStatus.UNREAD,
    ) is False


def test_lost_still_fires_when_target_explicitly_ok():
    assert is_lost(
        age_seconds=4000, matched=False, last_pipeline_delivery_age_seconds=200,
        queued_max_age_seconds=3600, lost_quiet_seconds=120,
        target_read_status=ReadStatus.OK,
    ) is True


def test_scan_window_seconds_o15():
    assert scan_window_seconds(queued_max_age_seconds=3600, e2e_window_seconds=150) == 3900
    assert scan_window_seconds(queued_max_age_seconds=100, e2e_window_seconds=150) == 450


def test_o15_scan_window_never_narrower_than_either_input_the_invariant_it_relies_on():
    for queued_max_age_seconds, e2e_window_seconds in [
        (3600, 150), (150, 3600), (100, 100), (0, 500), (7200, 1),
    ]:
        window = scan_window_seconds(queued_max_age_seconds, e2e_window_seconds)
        assert window >= queued_max_age_seconds
        assert window >= e2e_window_seconds
        assert window == max(queued_max_age_seconds, e2e_window_seconds) + 300


async def test_outbox_scan_cache_incremental_merges_new_rows():
    cache = OutboxScanCache(server_id="1001", pair_full_rescan_seconds=6000)
    calls = []

    async def fetch(window_seconds):
        calls.append(window_seconds)
        return [{"message_id": "a", "sent_at": (NOW - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")}]

    rows1 = await cache.poll(NOW, 3600, 150, fetch, now_monotonic=0.0)
    assert {r["message_id"] for r in rows1} == {"a"}
    assert calls[0] == scan_window_seconds(3600, 150)

    calls2 = []

    async def fetch2(window_seconds):
        calls2.append(window_seconds)
        return [{"message_id": "b", "sent_at": (NOW - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")}]

    rows2 = await cache.poll(NOW, 3600, 150, fetch2, now_monotonic=5.0)
    assert {r["message_id"] for r in rows2} == {"a", "b"}
    assert calls2[0] == 15
    assert calls2[0] < calls[0]


async def test_outbox_scan_cache_full_rescan_retires_a_row_p98():
    cache = OutboxScanCache(server_id="1001", pair_full_rescan_seconds=10)

    async def fetch_with_a(window_seconds):
        return [{"message_id": "a", "sent_at": (NOW - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")}]

    await cache.poll(NOW, 3600, 150, fetch_with_a, now_monotonic=0.0)

    async def fetch_without_a(window_seconds):
        return []

    rows_mid = await cache.poll(NOW, 3600, 150, fetch_without_a, now_monotonic=0.001)
    assert {r["message_id"] for r in rows_mid} == {"a"}

    rows_after = await cache.poll(NOW, 3600, 150, fetch_without_a, now_monotonic=100.0)
    assert rows_after == []


async def test_outbox_scan_cache_hot_cycle_guard_never_two_full_rescans_back_to_back():
    cache = OutboxScanCache(server_id="1001", pair_full_rescan_seconds=1)
    calls = []

    async def fetch(window_seconds):
        calls.append(window_seconds)
        return []

    await cache.poll(NOW, 3600, 150, fetch, now_monotonic=0.0)
    assert cache._last_poll_was_full_rescan is True
    await cache.poll(NOW, 3600, 150, fetch, now_monotonic=0.001)
    assert cache._last_poll_was_full_rescan is False


async def test_outbox_scan_cache_exponential_backoff_on_repeated_failures():
    cache = OutboxScanCache(server_id="1001", pair_full_rescan_seconds=10, max_full_rescan_backoff=4)

    async def failing_fetch(window_seconds):
        raise RuntimeError("db unreachable")

    await cache.poll(NOW, 3600, 150, failing_fetch, now_monotonic=0.0)
    assert cache._consecutive_failures == 1
    await cache.poll(NOW, 3600, 150, failing_fetch, now_monotonic=1.0)
    assert cache._consecutive_failures == 2
    for i in range(10):
        await cache.poll(NOW, 3600, 150, failing_fetch, now_monotonic=2.0 + i)
    assert cache._effective_rescan_interval() == 10 * 4


async def test_outbox_scan_cache_backoff_genuinely_defers_a_rescan_not_just_arithmetic():
    cache = OutboxScanCache(server_id="1001", pair_full_rescan_seconds=10, max_full_rescan_backoff=4)
    calls: list[int] = []
    full_window = scan_window_seconds(3600, 150)

    async def failing_fetch(window_seconds):
        raise RuntimeError("db unreachable")

    async def ok_fetch(window_seconds):
        calls.append(window_seconds)
        return []

    async def fetch_with_row(window_seconds):
        calls.append(window_seconds)
        return [{"message_id": "z", "sent_at": (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")}]

    await cache.poll(NOW, 3600, 150, fetch_with_row, now_monotonic=0.0)
    assert calls[-1] == full_window

    await cache.poll(NOW, 3600, 150, failing_fetch, now_monotonic=1.0)
    assert cache._consecutive_failures == 1

    await cache.poll(NOW, 3600, 150, ok_fetch, now_monotonic=12.0)
    assert calls[-1] < full_window

    await cache.poll(NOW, 3600, 150, ok_fetch, now_monotonic=25.0)
    assert calls[-1] == full_window


async def test_outbox_scan_cache_stagger_offset_delays_first_full_rescan():
    cache = OutboxScanCache(server_id="1002", pair_full_rescan_seconds=60, stagger_offset_seconds=30.0)

    async def fetch(window_seconds):
        return []

    await cache.poll(NOW, 3600, 150, fetch, now_monotonic=5.0)
    assert cache._last_poll_was_full_rescan is False
    await cache.poll(NOW, 3600, 150, fetch, now_monotonic=35.0)
    assert cache._last_poll_was_full_rescan is True


async def test_outbox_scan_cache_serves_stale_cache_on_a_failed_fetch_not_empty():
    cache = OutboxScanCache(server_id="1001", pair_full_rescan_seconds=10000)

    async def fetch_with_row(window_seconds):
        return [{"message_id": "z", "sent_at": (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")}]

    rows1 = await cache.poll(NOW, 3600, 150, fetch_with_row, now_monotonic=0.0)
    assert {r["message_id"] for r in rows1} == {"z"}

    async def failing_fetch(window_seconds):
        raise RuntimeError("db unreachable")

    rows2 = await cache.poll(NOW, 3600, 150, failing_fetch, now_monotonic=1.0)
    assert {r["message_id"] for r in rows2} == {"z"}
    assert cache._consecutive_failures == 1


def test_reconcile_range_cache_merges_overlapping_ranges_p99():
    cache = ReconcileRangeCache()
    cache.mark_covered(_ago(100), _ago(50))
    cache.mark_covered(_ago(60), _ago(10))
    assert cache.is_covered(_ago(30)) is True
    assert len(cache.covered_ranges()) == 1
    assert cache.is_covered(_ago(200)) is False


def test_inbox_ask_tracker_caps_and_remembers_matched_p100():
    tracker = InboxAskTracker(pair_inbox_ask_max=2)
    ids = ["a", "b", "c"]
    asked = tracker.ids_to_ask(ids)
    assert asked == ["a", "b"]

    tracker.mark_matched("a")
    asked2 = tracker.ids_to_ask(ids)
    assert "a" not in asked2
    assert asked2 == ["b", "c"]


def test_packets_tracker_is_a_set_diff_not_a_time_window_p97():
    tracker = PacketsTracker()
    d1 = tracker.diff(101, {"a", "b"})
    assert d1.added == {"a", "b"}
    assert d1.removed == frozenset()

    d2 = tracker.diff(101, {"b", "c"})
    assert d2.added == {"c"}
    assert d2.removed == {"a"}
    assert d2.still_in_flight == {"b"}


def test_clamp_packet_count_p126():
    assert clamp_packet_count(-5) == 0
    assert clamp_packet_count(0) == 0
    assert clamp_packet_count(23.4) == 23
    assert clamp_packet_count(23.6) == 24
    assert clamp_packet_count(9999) == 50


def test_diff_pairs_always_includes_id_and_packets_p127():
    previous = {"id": "p1", "packets": ["a"], "queuedCount": 5, "lostCount": 0}
    current = {"id": "p1", "packets": ["a"], "queuedCount": 5, "lostCount": 0}
    patch = diff_pairs(previous, current)
    assert patch == {"id": "p1", "packets": ["a"]}


def test_diff_pairs_includes_changed_fields_too():
    previous = {"id": "p1", "packets": ["a"], "queuedCount": 5}
    current = {"id": "p1", "packets": ["a", "b"], "queuedCount": 9}
    patch = diff_pairs(previous, current)
    assert patch == {"id": "p1", "packets": ["a", "b"], "queuedCount": 9}


def test_diff_pairs_first_ever_patch_has_no_previous():
    current = {"id": "p1", "packets": [], "queuedCount": 0}
    patch = diff_pairs(None, current)
    assert patch == {"id": "p1", "packets": [], "queuedCount": 0}


async def test_fix01_matched_pair_real_data(mysql_scenario, postgresql_scenario):
    from _contract.schema import ServerEntry, Settings
    from _db.mysql import MySQLDriver
    from _db.postgresql import PostgreSQLDriver
    from _fixtures import env

    facts = mysql_scenario("FIX01")
    settings = Settings(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
    )
    mysql_creds = env.mysql_credentials()
    mysql_driver = MySQLDriver(
        ServerEntry(id=1001, name="MySQL", platform="mysql", host=mysql_creds["host"], port=mysql_creds["port"],
                    user=mysql_creds["user"], password=mysql_creds["password"], database=mysql_creds["database"]),
        settings,
    )
    pg_creds = env.postgresql_credentials()
    pg_driver = PostgreSQLDriver(
        ServerEntry(id=1002, name="PostgreSQL", platform="postgresql", host=pg_creds["host"], port=pg_creds["port"],
                    user=pg_creds["user"], password=pg_creds["password"], database=pg_creds["dbname"], schema="sync"),
        settings,
    )

    outbox_rows = await mysql_driver.fetch_outbox_sent(scan_window_seconds(3600, 150))
    row = next(r for r in outbox_rows if r["message_id"] == facts["message_id"])
    inbox_rows = await pg_driver.fetch_inbox_index((facts["message_id"],))
    inbox_index = {r["message_id"]: r["applied_at"] for r in inbox_rows}

    matches = match_outbox_against_inbox([row], inbox_index)
    assert matches[facts["message_id"]] is True

    from datetime import datetime as dt
    sent_at = dt.fromisoformat(row["sent_at"].replace("Z", "+00:00"))
    now = dt.now(sent_at.tzinfo)
    c = classify_pair(facts["message_id"], 101, sent_at, matched=True, target_read_status=ReadStatus.OK,
                       now=now, e2e_window_seconds=150, overlap_seconds=60)
    assert c.matched is True
    assert c.queued_trend is False


async def test_fix02_unmatched_within_window_real_data(postgresql_scenario):
    from _contract.schema import ServerEntry, Settings
    from _db.mysql import MySQLDriver
    from _db.postgresql import PostgreSQLDriver
    from _fixtures import env

    facts = postgresql_scenario("FIX02")
    settings = Settings(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
    )
    pg_creds = env.postgresql_credentials()
    pg_driver = PostgreSQLDriver(
        ServerEntry(id=1002, name="PostgreSQL", platform="postgresql", host=pg_creds["host"], port=pg_creds["port"],
                    user=pg_creds["user"], password=pg_creds["password"], database=pg_creds["dbname"], schema="sync"),
        settings,
    )
    mysql_creds = env.mysql_credentials()
    mysql_driver = MySQLDriver(
        ServerEntry(id=1001, name="MySQL", platform="mysql", host=mysql_creds["host"], port=mysql_creds["port"],
                    user=mysql_creds["user"], password=mysql_creds["password"], database=mysql_creds["database"]),
        settings,
    )

    outbox_rows = await pg_driver.fetch_outbox_sent(scan_window_seconds(3600, 150))
    row = next(r for r in outbox_rows if r["message_id"] == facts["message_id"])
    inbox_rows = await mysql_driver.fetch_inbox_index((facts["message_id"],))
    inbox_index = {r["message_id"]: r["applied_at"] for r in inbox_rows}
    matched = match_outbox_against_inbox([row], inbox_index)[facts["message_id"]]
    assert matched is False

    from datetime import datetime as dt
    sent_at = dt.fromisoformat(row["sent_at"].replace("Z", "+00:00"))
    now = dt.now(sent_at.tzinfo)
    c = classify_pair(facts["message_id"], 201, sent_at, matched=False, target_read_status=ReadStatus.OK,
                       now=now, e2e_window_seconds=150, overlap_seconds=60)
    assert c.queued_trend is False


async def test_fix04_lost_real_data(mysql_scenario):
    from _contract.schema import ServerEntry, Settings
    from _db.mysql import MySQLDriver
    from _fixtures import env

    facts = mysql_scenario("FIX04")
    settings = Settings(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
    )
    mysql_creds = env.mysql_credentials()
    mysql_driver = MySQLDriver(
        ServerEntry(id=1001, name="MySQL", platform="mysql", host=mysql_creds["host"], port=mysql_creds["port"],
                    user=mysql_creds["user"], password=mysql_creds["password"], database=mysql_creds["database"]),
        settings,
    )
    outbox_rows = await mysql_driver.fetch_outbox_sent(4400)
    row = next(r for r in outbox_rows if r["message_id"] == facts["message_id"])

    from datetime import datetime as dt
    sent_at = dt.fromisoformat(row["sent_at"].replace("Z", "+00:00"))
    now = dt.now(sent_at.tzinfo)
    age_seconds = (now - sent_at).total_seconds()
    assert age_seconds > 3600
    lost = is_lost(age_seconds, matched=False, last_pipeline_delivery_age_seconds=None,
                    queued_max_age_seconds=3600, lost_quiet_seconds=120)
    assert lost is True


async def test_fix09_independence_of_conditions_real_data(postgresql_scenario, mysql_scenario):
    from _contract.schema import ServerEntry, Settings
    from _db.mysql import MySQLDriver
    from _db.postgresql import PostgreSQLDriver
    from _fixtures import env

    facts = postgresql_scenario("FIX09")
    settings = Settings(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
    )
    pg_creds = env.postgresql_credentials()
    pg_driver = PostgreSQLDriver(
        ServerEntry(id=1002, name="PostgreSQL", platform="postgresql", host=pg_creds["host"], port=pg_creds["port"],
                    user=pg_creds["user"], password=pg_creds["password"], database=pg_creds["dbname"], schema="sync"),
        settings,
    )
    mysql_creds = env.mysql_credentials()
    mysql_driver = MySQLDriver(
        ServerEntry(id=1001, name="MySQL", platform="mysql", host=mysql_creds["host"], port=mysql_creds["port"],
                    user=mysql_creds["user"], password=mysql_creds["password"], database=mysql_creds["database"]),
        settings,
    )

    outbox_rows = await pg_driver.fetch_outbox_sent(4400)
    row_old = next(r for r in outbox_rows if r["message_id"] == facts["message_id_old"])

    inbox_since_rows = await mysql_driver.fetch_inbox_since(3600)
    pipeline_103_deliveries = [r for r in inbox_since_rows if r["pipeline_id"] == 103]
    assert pipeline_103_deliveries, "FIX09's Row B must be a real, recent delivery on pipeline 103"

    from datetime import datetime as dt, timezone as tz
    now = dt.now(tz.utc)

    assert isinstance(pipeline_103_deliveries[0]["received_at"], str)
    assert pipeline_103_deliveries[0]["received_at"].endswith("Z")

    def _received_at(r):
        return dt.fromisoformat(r["received_at"].replace("Z", "+00:00"))

    most_recent_delivery_age = min((now - _received_at(r)).total_seconds() for r in pipeline_103_deliveries)
    assert most_recent_delivery_age < 120

    sent_at_old = dt.fromisoformat(row_old["sent_at"].replace("Z", "+00:00"))
    age_old = (now - sent_at_old).total_seconds()
    assert age_old > 3600

    lost = is_lost(age_old, matched=False, last_pipeline_delivery_age_seconds=most_recent_delivery_age,
                    queued_max_age_seconds=3600, lost_quiet_seconds=120)
    assert lost is False


def _settings(**overrides):
    from _contract.schema import Settings

    base = {
        "auth": {
            "web_client_id": "w", "web_client_secret": "w",
            "app_client_id": "a", "app_client_secret": "a",
        },
        "ack_pin": "1234",
    }
    base.update(overrides)
    return Settings.model_validate(base)


def test_outbox_scan_cache_from_settings_reads_c10_c20_not_hardcoded():
    settings = _settings(pair_full_rescan_seconds=999, max_full_rescan_backoff=7)
    cache = OutboxScanCache.from_settings(settings, server_id="1001")
    assert cache.pair_full_rescan_seconds == 999
    assert cache.max_full_rescan_backoff == 7


def test_outbox_scan_cache_from_settings_value_actually_changes_rescan_timing_c10():
    settings = _settings(pair_full_rescan_seconds=5)
    cache = OutboxScanCache.from_settings(settings, server_id="1001")
    cache._last_full_rescan_monotonic = 0.0
    cache._last_poll_was_full_rescan = False
    assert cache._due_for_full_rescan(now_monotonic=5.0) is True
    assert cache._due_for_full_rescan(now_monotonic=4.0) is False


def test_inbox_ask_tracker_from_settings_reads_c11_not_hardcoded():
    settings = _settings(pair_inbox_ask_max=3)
    tracker = InboxAskTracker.from_settings(settings)
    assert tracker.pair_inbox_ask_max == 3
    asked = tracker.ids_to_ask(["a", "b", "c", "d", "e"])
    assert asked == ["a", "b", "c"]
