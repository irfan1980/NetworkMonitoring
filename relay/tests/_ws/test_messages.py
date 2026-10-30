from _ws.messages import (
    POST_HELLO_ALLOWED_TYPES,
    validate_inbound,
    validate_inbound_hello,
    validate_outbound,
)


def test_p43_hello_is_the_message_schema_a_client_sends_first():
    hello = validate_inbound_hello({"type": "hello", "device": "wall-1", "clientId": "abc"})
    assert hello is not None
    assert hello.clientId == "abc"


def test_p105_a_malformed_hello_is_silently_dropped_not_raised():
    assert validate_inbound_hello({"type": "hello", "device": "wall-1"}) is None
    assert validate_inbound_hello({"type": "not-hello"}) is None


def test_p106_post_hello_whitelist_is_exactly_resync_ack_silence():
    assert POST_HELLO_ALLOWED_TYPES == frozenset({"resync", "ack", "silence"})


def test_p106_resync_ack_silence_validate_inbound_after_hello():
    assert validate_inbound({"type": "resync"}) is not None
    assert validate_inbound({"type": "ack", "alertId": "a1", "clientId": "c1"}) is not None
    assert validate_inbound({"type": "silence", "clientId": "c1"}) is not None


def test_p106_a_non_whitelisted_type_is_ignored():
    assert validate_inbound({"type": "hello", "device": "x", "clientId": "y"}) is None
    assert validate_inbound({"type": "something-else"}) is None


def test_p107_ack_missing_required_field_fails_its_own_schema():
    assert validate_inbound({"type": "ack", "clientId": "c1"}) is None


def test_p44_p46_snapshot_delta_alert_ackresult_all_validate_outbound():
    assert validate_outbound({"type": "snapshot", "seq": 1, "ts": "2026-01-01T00:00:00Z"})
    assert validate_outbound({"type": "delta", "seq": 2, "ts": "2026-01-01T00:00:01Z"})
    assert validate_outbound({"type": "alert", "alertId": "a1", "rootCause": "db:1", "severity": 2})
    assert validate_outbound({"type": "ackResult", "alertId": "a1", "ok": True})


def test_p49_p107_outbound_missing_required_field_is_rejected():
    assert not validate_outbound({"type": "snapshot", "seq": 1})
    assert not validate_outbound({"type": "ackResult", "alertId": "a1"})


def test_p49_p107_an_unrecognised_outbound_type_is_rejected_not_sent_anyway():
    assert not validate_outbound({"type": "something-new", "seq": 1})
