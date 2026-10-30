from __future__ import annotations

from _metrics.readability import ReadStatus
from topology.edges import (
    apply_sender_boundary_amber_cap,
    build_edge,
    queue_level_for_count,
    thresholds_for_pipeline,
)
from topology.nodes import ColorLevel

OK = ReadStatus.OK
DOWN = ReadStatus.DOWN


def _edge(**overrides):
    base = dict(
        pipeline_id=1,
        source_node_id="db:mysql:1001",
        bus_node_id="bus:1",
        target_node_id="db:postgresql:1002",
        source_emitted=OK,
        bus_health_emitted=OK,
        bus_endpoint_emitted=OK,
        target_emitted=OK,
        queued_count=0,
        yellow_threshold=50,
        red_threshold=500,
        sender_boundary_stuck=False,
        packets=0,
    )
    base.update(overrides)
    return build_edge(**base)


def test_p34_edge_id_is_a_directed_string_source_bus_target():
    edge = _edge()
    assert edge.edge_id == "db:mysql:1001->bus:1->db:postgresql:1002"


def test_p34_edge_has_exactly_three_segments_in_fixed_order():
    edge = _edge()
    assert [s.kind for s in edge.segments] == ["sender", "bus", "receiver"]


def test_p34_ordered_pair_is_directional_reversed_source_target_is_a_different_edge():
    forward = _edge(source_node_id="db:mysql:1001", target_node_id="db:postgresql:1002")
    reverse = _edge(source_node_id="db:postgresql:1002", target_node_id="db:mysql:1001")
    assert forward.edge_id != reverse.edge_id


def test_p38_queue_level_for_count_thresholds():
    assert queue_level_for_count(10, yellow_threshold=50, red_threshold=500) == ColorLevel.GREEN
    assert queue_level_for_count(50, yellow_threshold=50, red_threshold=500) == ColorLevel.YELLOW
    assert queue_level_for_count(500, yellow_threshold=50, red_threshold=500) == ColorLevel.RED


def test_p38_edge_level_reflects_bus_queue_count_when_all_nodes_ok():
    edge = _edge(queued_count=500, yellow_threshold=50, red_threshold=500)
    assert edge.level == ColorLevel.RED
    bus_segment = edge.segments[1]
    assert bus_segment.level == ColorLevel.RED


def test_p39_amber_cap_downgrades_red_to_yellow():
    assert apply_sender_boundary_amber_cap(ColorLevel.RED) == ColorLevel.YELLOW


def test_p39_amber_cap_leaves_yellow_and_green_unchanged():
    assert apply_sender_boundary_amber_cap(ColorLevel.YELLOW) == ColorLevel.YELLOW
    assert apply_sender_boundary_amber_cap(ColorLevel.GREEN) == ColorLevel.GREEN


def test_p39_amber_cap_leaves_grey_unchanged_never_brightens_a_genuinely_down_sender():
    assert apply_sender_boundary_amber_cap(ColorLevel.GREY) == ColorLevel.GREY


def test_p39_stuck_sender_edge_never_reaches_red_via_the_sender_segment():
    edge = _edge(sender_boundary_stuck=True)
    sender_segment = edge.segments[0]
    assert apply_sender_boundary_amber_cap(ColorLevel.RED) != ColorLevel.RED
    assert sender_segment.level != ColorLevel.RED


def test_p41_edge_level_is_worst_of_its_three_segments():
    edge = _edge(queued_count=500, yellow_threshold=50, red_threshold=500)
    assert edge.level == ColorLevel.RED

    edge2 = _edge(bus_health_emitted=DOWN)
    assert edge2.level == ColorLevel.GREY


def test_p41_a_down_receiver_db_node_renders_the_edge_red_p40_interaction():
    edge = _edge(target_emitted=DOWN)
    assert edge.segments[2].level == ColorLevel.RED
    assert edge.level == ColorLevel.RED


def test_p41_a_down_bus_renders_the_edge_grey_even_with_a_red_backlog():
    edge = _edge(bus_health_emitted=DOWN, queued_count=999, yellow_threshold=50, red_threshold=500)
    assert edge.level == ColorLevel.GREY


def test_p42_packets_are_carried_on_the_edge_as_a_single_receiver_end_field():
    edge = _edge(packets=7)
    assert edge.packets == 7
    for segment in edge.segments:
        assert not hasattr(segment, "packets")


def test_p103_no_override_uses_global_defaults():
    yellow, red = thresholds_for_pipeline(42, {}, default_yellow=50, default_red=500)
    assert (yellow, red) == (50, 500)


def test_p103_full_override_takes_precedence_over_globals():
    overrides = {"42": {"yellow": 10, "red": 20}}
    yellow, red = thresholds_for_pipeline(42, overrides, default_yellow=50, default_red=500)
    assert (yellow, red) == (10, 20)


def test_p103_partial_override_falls_back_to_the_global_default_for_the_missing_key():
    overrides = {"42": {"yellow": 5}}
    yellow, red = thresholds_for_pipeline(42, overrides, default_yellow=50, default_red=500)
    assert (yellow, red) == (5, 500)


def test_p103_override_is_keyed_by_string_pipeline_id_and_only_applies_to_its_own_pipeline():
    overrides = {"42": {"yellow": 10, "red": 20}}
    yellow, red = thresholds_for_pipeline(99, overrides, default_yellow=50, default_red=500)
    assert (yellow, red) == (50, 500)


def test_p103_edge_colour_actually_uses_the_resolved_pipeline_thresholds():
    yellow, red = thresholds_for_pipeline(42, {"42": {"yellow": 5, "red": 10}}, default_yellow=50, default_red=500)
    edge = _edge(pipeline_id=42, queued_count=6, yellow_threshold=yellow, red_threshold=red)
    assert edge.segments[1].level == ColorLevel.YELLOW
    assert edge.level == ColorLevel.YELLOW
