from datetime import datetime, timedelta, timezone

import pytest

from _ops.silence import PinRateLimited, PinRejected, SilenceStore
from _ws.rate_limit import AckRateLimiter


def _store(now=None):
    limiter = AckRateLimiter(attempts=3, window_seconds=60, lockout_seconds=60, now=lambda: (now or [0.0])[0])
    return SilenceStore(limiter, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_verify_pin_accepts_correct_pin():
    store = _store()
    store.verify_pin("client-1", "1234", "1234")


def test_verify_pin_rejects_wrong_pin():
    store = _store()
    with pytest.raises(PinRejected):
        store.verify_pin("client-1", "0000", "1234")


def test_verify_pin_rejects_missing_pin():
    store = _store()
    with pytest.raises(PinRejected):
        store.verify_pin("client-1", None, "1234")


def test_verify_pin_never_logs_the_real_pin(caplog):
    import logging

    store = _store()
    with caplog.at_level(logging.WARNING, logger="relay.ops.silence"):
        with pytest.raises(PinRejected):
            store.verify_pin("client-1", "SECRET-GUESS", "1234")
    assert "SECRET-GUESS" not in caplog.text
    assert "1234" not in caplog.text


def test_verify_pin_rate_limits_after_c21_attempts():
    clock = [0.0]
    limiter = AckRateLimiter(attempts=3, window_seconds=60, lockout_seconds=60, now=lambda: clock[0])
    store = SilenceStore(limiter)
    for _ in range(3):
        with pytest.raises(PinRejected):
            store.verify_pin("client-1", "wrong", "1234")
    with pytest.raises(PinRateLimited):
        store.verify_pin("client-1", "1234", "1234")


def test_correct_pins_never_count_toward_the_limit():
    store = _store()
    for _ in range(20):
        store.verify_pin("client-1", "1234", "1234")


def test_wrong_pins_between_correct_ones_still_lock_out():
    store = _store()
    for _ in range(2):
        store.verify_pin("client-1", "1234", "1234")
        with pytest.raises(PinRejected):
            store.verify_pin("client-1", "wrong", "1234")
    store.verify_pin("client-1", "1234", "1234")
    with pytest.raises(PinRejected):
        store.verify_pin("client-1", "wrong", "1234")
    with pytest.raises(PinRateLimited):
        store.verify_pin("client-1", "1234", "1234")


def test_five_wrong_pins_lock_out_with_default_limits():
    limiter = AckRateLimiter(attempts=5, window_seconds=60, lockout_seconds=60, now=lambda: 0.0)
    store = SilenceStore(limiter)
    for _ in range(5):
        with pytest.raises(PinRejected):
            store.verify_pin("c", "bad", "1234")
    with pytest.raises(PinRateLimited):
        store.verify_pin("c", "bad", "1234")


def test_lockout_ends_after_the_lockout_period():
    clock = [0.0]
    store = SilenceStore(AckRateLimiter(attempts=2, window_seconds=60, lockout_seconds=30, now=lambda: clock[0]))
    for _ in range(2):
        with pytest.raises(PinRejected):
            store.verify_pin("c", "bad", "1234")
    with pytest.raises(PinRateLimited):
        store.verify_pin("c", "1234", "1234")
    clock[0] = 31
    store.verify_pin("c", "1234", "1234")


def test_verify_pin_rate_limit_is_per_client_id():
    clock = [0.0]
    limiter = AckRateLimiter(attempts=1, window_seconds=60, lockout_seconds=60, now=lambda: clock[0])
    store = SilenceStore(limiter)
    with pytest.raises(PinRejected):
        store.verify_pin("client-1", "wrong", "1234")
    with pytest.raises(PinRateLimited):
        store.verify_pin("client-1", "1234", "1234")
    store.verify_pin("client-2", "1234", "1234")


def test_create_assigns_incrementing_ids_and_expiry():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store = _store()
    store._now = lambda: now  # noqa: SLF001
    s1 = store.create("maintenance A", minutes=15, created_by="client-1")
    s2 = store.create("maintenance B", minutes=5, created_by="client-1")
    assert s1.id == 1
    assert s2.id == 2
    assert s1.expires_at == "2026-01-01T12:15:00Z"
    assert s2.expires_at == "2026-01-01T12:05:00Z"
    assert s1.created_by == "client-1"


def test_list_active_excludes_expired_silences():
    clock_now = [datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)]
    store = _store()
    store._now = lambda: clock_now[0]  # noqa: SLF001

    store.create("expires soon", minutes=1, created_by="c1")
    store.create("expires later", minutes=60, created_by="c1")

    clock_now[0] = clock_now[0] + timedelta(minutes=5)
    active = store.list_active()
    assert [s.reason for s in active] == ["expires later"]


def test_delete_removes_a_real_silence_and_reports_found():
    store = _store()
    s = store.create("maintenance", minutes=15, created_by="c1")
    assert store.delete(s.id, deleted_by="c1") is True
    assert store.list_active() == []


def test_delete_unknown_id_reports_not_found():
    store = _store()
    assert store.delete(99999, deleted_by="c1") is False
