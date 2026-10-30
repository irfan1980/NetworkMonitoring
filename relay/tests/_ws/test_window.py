import pytest

from _ws.window import InvalidWindowParam, validate_window_param


def test_accepts_a_positive_number():
    assert validate_window_param("300") == 300.0
    assert validate_window_param("0.5") == 0.5


@pytest.mark.parametrize("raw", [None, "", "not-a-number", "0", "-5", "inf", "-inf", "nan", "NaN"])
def test_rejects_everything_not_positive_and_finite(raw):
    with pytest.raises(InvalidWindowParam):
        validate_window_param(raw)
