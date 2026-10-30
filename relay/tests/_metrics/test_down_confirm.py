from _metrics.down_confirm import DownConfirm
from _metrics.readability import ReadStatus

OK = ReadStatus.OK
DOWN = ReadStatus.DOWN
UNREAD = ReadStatus.UNREAD


def make_clock(start_ms: float = 0.0):
    state = {"t": start_ms}

    def now():
        return state["t"]

    def advance(ms: float):
        state["t"] += ms

    return now, advance


def test_first_ever_observation_down_confirms_immediately():
    dc = DownConfirm()
    assert dc.observe("obj", DOWN) == DOWN


def test_first_ever_observation_ok_is_trivially_confirmed_ok():
    dc = DownConfirm()
    assert dc.observe("obj", OK) == OK


def test_a_later_down_after_being_ok_requires_two_polls_and_the_soft_floor():
    now, advance = make_clock()
    dc = DownConfirm(down_confirm_soft_min_ms=100, now_ms=now)
    dc.observe("obj", OK)
    assert dc.observe("obj", DOWN) == OK
    advance(200)
    assert dc.observe("obj", DOWN) == DOWN


def test_two_down_observations_too_close_together_do_not_confirm_yet():
    now, advance = make_clock()
    dc = DownConfirm(down_confirm_soft_min_ms=1000, now_ms=now)
    dc.observe("obj", OK)
    dc.observe("obj", DOWN)
    advance(10)
    assert dc.observe("obj", DOWN) == OK


def test_recovery_also_requires_two_polls():
    now, advance = make_clock()
    dc = DownConfirm(down_confirm_soft_min_ms=0, now_ms=now)
    dc.observe("obj", DOWN)
    assert dc.observe("obj", OK) == DOWN
    assert dc.observe("obj", OK) == OK


def test_a_reverting_observation_resets_the_pending_streak():
    now, advance = make_clock()
    dc = DownConfirm(down_confirm_soft_min_ms=0, now_ms=now)
    dc.observe("obj", OK)
    dc.observe("obj", DOWN)
    dc.observe("obj", OK)
    assert dc.observe("obj", DOWN) == OK


def test_unread_never_confirms_down_no_matter_how_long():
    now, advance = make_clock()
    dc = DownConfirm(now_ms=now)
    dc.observe("obj", OK)
    for _ in range(50):
        advance(10_000)
        assert dc.observe("obj", UNREAD) == OK


def test_unread_does_not_itself_confirm_a_recovery_either():
    dc = DownConfirm(down_confirm_soft_min_ms=0)
    dc.observe("obj", DOWN)
    assert dc.observe("obj", UNREAD) == DOWN
    assert dc.observe("obj", UNREAD) == DOWN


def test_unread_does_not_reset_an_in_progress_pending_streak():
    now, advance = make_clock()
    dc = DownConfirm(down_confirm_soft_min_ms=0, now_ms=now)
    dc.observe("obj", OK)
    dc.observe("obj", DOWN)
    dc.observe("obj", UNREAD)
    assert dc.observe("obj", DOWN) == DOWN


def test_unread_as_the_very_first_ever_observation_leaves_default_ok():
    dc = DownConfirm()
    assert dc.observe("obj", UNREAD) == OK
    assert dc.confirmed_status("obj") == OK


def test_down_after_only_unread_observations_confirms_immediately_carve_out_1():
    dc = DownConfirm(down_confirm_soft_min_ms=999_999)
    for _ in range(5):
        assert dc.observe("obj", UNREAD) == OK
    assert dc.observe("obj", DOWN) == DOWN


def test_down_after_a_real_ok_baseline_still_needs_the_uniform_2_poll_rule():
    now, advance = make_clock()
    dc = DownConfirm(down_confirm_soft_min_ms=100, now_ms=now)
    dc.observe("obj", UNREAD)
    dc.observe("obj", OK)
    assert dc.observe("obj", DOWN) == OK
    advance(200)
    assert dc.observe("obj", DOWN) == DOWN


def test_observe_rejects_blocked_it_is_never_a_raw_observation():
    import pytest

    dc = DownConfirm()
    with pytest.raises(ValueError):
        dc.observe("obj", ReadStatus.BLOCKED)


def test_unread_streak_ms_tracks_continuous_unread_duration():
    now, advance = make_clock()
    dc = DownConfirm(now_ms=now)
    dc.observe("obj", OK)
    assert dc.unread_streak_ms("obj") == 0
    dc.observe("obj", UNREAD)
    advance(5000)
    assert dc.unread_streak_ms("obj") == 5000
    assert dc.is_stable_unread("obj") is False


def test_is_stable_unread_true_once_the_debounce_floor_is_reached():
    now, advance = make_clock()
    dc = DownConfirm(unread_min_ms=15000, now_ms=now)
    dc.observe("obj", OK)
    dc.observe("obj", UNREAD)
    advance(15000)
    assert dc.is_stable_unread("obj") is True


def test_unread_streak_resets_once_a_real_observation_arrives():
    now, advance = make_clock()
    dc = DownConfirm(now_ms=now)
    dc.observe("obj", OK)
    dc.observe("obj", UNREAD)
    advance(20000)
    dc.observe("obj", OK)
    assert dc.unread_streak_ms("obj") == 0


def test_independent_objects_confirm_independently_p96():
    dc = DownConfirm()
    dc.observe("bus:1:emulator:health", DOWN)
    assert dc.confirmed_status("bus:1:emulator:health") == DOWN
    assert dc.confirmed_status("bus:2:emulator:health") == OK
    dc.observe("bus:2:emulator:health", OK)
    assert dc.confirmed_status("bus:2:emulator:health") == OK


def test_two_buses_hold_independent_in_progress_streaks_simultaneously_p96():
    dc = DownConfirm(down_confirm_soft_min_ms=0)
    dc.observe("bus:1:emulator:health", OK)
    dc.observe("bus:2:emulator:health", OK)

    dc.observe("bus:1:emulator:health", DOWN)
    dc.observe("bus:2:emulator:health", DOWN)
    assert dc.confirmed_status("bus:1:emulator:health") == OK
    assert dc.confirmed_status("bus:2:emulator:health") == OK

    dc.observe("bus:2:emulator:health", OK)
    assert dc.confirmed_status("bus:2:emulator:health") == OK

    dc.observe("bus:1:emulator:health", DOWN)
    assert dc.confirmed_status("bus:1:emulator:health") == DOWN
    assert dc.confirmed_status("bus:2:emulator:health") == OK

    dc.observe("bus:2:emulator:health", DOWN)
    assert dc.confirmed_status("bus:2:emulator:health") == OK
    dc.observe("bus:2:emulator:health", DOWN)
    assert dc.confirmed_status("bus:2:emulator:health") == DOWN


def test_from_settings_reads_c22_fields_not_hardcoded_defaults():
    from _contract.schema import Settings

    settings = Settings.model_validate(
        {
            "auth": {
                "web_client_id": "a", "web_client_secret": "b",
                "app_client_id": "c", "app_client_secret": "d",
            },
            "ack_pin": "1234",
            "down_confirm_soft_min_ms": 4242,
            "unread_min_ms": 7777,
        }
    )
    dc = DownConfirm.from_settings(settings)
    assert dc.down_confirm_soft_min_ms == 4242
    assert dc.unread_min_ms == 7777
