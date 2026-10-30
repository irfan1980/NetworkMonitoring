from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from _metrics.attribution import AttributionResult
from _metrics.pair_scan import clamp_packet_count
from _ws.diff import diff_pairs_list, diff_top_level_keys
from _ws.wire_objects import build_wire_object, build_wire_objects
from topology.edges import TopologyEdge

SNAPSHOT_KEY_ORDER = [
    "type", "seq", "ts", "machine_id", "active_services", "emulator", "bus",
    "pipelines", "objects", "topology", "pairs", "activeAlerts", "summary",
]
DELTA_KEY_ORDER = [
    "type", "seq", "ts", "machine_id", "active_services", "emulator", "bus",
    "pipelines", "objects", "topology", "pairs", "packets", "activeAlerts", "summary",
]
WS_SNAPSHOT_KEY_ORDER = ["type", "seq", "ts", "machines", "activeAlerts"]
WS_DELTA_KEY_ORDER = ["type", "seq", "ts", "machines", "activeAlerts", "pairs", "pipelines"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ordered(d: dict, order: list[str]) -> dict:
    result = {k: d[k] for k in order if k in d}
    result.update({k: v for k, v in d.items() if k not in result})
    return result


def to_wire_edge(edge: TopologyEdge, cross_machine: Optional[dict] = None) -> dict:
    return {
        "edge_id": edge.edge_id,
        "pipeline_id": edge.pipeline_id,
        "source_node_id": edge.source_node_id,
        "bus_node_id": edge.bus_node_id,
        "target_node_id": edge.target_node_id,
        "level": edge.level.value,
        "packets": clamp_packet_count(edge.packets),
        "cross_machine": cross_machine,
    }


@dataclass
class MachineSections:

    active_services: list[dict] = field(default_factory=list)
    emulator: dict = field(default_factory=dict)
    bus: list[dict] = field(default_factory=list)
    attribution: Optional[AttributionResult] = None
    object_kinds: dict[str, str] = field(default_factory=dict)
    edges: list[TopologyEdge] = field(default_factory=list)
    edge_cross_machine: dict[str, Optional[dict]] = field(default_factory=dict)
    topology_nodes: list[dict] = field(default_factory=list)
    bus_edges: list[dict] = field(default_factory=list)
    pipelines: list[dict] = field(default_factory=list)
    pairs: list[dict] = field(default_factory=list)
    packets: list[dict] = field(default_factory=list)
    active_alerts: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=lambda: {"level": "unknown", "note": "summary not available"})

    def objects_wire(self) -> list[dict]:
        if self.attribution is None:
            return []
        return build_wire_objects(self.attribution.emitted, self.object_kinds, self.attribution.blocked_by)

    def topology_wire(self) -> dict:
        return {
            "nodes": self.topology_nodes,
            "edges": [to_wire_edge(e, self.edge_cross_machine.get(e.edge_id)) for e in self.edges],
            "busEdges": self.bus_edges,
        }


def assemble_machine_snapshot(machine_id: int, seq: int, sections: MachineSections) -> dict:
    body = {
        "type": "snapshot",
        "seq": seq,
        "ts": _now_iso(),
        "machine_id": machine_id,
        "active_services": sections.active_services,
        "emulator": sections.emulator,
        "bus": sections.bus,
        "pipelines": sections.pipelines,
        "objects": sections.objects_wire(),
        "topology": sections.topology_wire(),
        "pairs": sections.pairs,
        "activeAlerts": sections.active_alerts,
        "summary": sections.summary,
    }
    return _ordered(body, SNAPSHOT_KEY_ORDER)


def assemble_machine_delta(machine_id: int, seq: int, previous: Optional[MachineSections], current: MachineSections) -> dict:
    prev_top = {} if previous is None else {
        "active_services": previous.active_services,
        "emulator": previous.emulator,
        "bus": previous.bus,
        "pipelines": previous.pipelines,
        "objects": previous.objects_wire(),
        "topology": previous.topology_wire(),
        "activeAlerts": previous.active_alerts,
        "summary": previous.summary,
    }
    curr_top = {
        "active_services": current.active_services,
        "emulator": current.emulator,
        "bus": current.bus,
        "pipelines": current.pipelines,
        "objects": current.objects_wire(),
        "topology": current.topology_wire(),
        "activeAlerts": current.active_alerts,
        "summary": current.summary,
    }
    changed = diff_top_level_keys(prev_top, curr_top)

    pairs_delta = diff_pairs_list(previous.pairs if previous else None, current.pairs)

    body = {
        "type": "delta",
        "seq": seq,
        "ts": _now_iso(),
        "machine_id": machine_id,
        **changed,
        "pairs": pairs_delta["changed"],
        "packets": current.packets,
        "activeAlerts": current.active_alerts,
        "summary": current.summary,
    }
    return _ordered(body, DELTA_KEY_ORDER)


def assemble_ws_snapshot(seq: int, machines: list[dict], active_alerts: Optional[list[dict]] = None) -> dict:
    body = {
        "type": "snapshot",
        "seq": seq,
        "ts": _now_iso(),
        "machines": machines,
        "activeAlerts": active_alerts or [],
    }
    return _ordered(body, WS_SNAPSHOT_KEY_ORDER)


def assemble_ws_delta(
    seq: int,
    changed_machines: list[dict],
    removed_machine_ids: list[int],
    active_alerts: Optional[list[dict]] = None,
    pairs: Optional[list[dict]] = None,
    pipelines: Optional[list[dict]] = None,
) -> dict:
    body: dict = {"type": "delta", "seq": seq, "ts": _now_iso()}
    if pairs is not None:
        body["pairs"] = pairs
    if pipelines is not None:
        body["pipelines"] = pipelines
    if changed_machines or removed_machine_ids:
        body["machines"] = {"changed": changed_machines, "removed": removed_machine_ids}
    if active_alerts:
        body["activeAlerts"] = active_alerts
    return _ordered(body, WS_DELTA_KEY_ORDER)
