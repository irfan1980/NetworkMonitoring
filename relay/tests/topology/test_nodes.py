from __future__ import annotations

import pytest

from _metrics.readability import ReadStatus
from topology.nodes import ColorLevel, TopologyNode, compute_node_status, worst_of

OK = ReadStatus.OK
DOWN = ReadStatus.DOWN
BLOCKED = ReadStatus.BLOCKED


def test_p36_color_level_has_exactly_four_members_no_orange():
    members = {level.value for level in ColorLevel}
    assert members == {"grey", "red", "yellow", "green"}
    assert "orange" not in members


def test_p41_worst_of_precedence_order():
    assert worst_of(ColorLevel.GREEN, ColorLevel.YELLOW) == ColorLevel.YELLOW
    assert worst_of(ColorLevel.YELLOW, ColorLevel.RED) == ColorLevel.RED
    assert worst_of(ColorLevel.RED, ColorLevel.GREY) == ColorLevel.GREY
    assert worst_of(ColorLevel.GREEN, ColorLevel.RED, ColorLevel.YELLOW, ColorLevel.GREY) == ColorLevel.GREY


def test_p41_worst_of_single_value_and_empty_call():
    assert worst_of(ColorLevel.YELLOW) == ColorLevel.YELLOW
    with pytest.raises(ValueError):
        worst_of()


def test_p37_down_bus_node_is_grey_never_red():
    assert compute_node_status("bus", DOWN) == ColorLevel.GREY
    assert compute_node_status("bus", BLOCKED) == ColorLevel.GREY


def test_p40_db_node_down_renders_red_not_grey():
    assert compute_node_status("db", DOWN) == ColorLevel.RED


def test_p40_bus_node_is_unaffected_by_the_db_red_exception():
    assert compute_node_status("bus", DOWN) == ColorLevel.GREY
    assert compute_node_status("db", DOWN) == ColorLevel.RED


@pytest.mark.parametrize(
    "kind,emitted,queue_level,expected",
    [
        ("db", OK, ColorLevel.GREEN, ColorLevel.GREEN),
        ("db", OK, ColorLevel.YELLOW, ColorLevel.YELLOW),
        ("db", OK, ColorLevel.RED, ColorLevel.RED),
        ("db", DOWN, ColorLevel.GREEN, ColorLevel.RED),
        ("db", BLOCKED, ColorLevel.GREEN, ColorLevel.RED),
        ("bus", OK, ColorLevel.RED, ColorLevel.RED),
        ("bus", DOWN, ColorLevel.RED, ColorLevel.GREY),
        ("bus", BLOCKED, ColorLevel.GREEN, ColorLevel.GREY),
    ],
)
def test_p102_compute_node_status_kind_based_branching(kind, emitted, queue_level, expected):
    assert compute_node_status(kind, emitted, queue_level=queue_level) == expected


def test_p34_topology_node_is_a_plain_id_kind_pair():
    node = TopologyNode(node_id="db:mysql:1001:query", kind="db")
    assert node.node_id == "db:mysql:1001:query"
    assert node.kind == "db"
