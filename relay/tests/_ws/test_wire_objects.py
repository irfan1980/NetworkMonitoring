import pytest

from _metrics.readability import ReadStatus
from _ws.messages import validate_outbound
from _ws.wire_objects import build_wire_object, build_wire_objects, to_wire_status


def test_p22_ok_down_blocked_are_the_only_emittable_wire_statuses():
    assert to_wire_status(ReadStatus.OK) == "ok"
    assert to_wire_status(ReadStatus.DOWN) == "down"
    assert to_wire_status(ReadStatus.BLOCKED) == "blocked"


def test_p22_unread_can_never_be_serialized_onto_the_wire():
    with pytest.raises(ValueError, match="is not an emitted wire status"):
        to_wire_status(ReadStatus.UNREAD)


def test_p22_build_wire_object_raises_for_unread_never_silently_emits_it():
    with pytest.raises(ValueError):
        build_wire_object("db:mysql:1001:query", "db", ReadStatus.UNREAD)


def test_p22_pydantic_is_the_second_independent_layer_a_raw_bad_status_is_rejected():
    bad_object_payload = {
        "type": "snapshot",
        "seq": 1,
        "ts": "2026-01-01T00:00:00Z",
        "objects": [{"id": "x", "kind": "db", "status": "unread"}],
    }
    from _ws.wire_objects import WireObject
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WireObject(**bad_object_payload["objects"][0])
    assert validate_outbound(bad_object_payload)


def test_build_wire_objects_bulk_form_matches_attribution_shapes():
    emitted = {"db:mysql:1001:query": ReadStatus.OK, "bus:1:endpoint": ReadStatus.BLOCKED}
    kinds = {"db:mysql:1001:query": "db", "bus:1:endpoint": "bus"}
    blocked_by = {"bus:1:endpoint": "bus:1:health"}
    objs = build_wire_objects(emitted, kinds, blocked_by)
    by_id = {o["id"]: o for o in objs}
    assert by_id["db:mysql:1001:query"]["status"] == "ok"
    assert by_id["bus:1:endpoint"]["status"] == "blocked"
    assert by_id["bus:1:endpoint"]["blocked_by"] == "bus:1:health"
    assert "blocked_by" not in by_id["db:mysql:1001:query"]
