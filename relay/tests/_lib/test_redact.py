from _lib.redact import MIN_SECRET_LENGTH, redact


def test_none_passthrough():
    assert redact(None, ["abcd"]) is None


def test_a_secret_at_the_minimum_length_is_redacted():
    assert MIN_SECRET_LENGTH == 4
    assert redact("password is abcd here", ["abcd"]) == "password is *** here"


def test_p120_a_secret_shorter_than_min_length_is_never_redacted_the_documented_gap():
    assert redact("pin is 123 here", ["123"]) == "pin is 123 here"


def test_multiple_secrets_all_redacted():
    text = "user=root password=fake-db-pw-1"
    assert redact(text, ["root", "fake-db-pw-1"]) == "user=*** password=***"


def test_empty_secret_list_returns_text_unchanged():
    assert redact("nothing to redact", []) == "nothing to redact"


def test_falsy_secret_in_list_is_skipped_safely():
    assert redact("abcd", ["", None]) == "abcd"
