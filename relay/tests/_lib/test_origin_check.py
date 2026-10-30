from _lib.origin_check import is_origin_allowed


def test_no_origin_header_always_allowed():
    assert is_origin_allowed(None, []) is True
    assert is_origin_allowed("", []) is True


def test_lan_origins_auto_allowed():
    assert is_origin_allowed("http://192.168.1.50:8085", []) is True
    assert is_origin_allowed("http://10.0.0.5:8085", []) is True
    assert is_origin_allowed("http://172.20.1.1:8085", []) is True
    assert is_origin_allowed("http://127.0.0.1:8085", []) is True
    assert is_origin_allowed("http://localhost:8085", []) is True
    assert is_origin_allowed("http://[::1]:8085", []) is True


def test_public_origin_rejected_without_explicit_allow_list():
    assert is_origin_allowed("https://evil.example.com", []) is False


def test_public_origin_allowed_when_explicitly_configured():
    assert is_origin_allowed("https://dashboard.example.com", ["https://dashboard.example.com"]) is True


def test_explicit_allow_list_does_not_match_by_substring():
    assert is_origin_allowed("https://evil-dashboard.example.com", ["https://dashboard.example.com"]) is False


def test_malformed_origin_is_refused():
    assert is_origin_allowed("not a url", []) is False
