from __future__ import annotations

from _alerts.degraded import DegradedResult, evaluate_degraded, is_draining

BASE = dict(
    queued_count=0,
    backlog_max=100,
    red_on_the_map=False,
    lost_count=0,
    matched_recently=False,
    queued_countable=False,
    send_oldest_pending_seconds=None,
    send_avg_lag_seconds=None,
    apply_oldest_pending_seconds=None,
    apply_avg_lag_seconds=None,
    lag_max=60,
    unblocked_for_seconds=None,
)


def test_healthy_pipeline_is_not_degraded():
    result = evaluate_degraded(**BASE)
    assert result.degraded is False
    assert result.reasons == ()


def test_condition_1_in_bus_pile_backlog():
    result = evaluate_degraded(**{**BASE, "queued_count": 101, "backlog_max": 100})
    assert result.degraded is True
    assert "backlog" in result.reasons


def test_condition_2_red_on_the_map_is_a_separate_condition():
    result = evaluate_degraded(**{**BASE, "red_on_the_map": True})
    assert result.degraded is True
    assert result.reasons == ("red_on_the_map",)


def test_condition_3_non_draining_lost_count():
    result = evaluate_degraded(**{**BASE, "lost_count": 5})
    assert result.degraded is True
    assert "lost" in result.reasons


def test_condition_3_lost_suppressed_while_draining():
    result = evaluate_degraded(**{
        **BASE, "lost_count": 5, "matched_recently": True, "queued_countable": True,
    })
    assert result.draining is True
    assert "lost" not in result.reasons


def test_draining_flag_suppresses_backlog_and_lag_but_not_red_on_the_map():
    result = evaluate_degraded(**{
        **BASE,
        "queued_count": 200, "backlog_max": 100,
        "red_on_the_map": True,
        "send_oldest_pending_seconds": 500, "lag_max": 60,
        "matched_recently": True, "queued_countable": True,
    })
    assert result.draining is True
    assert "backlog" not in result.reasons
    assert "send_lag" not in result.reasons
    assert "red_on_the_map" in result.reasons
    assert result.degraded is True


def test_conditions_5_6_actual_oldest_row_age_preferred_over_average():
    result = evaluate_degraded(**{
        **BASE,
        "send_oldest_pending_seconds": 120,
        "send_avg_lag_seconds": 5,
        "lag_max": 60,
    })
    assert result.send_wait_seconds == 120
    assert "send_lag" in result.reasons


def test_average_is_only_a_fallback_when_no_oldest_row_reading_exists():
    result = evaluate_degraded(**{
        **BASE,
        "send_oldest_pending_seconds": None,
        "send_avg_lag_seconds": 90,
        "lag_max": 60,
    })
    assert result.send_wait_seconds == 90
    assert "send_lag" in result.reasons


def test_wait_capped_by_unblocked_for_seconds():
    result = evaluate_degraded(**{
        **BASE,
        "apply_oldest_pending_seconds": 500,
        "lag_max": 60,
        "unblocked_for_seconds": 10,
    })
    assert result.apply_wait_seconds == 10
    assert "apply_lag" not in result.reasons


def test_apply_lag_condition_independent_of_send_lag():
    result = evaluate_degraded(**{
        **BASE,
        "apply_oldest_pending_seconds": 200,
        "lag_max": 60,
    })
    assert "apply_lag" in result.reasons
    assert "send_lag" not in result.reasons


def test_is_draining_requires_both_matched_recently_and_queued_countable():
    assert is_draining(matched_recently=True, queued_countable=True) is True
    assert is_draining(matched_recently=True, queued_countable=False) is False
    assert is_draining(matched_recently=False, queued_countable=True) is False
    assert is_draining(matched_recently=False, queued_countable=False) is False


def test_multiple_reasons_can_fire_at_once():
    result = evaluate_degraded(**{
        **BASE,
        "queued_count": 200, "backlog_max": 100,
        "lost_count": 3,
        "red_on_the_map": True,
    })
    assert result.degraded is True
    assert set(result.reasons) == {"backlog", "lost", "red_on_the_map"}


def test_degraded_result_is_immutable_dataclass():
    result = evaluate_degraded(**BASE)
    assert isinstance(result, DegradedResult)
