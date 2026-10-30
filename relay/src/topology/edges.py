from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from _metrics.readability import ReadStatus
from topology.busEdges import combine_bus_segment
from topology.nodes import ColorLevel, NodeKind, compute_node_status, worst_of


def thresholds_for_pipeline(
    pipeline_id: int,
    pending_thresholds: dict,
    default_yellow: int,
    default_red: int,
) -> tuple[int, int]:
    override = pending_thresholds.get(str(pipeline_id)) or pending_thresholds.get(str(pipeline_id).rpartition(":")[2])
    if not override:
        return default_yellow, default_red
    yellow = override.get("yellow", default_yellow)
    red = override.get("red", default_red)
    return yellow, red


def queue_level_for_count(count: int, yellow_threshold: int, red_threshold: int) -> ColorLevel:
    if count >= red_threshold:
        return ColorLevel.RED
    if count >= yellow_threshold:
        return ColorLevel.YELLOW
    return ColorLevel.GREEN


def apply_sender_boundary_amber_cap(level: ColorLevel) -> ColorLevel:
    return ColorLevel.YELLOW if level == ColorLevel.RED else level


@dataclass(frozen=True)
class TopologySegment:
    kind: str
    level: ColorLevel


@dataclass(frozen=True)
class TopologyEdge:

    edge_id: str
    pipeline_id: int
    source_node_id: str
    bus_node_id: str
    target_node_id: str
    segments: tuple[TopologySegment, TopologySegment, TopologySegment]
    level: ColorLevel
    packets: int = 0


def build_edge(
    pipeline_id: int,
    source_node_id: str,
    bus_node_id: str,
    target_node_id: str,
    source_emitted: ReadStatus,
    bus_health_emitted: ReadStatus,
    bus_endpoint_emitted: ReadStatus,
    target_emitted: ReadStatus,
    queued_count: int,
    yellow_threshold: int,
    red_threshold: int,
    sender_boundary_stuck: bool = False,
    packets: int = 0,
) -> TopologyEdge:
    sender_level = compute_node_status("db", source_emitted, queue_level=ColorLevel.GREEN)
    if sender_boundary_stuck:
        sender_level = apply_sender_boundary_amber_cap(sender_level)

    bus_queue_level = queue_level_for_count(queued_count, yellow_threshold, red_threshold)
    bus_level = combine_bus_segment(bus_health_emitted, bus_endpoint_emitted, queue_level=bus_queue_level)

    receiver_level = compute_node_status("db", target_emitted, queue_level=ColorLevel.GREEN)

    segments = (
        TopologySegment(kind="sender", level=sender_level),
        TopologySegment(kind="bus", level=bus_level),
        TopologySegment(kind="receiver", level=receiver_level),
    )
    edge_level = worst_of(*(s.level for s in segments))

    return TopologyEdge(
        edge_id=f"{source_node_id}->{bus_node_id}->{target_node_id}",
        pipeline_id=pipeline_id,
        source_node_id=source_node_id,
        bus_node_id=bus_node_id,
        target_node_id=target_node_id,
        segments=segments,
        level=edge_level,
        packets=packets,
    )
