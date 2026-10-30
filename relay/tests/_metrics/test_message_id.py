from __future__ import annotations

from _metrics.message_id import parse_message_id


def test_parses_the_documented_format_p16():
    parsed = parse_message_id("1001:ORDER:FIX01-1:1:p101:abcd-1234-uuid")
    assert parsed is not None
    assert parsed.origin == "1001"
    assert parsed.entity == "ORDER"
    assert parsed.key == "FIX01-1"
    assert parsed.row_version == 1
    assert parsed.pipeline_id == 101
    assert parsed.uuid == "abcd-1234-uuid"


def test_matches_the_real_fixture_toolkit_generator():
    from _fixtures import rows

    mid = rows.message_id("1002", "CUSTOMER", "FIX09-OLD", 1, 201)
    parsed = parse_message_id(mid)
    assert parsed is not None
    assert parsed.origin == "1002"
    assert parsed.entity == "CUSTOMER"
    assert parsed.key == "FIX09-OLD"
    assert parsed.row_version == 1
    assert parsed.pipeline_id == 201


def test_malformed_id_returns_none_never_raises():
    assert parse_message_id("not-a-message-id") is None
    assert parse_message_id("a:b:c:d:e:f:g") is None
    assert parse_message_id("1001:ORDER:K:1:101:uuid") is None
    assert parse_message_id("1001:ORDER:K:x:p101:uuid") is None
