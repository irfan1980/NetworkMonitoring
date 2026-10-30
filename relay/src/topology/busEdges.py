from __future__ import annotations

from _metrics.readability import ReadStatus
from topology.nodes import ColorLevel, compute_node_status, worst_of


def combine_bus_segment(
    health_emitted: ReadStatus,
    endpoint_emitted: ReadStatus,
    queue_level: ColorLevel = ColorLevel.GREEN,
) -> ColorLevel:
    health_level = compute_node_status("bus", health_emitted, queue_level=ColorLevel.GREEN)
    endpoint_level = compute_node_status("bus", endpoint_emitted, queue_level=queue_level)
    return worst_of(health_level, endpoint_level)
