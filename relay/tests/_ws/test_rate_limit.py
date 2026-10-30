from _ws.rate_limit import AckRateLimiter


def test_allows_up_to_the_configured_attempt_count():
    now = [0.0]
    limiter = AckRateLimiter(attempts=3, window_seconds=60, lockout_seconds=60, now=lambda: now[0])
    assert limiter.allow("c1") is True
    assert limiter.allow("c1") is True
    assert limiter.allow("c1") is True


def test_denies_once_over_the_attempt_count_within_the_window():
    now = [0.0]
    limiter = AckRateLimiter(attempts=2, window_seconds=60, lockout_seconds=60, now=lambda: now[0])
    limiter.allow("c1")
    limiter.allow("c1")
    assert limiter.allow("c1") is False


def test_lockout_persists_until_lockout_seconds_elapse():
    now = [0.0]
    limiter = AckRateLimiter(attempts=1, window_seconds=60, lockout_seconds=30, now=lambda: now[0])
    limiter.allow("c1")
    assert limiter.allow("c1") is False
    now[0] = 10
    assert limiter.allow("c1") is False
    now[0] = 31
    assert limiter.allow("c1") is True


def test_clients_are_tracked_independently():
    now = [0.0]
    limiter = AckRateLimiter(attempts=1, window_seconds=60, lockout_seconds=60, now=lambda: now[0])
    limiter.allow("c1")
    assert limiter.allow("c1") is False
    assert limiter.allow("c2") is True


def test_from_settings_reads_c21_fields():
    class _Settings:
        pin_rate_limit_attempts = 5
        pin_rate_limit_window_seconds = 60
        pin_rate_limit_lockout_seconds = 60

    limiter = AckRateLimiter.from_settings(_Settings())
    assert limiter._attempts == 5
    assert limiter._window_seconds == 60
    assert limiter._lockout_seconds == 60
