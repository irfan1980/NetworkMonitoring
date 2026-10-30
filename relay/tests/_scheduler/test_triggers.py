from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from _scheduler.model import Trigger
from _scheduler.triggers import compile_cron, is_reboot, next_fire, preview, summarize
from _scheduler.utc import iso


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def trig(**kw) -> Trigger:
    return Trigger.model_validate(kw)


def test_cron_five_field():
    t = trig(kind="cron", cron="*/15 9 * * 1-5")
    assert next_fire(t, utc(2026, 3, 2, 9, 0)) == utc(2026, 3, 2, 9, 15)
    assert next_fire(t, utc(2026, 3, 2, 9, 50)) == utc(2026, 3, 3, 9, 0)
    assert next_fire(t, utc(2026, 3, 6, 9, 50)) == utc(2026, 3, 9, 9, 0)


@pytest.mark.parametrize("preset,after,expected", [
    ("@hourly", utc(2026, 3, 1, 12, 30), utc(2026, 3, 1, 13, 0)),
    ("@daily", utc(2026, 3, 1, 12, 30), utc(2026, 3, 2, 0, 0)),
    ("@weekly", utc(2026, 3, 1, 12, 30), utc(2026, 3, 8, 0, 0)),
    ("@monthly", utc(2026, 3, 1, 12, 30), utc(2026, 4, 1, 0, 0)),
    ("@yearly", utc(2026, 3, 1, 12, 30), utc(2027, 1, 1, 0, 0)),
])
def test_cron_presets(preset, after, expected):
    assert next_fire(trig(kind="cron", cron=preset), after) == expected


def test_reboot_preset_is_not_time_based():
    t = trig(kind="cron", cron="@reboot")
    assert is_reboot(t)
    assert next_fire(t, utc(2026, 3, 1)) is None
    assert preview(t) == []


@pytest.mark.parametrize("bad", ["", "* * * *", "61 * * * *", "@sometimes", "a b c d e", "* * * * * *"])
def test_invalid_cron_is_rejected(bad):
    with pytest.raises(ValidationError):
        trig(kind="cron", cron=bad)


def test_daily_compiles_to_cron():
    t = trig(kind="daily", time="09:30")
    assert compile_cron(t) == "30 9 * * *"
    assert next_fire(t, utc(2026, 3, 1, 9, 30)) == utc(2026, 3, 2, 9, 30)
    assert next_fire(t, utc(2026, 3, 1, 9, 29)) == utc(2026, 3, 1, 9, 30)


def test_weekly_days_are_sorted_and_deduplicated():
    t = trig(kind="weekly", time="08:00", days_of_week=[5, 1, 1])
    assert t.days_of_week == [1, 5]
    assert compile_cron(t) == "0 8 * * 1,5"
    assert next_fire(t, utc(2026, 3, 1, 12)) == utc(2026, 3, 2, 8)
    assert next_fire(t, utc(2026, 3, 2, 12)) == utc(2026, 3, 6, 8)


def test_weekly_sunday_is_zero():
    t = trig(kind="weekly", time="08:00", days_of_week=[0])
    assert next_fire(t, utc(2026, 3, 2)) == utc(2026, 3, 8, 8)


def test_monthly_day_31_skips_short_months():
    t = trig(kind="monthly", time="06:00", day_of_month=31)
    assert next_fire(t, utc(2026, 1, 31, 7)) == utc(2026, 3, 31, 6)
    assert next_fire(t, utc(2026, 3, 31, 7)) == utc(2026, 5, 31, 6)


def test_monthly_last_day_handles_month_ends_and_leap_years():
    t = trig(kind="monthly", time="23:59", day_of_month=-1)
    assert next_fire(t, utc(2026, 1, 31, 23, 59)) == utc(2026, 2, 28, 23, 59)
    assert next_fire(t, utc(2028, 1, 31, 23, 59)) == utc(2028, 2, 29, 23, 59)
    assert next_fire(t, utc(2026, 4, 1)) == utc(2026, 4, 30, 23, 59)


def test_monthly_day_29_in_non_leap_february():
    t = trig(kind="monthly", time="00:00", day_of_month=29)
    assert next_fire(t, utc(2026, 1, 30)) == utc(2026, 3, 29)
    assert next_fire(t, utc(2028, 1, 30)) == utc(2028, 2, 29)


def test_monthly_rejects_day_32_and_zero():
    for day in (0, 32, -2):
        with pytest.raises(ValidationError):
            trig(kind="monthly", time="00:00", day_of_month=day)


def test_year_rollover():
    t = trig(kind="monthly", time="00:00", day_of_month=1)
    assert next_fire(t, utc(2026, 12, 15)) == utc(2027, 1, 1)


def test_once_fires_only_in_the_future():
    t = trig(kind="once", at="2026-03-01T12:00:00Z")
    assert next_fire(t, utc(2026, 3, 1, 11)) == utc(2026, 3, 1, 12)
    assert next_fire(t, utc(2026, 3, 1, 12)) is None
    assert next_fire(t, utc(2026, 3, 2)) is None


def test_interval_stays_on_its_anchor_grid():
    t = trig(kind="interval", every_seconds=90, start_at="2026-03-01T12:00:00Z")
    assert next_fire(t, utc(2026, 3, 1, 11)) == utc(2026, 3, 1, 12)
    assert next_fire(t, utc(2026, 3, 1, 12, 0, 0)) == utc(2026, 3, 1, 12, 1, 30)
    assert next_fire(t, utc(2026, 3, 1, 12, 1, 31)) == utc(2026, 3, 1, 12, 3)


def test_interval_defaults_its_anchor_to_now():
    t = trig(kind="interval", every_seconds=60)
    assert t.start_at is not None and t.start_at.tzinfo is not None


def test_events_have_no_clock_fire_time():
    for t in (trig(kind="event", event="machine_online"), trig(kind="event", event="relay_start"),
              trig(kind="event", event="after_schedule", after_schedule_id=2)):
        assert next_fire(t, utc(2026, 3, 1)) is None


def test_after_schedule_defaults_to_success_and_needs_an_id():
    assert trig(kind="event", event="after_schedule", after_schedule_id=2).on == "success"
    with pytest.raises(ValidationError):
        trig(kind="event", event="after_schedule")


def test_preview_returns_next_n_in_order():
    out = preview(trig(kind="daily", time="10:00"), 5, after=utc(2026, 3, 1, 12))
    assert out == [utc(2026, 3, d, 10) for d in range(2, 7)]


def test_times_are_converted_to_utc_and_serialised_with_z():
    t = trig(kind="once", at="2026-03-01T12:00:00+05:00")
    assert t.at == utc(2026, 3, 1, 7)
    assert t.model_dump(mode="json")["at"] == "2026-03-01T07:00:00Z"
    naive = trig(kind="once", at="2026-03-01T12:00:00")
    assert iso(naive.at) == "2026-03-01T12:00:00Z"


def test_unknown_fields_and_missing_pieces_are_rejected():
    with pytest.raises(ValidationError):
        trig(kind="daily", time="09:30", bogus=1)
    with pytest.raises(ValidationError):
        trig(kind="daily", time="25:00")
    with pytest.raises(ValidationError):
        trig(kind="weekly", time="09:00")
    with pytest.raises(ValidationError):
        trig(kind="once")


def test_summaries_are_readable():
    assert summarize(trig(kind="daily", time="09:30")) == "daily at 09:30 UTC"
    assert summarize(trig(kind="interval", every_seconds=300)) == "every 5 min"
    assert summarize(trig(kind="interval", every_seconds=7200)) == "every 2 hours"
    assert summarize(trig(kind="monthly", time="01:00", day_of_month=-1)) == "monthly on last day at 01:00 UTC"
    assert summarize(trig(kind="weekly", time="09:00", days_of_week=[1, 2])) == "weekly on Mon,Tue at 09:00 UTC"
    assert summarize(trig(kind="event", event="machine_online")) == "when the machine comes online"
