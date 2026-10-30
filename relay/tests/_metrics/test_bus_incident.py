from __future__ import annotations

from _metrics.bus_incident import (
    BusRawStreakTracker,
    LostAlertTracker,
    PipelineAttribution,
    apply_sound_gate,
    classify_pair_root_cause_kind,
    resolve_bus_incident,
)
from _metrics.readability import ReadStatus

OK = ReadStatus.OK
DOWN = ReadStatus.DOWN
UNREAD = ReadStatus.UNREAD


def _resolve(**overrides):
    base = dict(
        bus_health_status=OK,
        bus_endpoint_status=OK,
        bus_health_object_id="bus:1:health",
        bus_endpoint_object_id="bus:1:endpoint",
        target_db_status=OK,
        target_db_object_id="db:mysql:1001:query",
        target_heartbeat_class="ok",
        target_read_status=OK,
        source_read_status=OK,
        queued_data_available=True,
        last_known=None,
    )
    base.update(overrides)
    return resolve_bus_incident(**base)


def test_p26_sender_checked_before_receiver_before_bus():
    assert classify_pair_root_cause_kind(DOWN, OK, bus_not_ok=True) == "sender"
    assert classify_pair_root_cause_kind(OK, DOWN, bus_not_ok=True) == "receiver"
    assert classify_pair_root_cause_kind(OK, OK, bus_not_ok=True) == "bus"
    assert classify_pair_root_cause_kind(OK, OK, bus_not_ok=False) == "normal"


def test_rule1_bus_down_hand_over():
    r = _resolve(bus_health_status=DOWN)
    assert r == PipelineAttribution(cause="bus_down", blocked_by="bus:1:health")


def test_rule1_bus_endpoint_down_also_hands_over():
    r = _resolve(bus_endpoint_status=DOWN)
    assert r == PipelineAttribution(cause="bus_down", blocked_by="bus:1:endpoint")


def test_rule2_receiver_down_only_when_bus_is_ok():
    r = _resolve(target_db_status=DOWN)
    assert r == PipelineAttribution(cause="receiver_down", blocked_by="db:mysql:1001:query")


def test_rule1_beats_rule2_when_both_are_down_p21_exactly_one_attribution():
    r = _resolve(bus_health_status=DOWN, target_db_status=DOWN)
    assert r.cause == "bus_down"


def test_rule3_receiver_stale_freeze_keeps_last_known():
    prior = PipelineAttribution(cause="normal", blocked_by=None)
    r = _resolve(target_heartbeat_class="blocked_by_relay", last_known=prior)
    assert r == prior


def test_rule3_receiver_stale_freeze_with_no_prior_state():
    r = _resolve(target_heartbeat_class="down", last_known=None)
    assert r.cause == "receiver_stale_frozen"


def test_rule4_platform_unreadable_freeze():
    prior = PipelineAttribution(cause="receiver_down", blocked_by="db:x")
    r = _resolve(target_read_status=UNREAD, last_known=prior)
    assert r == prior
    r2 = _resolve(source_read_status=UNREAD, last_known=prior)
    assert r2 == prior


def test_rule5_queued_unknown_freeze():
    prior = PipelineAttribution(cause="bus_down", blocked_by="bus:1:health")
    r = _resolve(queued_data_available=False, last_known=prior)
    assert r == prior


def test_normal_case_when_nothing_overrides():
    r = _resolve()
    assert r == PipelineAttribution(cause="normal", blocked_by=None)


def test_fixed_order_rule1_checked_before_rule5_even_if_both_conditions_true():
    r = _resolve(bus_health_status=DOWN, queued_data_available=False, last_known=None)
    assert r.cause == "bus_down"


def test_bus_raw_streak_tracker_counts_consecutive_raw_non_ok():
    tracker = BusRawStreakTracker()
    assert tracker.observe("bus:1:health", DOWN) == 1
    assert tracker.observe("bus:1:health", DOWN) == 2
    assert tracker.observe("bus:1:health", OK) == 0
    assert tracker.streak("bus:1:health") == 0


def test_bus_raw_streak_tracker_keys_independently_p96():
    tracker = BusRawStreakTracker()
    tracker.observe("bus:1:health", DOWN)
    tracker.observe("bus:1:health", DOWN)
    tracker.observe("bus:2:health", DOWN)
    assert tracker.streak("bus:1:health") == 2
    assert tracker.streak("bus:2:health") == 1


def test_lost_alert_raises_immediately_no_2_poll_confirmation_p94():
    now = [0.0]
    tracker = LostAlertTracker(bus_incident_stale_after_ms=120000, now_ms=lambda: now[0])
    tick = tracker.observe("bus:1:pipe:102", lost_count=1, trigger_severity=2)
    assert tick.newly_raised is True
    assert tick.active is True
    assert tick.severity == 2


def test_lost_alert_severity_capped_at_2():
    now = [0.0]
    tracker = LostAlertTracker(now_ms=lambda: now[0])
    tick = tracker.observe("bus:1:pipe:102", lost_count=1, trigger_severity=5)
    assert tick.severity == 2


def test_lost_alert_resolves_only_after_2_zero_polls_held_for_stale_after_ms():
    now = [0.0]
    tracker = LostAlertTracker(bus_incident_stale_after_ms=120000, now_ms=lambda: now[0])
    tracker.observe("k", lost_count=1, trigger_severity=2)

    now[0] = 1000.0
    tick1 = tracker.observe("k", lost_count=0, trigger_severity=2)
    assert tick1.newly_resolved is False
    assert tick1.active is True

    now[0] = 2000.0
    tick2 = tracker.observe("k", lost_count=0, trigger_severity=2)
    assert tick2.newly_resolved is False

    now[0] = 1000.0 + 120000.0
    tick3 = tracker.observe("k", lost_count=0, trigger_severity=2)
    assert tick3.newly_resolved is True
    assert tick3.active is False


def test_lost_alert_never_suppressed_by_a_down_bus_or_receiver_structurally():
    now = [0.0]
    tracker = LostAlertTracker(now_ms=lambda: now[0])
    tick = tracker.observe("k", lost_count=3, trigger_severity=1)
    assert tick.active is True
    assert tick.severity == 1


def test_lost_alert_multi_bus_independent_streaks_p96():
    now = [0.0]
    tracker = LostAlertTracker(now_ms=lambda: now[0])
    tracker.observe("bus:1:pipe:102", lost_count=1, trigger_severity=2)
    tracker.observe("bus:2:pipe:301", lost_count=0, trigger_severity=2)
    assert tracker.observe("bus:1:pipe:102", lost_count=1, trigger_severity=2).active is True
    assert tracker.observe("bus:2:pipe:301", lost_count=0, trigger_severity=2).active is False


def test_sound_gate_strips_alarm_below_threshold_p101():
    assert apply_sound_gate(wants_alarm=True, oldest_pending_packet_age_seconds=30, sound_min_oldest_packet_age_seconds=60) is False


def test_sound_gate_allows_alarm_above_threshold():
    assert apply_sound_gate(wants_alarm=True, oldest_pending_packet_age_seconds=90, sound_min_oldest_packet_age_seconds=60) is True


def test_sound_gate_never_adds_an_alarm_that_wasnt_wanted():
    assert apply_sound_gate(wants_alarm=False, oldest_pending_packet_age_seconds=9999, sound_min_oldest_packet_age_seconds=60) is False


def test_sound_gate_no_packets_means_no_sound():
    assert apply_sound_gate(wants_alarm=True, oldest_pending_packet_age_seconds=None, sound_min_oldest_packet_age_seconds=60) is False


def test_sound_gate_is_stateless_o5():
    assert apply_sound_gate(True, 30, 60) is False
    assert apply_sound_gate(True, 90, 60) is True
    assert apply_sound_gate(True, 30, 60) is False


async def test_fix10_two_bus_independence_real_emulators(mysql_scenario):
    from _contract.schema import ServiceBusEntry, Settings
    from _db.servicebus import ServiceBusDriver

    facts = mysql_scenario("FIX10")
    settings = Settings(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
    )
    bus1 = ServiceBusEntry(id=1, name="sb-1", health_url="http://localhost:5300/health", amqp_port=5672, topic="sync")
    bus2 = ServiceBusEntry(id=2, name="sb-2", health_url="http://localhost:5301/health", amqp_port=5673, topic="sync-2")
    driver1 = ServiceBusDriver(bus1, settings)
    driver2 = ServiceBusDriver(bus2, settings)

    status1 = await driver1.check_health()
    status2 = await driver2.check_health()
    assert status1 == ReadStatus.OK
    assert status2 == ReadStatus.DOWN

    attribution_bus1 = resolve_bus_incident(
        bus_health_status=status1, bus_endpoint_status=ReadStatus.OK,
        bus_health_object_id="bus:1:health", bus_endpoint_object_id="bus:1:endpoint",
        target_db_status=OK, target_db_object_id="db:postgresql:1002:query",
        target_heartbeat_class="ok", target_read_status=OK, source_read_status=OK,
        queued_data_available=True, last_known=None,
    )
    attribution_bus2 = resolve_bus_incident(
        bus_health_status=status2, bus_endpoint_status=ReadStatus.OK,
        bus_health_object_id="bus:2:health", bus_endpoint_object_id="bus:2:endpoint",
        target_db_status=OK, target_db_object_id="db:mysql:1001:query",
        target_heartbeat_class="ok", target_read_status=OK, source_read_status=OK,
        queued_data_available=True, last_known=None,
    )
    assert attribution_bus1.cause == "normal"
    assert attribution_bus2 == PipelineAttribution(cause="bus_down", blocked_by="bus:2:health")


async def test_fix11_bus_down_stays_active_rule_even_once_receiver_also_down(mssql_scenario):
    from _contract.schema import ServiceBusEntry, Settings
    from _db.servicebus import ServiceBusDriver
    from _fixtures import scenarios as scenarios_module

    facts = mssql_scenario("FIX11")
    settings = Settings(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
    )
    bus2 = ServiceBusEntry(id=2, name="sb-2", health_url="http://localhost:5301/health", amqp_port=5673, topic="sync-2")
    driver2 = ServiceBusDriver(bus2, settings)

    status2 = await driver2.check_health()
    assert status2 == ReadStatus.DOWN
    attribution_step1 = resolve_bus_incident(
        bus_health_status=status2, bus_endpoint_status=ReadStatus.OK,
        bus_health_object_id="bus:2:health", bus_endpoint_object_id="bus:2:endpoint",
        target_db_status=OK, target_db_object_id="db:mysql:1001:query",
        target_heartbeat_class="ok", target_read_status=OK, source_read_status=OK,
        queued_data_available=True, last_known=None,
    )
    assert attribution_step1 == PipelineAttribution(cause="bus_down", blocked_by="bus:2:health")

    scenarios_module.step2_stop_receiver(facts, min_wait_seconds=1.0)
    attribution_step2 = resolve_bus_incident(
        bus_health_status=status2, bus_endpoint_status=ReadStatus.OK,
        bus_health_object_id="bus:2:health", bus_endpoint_object_id="bus:2:endpoint",
        target_db_status=ReadStatus.DOWN, target_db_object_id="db:mysql:1001:query",
        target_heartbeat_class="ok", target_read_status=OK, source_read_status=OK,
        queued_data_available=True, last_known=attribution_step1,
    )
    assert attribution_step2.cause == "bus_down"
    assert attribution_step2.blocked_by == "bus:2:health"


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


def test_lost_alert_tracker_from_settings_reads_c17_not_hardcoded():
    settings = _settings(bus_incident_stale_after_ms=999)
    tracker = LostAlertTracker.from_settings(settings, now_ms=lambda: 0.0)
    assert tracker.bus_incident_stale_after_ms == 999


def test_lost_alert_tracker_from_settings_value_actually_changes_resolve_timing_c17():
    now = [0.0]
    settings = _settings(bus_incident_stale_after_ms=50)
    tracker = LostAlertTracker.from_settings(settings, now_ms=lambda: now[0])

    tracker.observe("p1", lost_count=1, trigger_severity=1)
    now[0] = 10.0
    tick = tracker.observe("p1", lost_count=0, trigger_severity=1)
    assert tick.active is True
    now[0] = 65.0
    tick = tracker.observe("p1", lost_count=0, trigger_severity=1)
    assert tick.newly_resolved is True
