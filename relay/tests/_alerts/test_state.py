from __future__ import annotations

from _alerts.state import AlertStateStore, BusIncidentCooldown, bus_incident_cooldown_elapsed


def _clock(start: float = 1000.0):
    state = {"t": start}

    def now() -> float:
        return state["t"]

    def advance(seconds: float) -> None:
        state["t"] += seconds

    return now, advance


def test_first_raise_is_new_and_dispatches_immediately_p117(tmp_path):
    now, _ = _clock()
    store = AlertStateStore(tmp_path, now=now)
    record, is_new, should_dispatch = store.raise_or_repeat(
        "object:db:1", "object", 2, "unknown",
        repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3,
    )
    assert is_new is True
    assert should_dispatch is True
    assert record.repeat_count == 0
    assert record.active is True


def test_repeat_before_interval_does_not_dispatch(tmp_path):
    now, advance = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 1, "ok", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    advance(60)
    _, is_new, should_dispatch = store.raise_or_repeat(
        "a", "object", 1, "ok", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3,
    )
    assert is_new is False
    assert should_dispatch is False


def test_repeat_after_interval_dispatches_and_increments_sev1_2(tmp_path):
    now, advance = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 2, "ok", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    advance(15 * 60)
    record, is_new, should_dispatch = store.raise_or_repeat(
        "a", "object", 2, "ok", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3,
    )
    assert is_new is False
    assert should_dispatch is True
    assert record.repeat_count == 1


def test_severity_3_5_uses_the_hours_interval_not_minutes(tmp_path):
    now, advance = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "pipeline", 4, "ok", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    advance(15 * 60)
    _, _, should_dispatch = store.raise_or_repeat(
        "a", "pipeline", 4, "ok", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3,
    )
    assert should_dispatch is False
    advance(4 * 3600)
    _, _, should_dispatch = store.raise_or_repeat(
        "a", "pipeline", 4, "ok", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3,
    )
    assert should_dispatch is True


def test_escalates_after_configured_repeat_count(tmp_path):
    now, advance = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 1, "ok", repeat_minutes_sev1_2=1, repeat_hours_sev3_5=4, escalate_after_repeats=2)
    for _ in range(2):
        advance(60)
        record, _, _ = store.raise_or_repeat("a", "object", 1, "ok", repeat_minutes_sev1_2=1, repeat_hours_sev3_5=4, escalate_after_repeats=2)
    assert record.escalated is True
    assert record.repeat_count == 2


def test_resolve_marks_inactive_and_a_new_raise_after_resolve_is_new_again(tmp_path):
    now, _ = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 1, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    resolved = store.resolve("a")
    assert resolved.active is False
    assert store.is_active("a") is False
    record, is_new, should_dispatch = store.raise_or_repeat(
        "a", "object", 1, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3,
    )
    assert is_new is True
    assert record.repeat_count == 0


def test_resolving_an_unknown_or_already_resolved_alert_is_a_no_op(tmp_path):
    now, _ = _clock()
    store = AlertStateStore(tmp_path, now=now)
    assert store.resolve("never-raised") is None
    store.raise_or_repeat("a", "object", 1, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    store.resolve("a")
    assert store.resolve("a") is None


def test_p63_state_survives_a_restart(tmp_path):
    now, advance = _clock()
    store1 = AlertStateStore(tmp_path, now=now)
    store1.raise_or_repeat("a", "pipeline", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    advance(15 * 60)
    store1.raise_or_repeat("a", "pipeline", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)

    store2 = AlertStateStore(tmp_path, now=now)
    record = store2.get("a")
    assert record is not None
    assert record.repeat_count == 1
    assert record.active is True
    _, is_new, _ = store2.raise_or_repeat(
        "a", "pipeline", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3,
    )
    assert is_new is False


def test_p63_a_missing_state_file_starts_empty_never_crashes(tmp_path):
    store = AlertStateStore(tmp_path / "does" / "not" / "exist" / "yet")
    assert store.active_alert_ids() == set()


def test_p63_a_corrupt_state_file_is_ignored_never_crashes(tmp_path):
    state_dir = tmp_path
    (state_dir / "alert_state.json").write_text("{not valid json", encoding="utf-8")
    store = AlertStateStore(state_dir)
    assert store.active_alert_ids() == set()


def test_p62_acknowledge_sets_the_flag_and_persists(tmp_path):
    now, _ = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)

    assert store.is_acknowledged("a") is False
    record = store.acknowledge("a", acknowledged_by="operator-1")
    assert record.acknowledged is True
    assert record.acknowledged_by == "operator-1"
    assert record.acknowledged_at == now()
    assert store.is_acknowledged("a") is True


def test_p62_acknowledge_never_changes_severity_repeat_count_or_escalated(tmp_path):
    now, advance = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    advance(15 * 60)
    store.raise_or_repeat("a", "object", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    before = store.get("a")
    before_severity, before_repeat_count, before_escalated = before.severity, before.repeat_count, before.escalated

    store.acknowledge("a", acknowledged_by="operator-1")

    after = store.get("a")
    assert after.severity == before_severity
    assert after.repeat_count == before_repeat_count
    assert after.escalated == before_escalated
    assert after.active is True


def test_p62_acknowledge_does_not_change_a_future_repeat_dispatch_decision(tmp_path):
    now, advance = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    store.acknowledge("a", acknowledged_by="operator-1")

    advance(15 * 60)
    _, is_new, should_dispatch = store.raise_or_repeat(
        "a", "object", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3,
    )
    assert should_dispatch is True


def test_p62_acknowledging_an_unknown_alert_id_returns_none_never_crashes(tmp_path):
    store = AlertStateStore(tmp_path)
    assert store.acknowledge("does-not-exist", acknowledged_by="operator-1") is None


def test_p62_acknowledging_an_already_resolved_alert_returns_none(tmp_path):
    now, _ = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    store.resolve("a")
    assert store.acknowledge("a", acknowledged_by="operator-1") is None


def test_p62_acknowledged_state_survives_a_restart(tmp_path):
    now, _ = _clock()
    store = AlertStateStore(tmp_path, now=now)
    store.raise_or_repeat("a", "object", 2, "unknown", repeat_minutes_sev1_2=15, repeat_hours_sev3_5=4, escalate_after_repeats=3)
    store.acknowledge("a", acknowledged_by="operator-1")

    restarted = AlertStateStore(tmp_path, now=now)
    assert restarted.is_acknowledged("a") is True
    assert restarted.get("a").acknowledged_by == "operator-1"


def test_p112_no_cooldown_record_means_no_cooldown_in_effect():
    assert bus_incident_cooldown_elapsed(None, "bus:1", now=1000, cooldown_minutes=5) is True


def test_p112_cooldown_for_a_different_alert_id_never_blocks_a_new_incident():
    cooldown = BusIncidentCooldown(alert_id="bus:1", since=1000)
    assert bus_incident_cooldown_elapsed(cooldown, "bus:2", now=1001, cooldown_minutes=5) is True


def test_p112_same_alert_id_still_within_cooldown_window():
    cooldown = BusIncidentCooldown(alert_id="bus:1", since=1000)
    assert bus_incident_cooldown_elapsed(cooldown, "bus:1", now=1000 + 60, cooldown_minutes=5) is False


def test_p112_same_alert_id_after_cooldown_window_elapsed():
    cooldown = BusIncidentCooldown(alert_id="bus:1", since=1000)
    assert bus_incident_cooldown_elapsed(cooldown, "bus:1", now=1000 + 5 * 60, cooldown_minutes=5) is True


def test_from_settings_reads_c37_fields_not_hardcoded_defaults(tmp_path):
    from _contract.schema import Settings

    settings = Settings.model_validate(
        {
            "auth": {
                "web_client_id": "w", "web_client_secret": "w",
                "app_client_id": "a", "app_client_secret": "a",
            },
            "ack_pin": "1234",
            "atomic_write_retry_attempts": 7,
            "atomic_write_retry_backoff_ms_per_attempt": 3,
        }
    )
    store = AlertStateStore.from_settings(settings, tmp_path)
    assert store._retry_attempts == 7
    assert store._backoff_ms_per_attempt == 3


def test_from_settings_c37_value_actually_reaches_the_persist_retry_loop(tmp_path, monkeypatch):
    import errno

    from _contract.schema import Settings

    settings = Settings.model_validate(
        {
            "auth": {
                "web_client_id": "w", "web_client_secret": "w",
                "app_client_id": "a", "app_client_secret": "a",
            },
            "ack_pin": "1234",
            "atomic_write_retry_attempts": 2,
            "atomic_write_retry_backoff_ms_per_attempt": 1,
        }
    )
    store = AlertStateStore.from_settings(settings, tmp_path)

    calls = {"n": 0}

    def always_flaky(src, dst):
        calls["n"] += 1
        raise OSError(errno.EBUSY, "always busy")

    monkeypatch.setattr("os.replace", always_flaky)
    try:
        store._persist()
    except OSError:
        pass
    assert calls["n"] == 2
