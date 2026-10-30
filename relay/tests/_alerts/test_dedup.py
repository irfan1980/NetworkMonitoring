from __future__ import annotations

from _alerts.dedup import AlertKey, OpenIncidentIndex


def test_alert_id_is_deterministic_for_the_same_root_cause():
    a = AlertKey("object", "db:mysql:1001:query")
    b = AlertKey("object", "db:mysql:1001:query")
    assert a.alert_id() == b.alert_id()


def test_claim_then_is_open():
    index = OpenIncidentIndex()
    key = AlertKey("bus", "12")
    assert index.is_open(key) is False
    index.claim(key, {"pipeline_id": 12})
    assert index.is_open(key) is True
    assert index.get(key) == {"pipeline_id": 12}


def test_release_closes_the_incident_and_it_can_be_reclaimed():
    index = OpenIncidentIndex()
    key = AlertKey("edge", "e1")
    index.claim(key, {})
    index.release(key)
    assert index.is_open(key) is False
    index.claim(key, {"reopened": True})
    assert index.get(key) == {"reopened": True}


def test_claiming_an_already_open_key_is_a_no_op_dedupe_p21():
    index = OpenIncidentIndex()
    key = AlertKey("pipeline", "5")
    index.claim(key, {"first": True})
    assert index.is_open(key) is True
    index.claim(key, {"first": False})
    assert len(index) == 1


def test_no_second_key_set_is_ever_consulted_only_the_live_index():
    index = OpenIncidentIndex()
    key1 = AlertKey("object", "a")
    key2 = AlertKey("object", "b")
    index.claim(key1, {})
    index.claim(key2, {})
    assert index.open_ids() == {key1.alert_id(), key2.alert_id()}
    index.release(key1)
    assert index.open_ids() == {key2.alert_id()}
