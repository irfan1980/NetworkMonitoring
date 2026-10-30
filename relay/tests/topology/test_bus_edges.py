from __future__ import annotations

from _metrics.readability import ReadStatus
from topology.busEdges import combine_bus_segment
from topology.nodes import ColorLevel

OK = ReadStatus.OK
DOWN = ReadStatus.DOWN
BLOCKED = ReadStatus.BLOCKED


def test_p35_both_objects_ok_uses_the_queue_driven_colour():
    assert combine_bus_segment(OK, OK, queue_level=ColorLevel.YELLOW) == ColorLevel.YELLOW
    assert combine_bus_segment(OK, OK, queue_level=ColorLevel.GREEN) == ColorLevel.GREEN


def test_p35_health_down_makes_the_whole_bus_segment_grey():
    assert combine_bus_segment(DOWN, OK, queue_level=ColorLevel.RED) == ColorLevel.GREY


def test_p35_endpoint_blocked_makes_the_whole_bus_segment_grey():
    assert combine_bus_segment(OK, BLOCKED, queue_level=ColorLevel.RED) == ColorLevel.GREY


def test_p35_relay_end_bus_node_never_reaches_red_via_the_db_exception():
    assert combine_bus_segment(DOWN, DOWN, queue_level=ColorLevel.GREEN) == ColorLevel.GREY
