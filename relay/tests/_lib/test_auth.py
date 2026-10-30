from unittest.mock import patch

from _lib.auth import REJECTION_REASON, bearer_token, timing_safe_equal_str


def test_p121_rejection_reason_is_one_fixed_literal():
    assert REJECTION_REASON == "unauthorized"


def test_bearer_token_prefers_the_header_and_falls_back_to_the_query():
    assert bearer_token({"authorization": "Bearer abc"}, {}) == ("abc", "header")
    assert bearer_token({"authorization": "bearer  abc "}, {"token": "q"}) == ("abc", "header")
    assert bearer_token({}, {"token": "q"}) == ("q", "query")
    assert bearer_token({"authorization": "Basic zzz"}, {}) == (None, "none")
    assert bearer_token({"authorization": "Bearer "}, {}) == (None, "none")
    assert bearer_token({"x-client-id": "a", "x-client-secret": "b"}, {"clientId": "a", "clientSecret": "b"}) == (None, "none")


def test_p122_equal_length_strings_compare_correctly():
    assert timing_safe_equal_str("secret", "secret") is True
    assert timing_safe_equal_str("secret", "wrongo") is False


def test_p122_unequal_length_still_returns_false_correctly():
    assert timing_safe_equal_str("short", "much-longer-secret") is False
    assert timing_safe_equal_str("much-longer-secret", "short") is False


def test_p122_a_length_mismatch_still_pays_for_a_real_compare_digest_call():
    with patch("_lib.auth.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as spy:
        timing_safe_equal_str("short", "a-much-longer-secret")
        assert spy.call_count == 1
        call_args = spy.call_args[0]
        assert call_args[0] == call_args[1] == b"short"
