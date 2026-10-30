import pytest

from _ws.diff import diff_pairs_list, diff_top_level_keys


def test_generic_differ_omits_unchanged_keys():
    prev = {"objects": [1, 2], "topology": {"a": 1}}
    curr = {"objects": [1, 2], "topology": {"a": 2}}
    assert diff_top_level_keys(prev, curr) == {"topology": {"a": 2}}


def test_generic_differ_refuses_to_touch_pairs_at_all():
    with pytest.raises(ValueError, match="pairs"):
        diff_top_level_keys({}, {"pairs": [{"id": "p1", "packets": 3}]})


def test_p127_diff_pairs_list_always_includes_id_and_packets_even_unchanged():
    prev = [{"id": "p1", "packets": 5, "level": "green"}]
    curr = [{"id": "p1", "packets": 5, "level": "green"}]
    result = diff_pairs_list(prev, curr)
    assert result["changed"] == [{"id": "p1", "packets": 5}]


def test_p127_diff_pairs_list_includes_changed_fields_too():
    prev = [{"id": "p1", "packets": 5, "level": "green"}]
    curr = [{"id": "p1", "packets": 5, "level": "red"}]
    result = diff_pairs_list(prev, curr)
    assert result["changed"] == [{"id": "p1", "packets": 5, "level": "red"}]


def test_diff_pairs_list_reports_removed_pairs_by_id():
    prev = [{"id": "p1", "packets": 0}, {"id": "p2", "packets": 0}]
    curr = [{"id": "p1", "packets": 0}]
    result = diff_pairs_list(prev, curr)
    assert result["removed"] == ["p2"]
