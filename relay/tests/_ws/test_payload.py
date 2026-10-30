from _metrics.attribution import AttributionResult
from _metrics.readability import ReadStatus
from _ws.payload import (
    DELTA_KEY_ORDER,
    SNAPSHOT_KEY_ORDER,
    MachineSections,
    assemble_machine_delta,
    assemble_machine_snapshot,
    assemble_ws_delta,
    assemble_ws_snapshot,
    to_wire_edge,
)
from topology.edges import build_edge
from topology.nodes import ColorLevel


def _attribution() -> AttributionResult:
    return AttributionResult(
        emitted={"db:mysql:1001:query": ReadStatus.OK, "bus:1:endpoint": ReadStatus.DOWN},
        blocked_by={},
        read_status={},
    )


def _edge(pipeline_id=12, packets=3):
    return build_edge(
        pipeline_id=pipeline_id,
        source_node_id="db:mysql:1001",
        bus_node_id="bus:1",
        target_node_id="db:postgresql:1002",
        source_emitted=ReadStatus.OK,
        bus_health_emitted=ReadStatus.OK,
        bus_endpoint_emitted=ReadStatus.OK,
        target_emitted=ReadStatus.OK,
        queued_count=0,
        yellow_threshold=50,
        red_threshold=500,
        packets=packets,
    )


def test_o17_snapshot_key_order_exactly():
    sections = MachineSections(attribution=_attribution(), object_kinds={"db:mysql:1001:query": "db", "bus:1:endpoint": "bus"})
    snapshot = assemble_machine_snapshot(machine_id=1, seq=1, sections=sections)
    assert list(snapshot.keys()) == SNAPSHOT_KEY_ORDER


def test_o17_delta_key_order_includes_packets_snapshot_does_not():
    sections = MachineSections(pairs=[{"id": "p1", "packets": 2}], packets=[{"pipeline_id": 12, "ids": []}])
    delta = assemble_machine_delta(machine_id=1, seq=2, previous=None, current=sections)
    assert list(delta.keys()) == [k for k in DELTA_KEY_ORDER if k in delta]
    assert "packets" in delta
    snapshot = assemble_machine_snapshot(machine_id=1, seq=1, sections=sections)
    assert "packets" not in snapshot


def test_p22_objects_wire_uses_only_emitted_statuses_via_attribution():
    sections = MachineSections(attribution=_attribution(), object_kinds={"db:mysql:1001:query": "db", "bus:1:endpoint": "bus"})
    snapshot = assemble_machine_snapshot(machine_id=1, seq=1, sections=sections)
    statuses = {o["status"] for o in snapshot["objects"]}
    assert statuses <= {"ok", "down", "blocked"}


def test_p127_delta_always_carries_pairs_with_id_and_packets_even_when_unchanged():
    prev_sections = MachineSections(pairs=[{"id": "p1", "packets": 5, "level": "green"}])
    curr_sections = MachineSections(pairs=[{"id": "p1", "packets": 5, "level": "green"}])
    delta = assemble_machine_delta(machine_id=1, seq=2, previous=prev_sections, current=curr_sections)
    assert delta["pairs"] == [{"id": "p1", "packets": 5}]


def test_p127_delta_pairs_never_dropped_even_when_every_other_section_is_unchanged():
    sections = MachineSections(pairs=[{"id": "p1", "packets": 0}])
    delta = assemble_machine_delta(machine_id=1, seq=2, previous=sections, current=sections)
    assert "pairs" in delta
    assert delta["pairs"] == [{"id": "p1", "packets": 0}]


def test_cross_machine_pipeline_both_sides_carry_the_identical_underlying_edge():
    edge = _edge(pipeline_id=12, packets=7)

    machine_1_sections = MachineSections(edges=[edge], edge_cross_machine={edge.edge_id: {"direction": "to", "counterpart_machine_id": 2, "service_bus_id": 1}})
    machine_2_sections = MachineSections(edges=[edge], edge_cross_machine={edge.edge_id: {"direction": "from", "counterpart_machine_id": 1, "service_bus_id": 1}})

    wire_1 = machine_1_sections.topology_wire()["edges"][0]
    wire_2 = machine_2_sections.topology_wire()["edges"][0]

    for key in ("edge_id", "pipeline_id", "level", "packets"):
        assert wire_1[key] == wire_2[key]
    assert wire_1["cross_machine"]["direction"] == "to"
    assert wire_2["cross_machine"]["direction"] == "from"


def test_to_wire_edge_with_no_cross_machine_annotation_is_none():
    edge = _edge()
    wire = to_wire_edge(edge, None)
    assert wire["cross_machine"] is None


def test_ws_thin_inventory_snapshot_key_order():
    snapshot = assemble_ws_snapshot(seq=1, machines=[{"machine_id": 1}])
    assert list(snapshot.keys()) == ["type", "seq", "ts", "machines", "activeAlerts"]


def test_ws_thin_inventory_delta_omits_machines_key_when_nothing_changed():
    delta = assemble_ws_delta(seq=2, changed_machines=[], removed_machine_ids=[])
    assert "machines" not in delta


def test_ws_thin_inventory_delta_carries_changed_and_removed():
    delta = assemble_ws_delta(seq=2, changed_machines=[{"machine_id": 2, "status": "offline"}], removed_machine_ids=[])
    assert delta["machines"] == {"changed": [{"machine_id": 2, "status": "offline"}], "removed": []}


def test_ws_delta_carries_pairs_and_pipelines_only_when_given():
    assert "pairs" not in assemble_ws_delta(seq=2, changed_machines=[], removed_machine_ids=[])
    delta = assemble_ws_delta(seq=3, changed_machines=[], removed_machine_ids=[], pairs=[], pipelines=[{"pipelineId": 1}])
    assert delta["pairs"] == [] and delta["pipelines"] == [{"pipelineId": 1}]
