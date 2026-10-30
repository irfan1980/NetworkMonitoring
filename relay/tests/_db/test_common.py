import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from _db.common import normalize_numeric, normalize_row, to_iso_utc


def test_none_stays_none():
    assert to_iso_utc(None) is None


def test_naive_datetime_treated_as_utc():
    dt = datetime(2026, 9, 24, 12, 0, 0)
    assert to_iso_utc(dt) == "2026-09-24T12:00:00Z"


def test_aware_non_utc_datetime_converted():
    dt = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    assert to_iso_utc(dt) == "2026-09-24T07:00:00Z"


def test_mssql_for_json_style_string_no_offset_treated_as_utc():
    assert to_iso_utc("2026-09-24T12:00:00.1234567") == "2026-09-24T12:00:00.1234567Z"


def test_mysql_datestrings_style_bare_plus_00_normalises_to_z():
    assert to_iso_utc("2026-09-24T12:00:00+00") == "2026-09-24T12:00:00Z"


def test_bare_minus_00_normalises_to_z():
    assert to_iso_utc("2026-09-24T12:00:00-00") == "2026-09-24T12:00:00Z"


def test_bare_plus_0000_with_colon_normalises_to_z():
    assert to_iso_utc("2026-09-24T12:00:00+00:00") == "2026-09-24T12:00:00Z"


def test_already_z_left_alone():
    assert to_iso_utc("2026-09-24T12:00:00Z") == "2026-09-24T12:00:00Z"


def test_real_nonzero_offset_left_alone():
    assert to_iso_utc("2026-09-24T12:00:00+05:00") == "2026-09-24T12:00:00+05:00"


def test_space_separated_datetime_gets_a_t():
    assert to_iso_utc("2026-09-24 12:00:00") == "2026-09-24T12:00:00Z"


def test_unsupported_type_raises():
    with pytest.raises(TypeError):
        to_iso_utc(12345)


def test_c30_non_default_auth_probe_interval_ms_actually_changes_is_due_timing():
    from _db.common import MonotonicAuthProbeClock

    clock = MonotonicAuthProbeClock(interval_ms=50)
    clock.record(ok=True)
    clock.last_probe_monotonic -= 1.0
    assert clock.is_due() is True


def test_c30_default_sized_interval_is_not_due_immediately_after_recording():
    from _db.common import MonotonicAuthProbeClock

    clock = MonotonicAuthProbeClock(interval_ms=30000)
    clock.record(ok=True)
    assert clock.is_due() is False


def test_whole_valued_decimal_becomes_int_not_float():
    result = normalize_numeric(Decimal("43"))
    assert result == 43
    assert isinstance(result, int)


def test_fractional_decimal_becomes_float():
    result = normalize_numeric(Decimal("12.5"))
    assert result == 12.5
    assert isinstance(result, float)


def test_none_decimal_stays_none_never_coerced_to_zero():
    assert normalize_numeric(None) is None


def test_non_decimal_values_pass_through_unchanged():
    assert normalize_numeric(5) == 5
    assert isinstance(normalize_numeric(5), int)
    assert normalize_numeric(5.5) == 5.5
    assert normalize_numeric("x") == "x"
    assert normalize_numeric(True) is True


def test_normalize_row_converts_every_decimal_field_in_a_row():
    row = {
        "pendingOutbox": Decimal("43"),
        "failedOutbox": Decimal("0"),
        "sendLagSeconds": Decimal("12.5"),
        "applyLagSeconds": None,
        "heartbeatAgeSeconds": 7,
        "name": "pipeline-a",
    }
    normalized = normalize_row(row)

    assert normalized["pendingOutbox"] == 43 and isinstance(normalized["pendingOutbox"], int)
    assert normalized["failedOutbox"] == 0 and isinstance(normalized["failedOutbox"], int)
    assert normalized["sendLagSeconds"] == 12.5 and isinstance(normalized["sendLagSeconds"], float)
    assert normalized["applyLagSeconds"] is None
    assert normalized["heartbeatAgeSeconds"] == 7
    assert normalized["name"] == "pipeline-a"

    json.dumps(normalized)


def test_a_raw_decimal_row_raises_typeerror_proving_the_test_is_not_vacuous():
    row = {"pendingOutbox": Decimal("43")}
    with pytest.raises(TypeError):
        json.dumps(row)
