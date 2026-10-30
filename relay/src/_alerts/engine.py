from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from _alerts.dedup import AlertKey, OpenIncidentIndex
from _alerts.severity import object_severity, pipeline_severity
from _metrics.readability import ReadStatus
from topology.nodes import ColorLevel

if TYPE_CHECKING:  # pragma: no cover
    from _contract.schema import ObjectSeverities
    from _metrics.bus_incident import LostAlertTick, PipelineAttribution

STAGE_ORDER: tuple[str, ...] = (
    "vanished",
    "objectTransitions",
    "edgeTransitions",
    "pipelineTransitions",
    "relayHeartbeats",
    "busAttributedAlerts",
    "rowLevelAlerts",
)


@dataclass
class PipelineSnapshot:

    pipeline_id: int
    bus_id: Optional[int] = None
    row_severities: list[int] = field(default_factory=list)
    degraded: bool = False
    degraded_reasons: tuple[str, ...] = field(default_factory=tuple)
    heartbeat_class: str = "ok"
    bus_attribution: Optional["PipelineAttribution"] = None
    lost_count: int = 0


@dataclass
class MachineAlertScope:

    machine_id: int
    pipelines: dict[int, PipelineSnapshot] = field(default_factory=dict)
    emitted: dict[str, ReadStatus] = field(default_factory=dict)
    previous_emitted: dict[str, ReadStatus] = field(default_factory=dict)
    object_kind_of: dict[str, str] = field(default_factory=dict)
    edge_levels: dict[str, ColorLevel] = field(default_factory=dict)
    previous_edge_levels: dict[str, ColorLevel] = field(default_factory=dict)
    edge_pipeline_of: dict[str, int] = field(default_factory=dict)
    vanished_pipeline_ids: set[int] = field(default_factory=set)

    def prune_vanished(self) -> list[int]:
        pruned = [pid for pid in self.vanished_pipeline_ids if pid in self.pipelines]
        for pid in pruned:
            del self.pipelines[pid]
        return pruned


@dataclass(frozen=True)
class EngineResult:
    events: tuple[dict, ...]
    stage_order: tuple[str, ...]
    red_pipeline_ids: frozenset[int]


def _stage_vanished(scope: MachineAlertScope, incidents: OpenIncidentIndex) -> list[dict]:
    events: list[dict] = []
    scope.prune_vanished()
    for pid in scope.vanished_pipeline_ids:
        key = AlertKey("pipeline", str(pid))
        if incidents.is_open(key):
            incidents.release(key)
            events.append({"type": "resolved", "kind": "pipeline", "pipeline_id": pid, "reason": "vanished"})
    return events


def _stage_object_transitions(
    scope: MachineAlertScope, incidents: OpenIncidentIndex, object_severities: "ObjectSeverities",
) -> list[dict]:
    events: list[dict] = []
    for object_id, emitted in scope.emitted.items():
        kind = scope.object_kind_of.get(object_id, "db")
        prev = scope.previous_emitted.get(object_id)
        key = AlertKey("object", object_id)

        if emitted == ReadStatus.OK:
            if incidents.is_open(key):
                incidents.release(key)
                events.append({"type": "resolved", "kind": "object", "object_id": object_id})
            continue

        if incidents.is_open(key):
            continue

        from_status = prev.value if prev is not None else "unknown"
        severity = object_severity(kind, object_severities)
        incidents.claim(key, {"object_id": object_id, "kind": kind, "severity": severity})
        events.append({
            "type": "raised", "kind": "object", "object_id": object_id,
            "from": from_status, "to": emitted.value, "severity": severity,
            "alert_id": key.alert_id(),
        })
    return events


def _stage_edge_transitions(scope: MachineAlertScope, incidents: OpenIncidentIndex) -> tuple[list[dict], frozenset[int]]:
    events: list[dict] = []
    red_pipeline_ids: set[int] = set()

    for edge_id, level in scope.edge_levels.items():
        if level == ColorLevel.RED:
            pid = scope.edge_pipeline_of.get(edge_id)
            if pid is not None:
                red_pipeline_ids.add(pid)

        prev = scope.previous_edge_levels.get(edge_id)
        key = AlertKey("edge", edge_id)

        if level != ColorLevel.RED:
            if incidents.is_open(key):
                incidents.release(key)
                events.append({"type": "resolved", "kind": "edge", "edge_id": edge_id})
            continue

        if incidents.is_open(key):
            continue

        from_level = prev.value if prev is not None else "unknown"
        incidents.claim(key, {"edge_id": edge_id})
        events.append({"type": "raised", "kind": "edge", "edge_id": edge_id, "from": from_level, "to": level.value})

    return events, frozenset(red_pipeline_ids)


def _stage_pipeline_transitions(
    scope: MachineAlertScope, incidents: OpenIncidentIndex, previous_degraded: dict[int, bool],
) -> list[dict]:
    events: list[dict] = []
    for pid, snap in scope.pipelines.items():
        key = AlertKey("pipeline", str(pid))

        if not snap.degraded:
            if incidents.is_open(key):
                incidents.release(key)
                events.append({"type": "resolved", "kind": "pipeline", "pipeline_id": pid})
            continue

        if incidents.is_open(key):
            continue

        if pid not in previous_degraded:
            from_status = "unknown"
        elif previous_degraded[pid]:
            from_status = "Degraded"
        else:
            from_status = "ok"

        severity = pipeline_severity(list(snap.row_severities))
        incidents.claim(key, {"pipeline_id": pid, "severity": severity})
        events.append({
            "type": "raised", "kind": "pipeline", "pipeline_id": pid,
            "from": from_status, "to": "Degraded", "severity": severity,
            "reasons": list(snap.degraded_reasons),
        })
    return events


def _stage_relay_heartbeats(scope: MachineAlertScope, incidents: OpenIncidentIndex) -> list[dict]:
    events: list[dict] = []
    for pid, snap in scope.pipelines.items():
        key = AlertKey("relay_heartbeat", str(pid))
        if snap.heartbeat_class == "ok":
            if incidents.is_open(key):
                incidents.release(key)
                events.append({"type": "resolved", "kind": "relay_heartbeat", "pipeline_id": pid})
            continue
        if incidents.is_open(key):
            continue
        incidents.claim(key, {"pipeline_id": pid, "heartbeat_class": snap.heartbeat_class})
        events.append({"type": "raised", "kind": "relay_heartbeat", "pipeline_id": pid, "to": snap.heartbeat_class})
    return events


def _stage_bus_attributed_alerts(
    scope: MachineAlertScope, incidents: OpenIncidentIndex, lost_ticks: dict[int, "LostAlertTick"],
) -> list[dict]:
    events: list[dict] = []
    for pid, snap in scope.pipelines.items():
        attribution = snap.bus_attribution
        key = AlertKey("bus", str(pid))
        if attribution is not None and attribution.cause in ("bus_down", "receiver_down"):
            if not incidents.is_open(key):
                incidents.claim(key, {"pipeline_id": pid, "cause": attribution.cause})
                events.append({
                    "type": "raised", "kind": "bus", "pipeline_id": pid,
                    "cause": attribution.cause, "blocked_by": attribution.blocked_by,
                })
        else:
            if incidents.is_open(key):
                incidents.release(key)
                events.append({"type": "resolved", "kind": "bus", "pipeline_id": pid})

        tick = lost_ticks.get(pid)
        if tick is None:
            continue
        lost_key = AlertKey("lost", str(pid))
        if tick.newly_raised:
            incidents.claim(lost_key, {"pipeline_id": pid, "severity": tick.severity})
            events.append({"type": "raised", "kind": "lost", "pipeline_id": pid, "severity": tick.severity})
        elif tick.newly_resolved:
            incidents.release(lost_key)
            events.append({"type": "resolved", "kind": "lost", "pipeline_id": pid})
    return events


def row_group_severity(row_severities_by_direction: dict[str, list[int]], direction: str, fallback: int = 3) -> int:
    candidates = row_severities_by_direction.get(direction, [])
    if candidates:
        return min(candidates)
    return fallback


def _stage_row_level_alerts(
    scope: MachineAlertScope, incidents: OpenIncidentIndex, row_data: dict[int, list[dict]],
) -> list[dict]:
    events: list[dict] = []
    for pid, snap in scope.pipelines.items():
        if not snap.degraded:
            continue
        rows = row_data.get(pid, [])
        by_direction: dict[str, list[int]] = {}
        for row in rows:
            if row.get("severity") is not None:
                by_direction.setdefault(row["direction"], []).append(row["severity"])

        for row in rows:
            key = AlertKey("row", f"{pid}:{row['row_id']}")
            severity = row.get("severity")
            if severity is None:
                severity = row_group_severity(by_direction, row["direction"])
            if incidents.is_open(key):
                continue
            incidents.claim(key, {"pipeline_id": pid, "row_id": row["row_id"], "severity": severity})
            events.append({
                "type": "raised", "kind": "row", "pipeline_id": pid,
                "row_id": row["row_id"], "severity": severity,
            })
    return events


def evaluate_machine_alerts(
    scope: MachineAlertScope,
    incidents: OpenIncidentIndex,
    *,
    object_severities: "ObjectSeverities",
    previous_degraded: dict[int, bool],
    lost_ticks: dict[int, "LostAlertTick"],
    row_data: dict[int, list[dict]],
) -> EngineResult:
    events: list[dict] = []
    executed: list[str] = []

    events += _stage_vanished(scope, incidents)
    executed.append("vanished")

    events += _stage_object_transitions(scope, incidents, object_severities)
    executed.append("objectTransitions")

    edge_events, red_pipeline_ids = _stage_edge_transitions(scope, incidents)
    events += edge_events
    executed.append("edgeTransitions")

    events += _stage_pipeline_transitions(scope, incidents, previous_degraded)
    executed.append("pipelineTransitions")

    events += _stage_relay_heartbeats(scope, incidents)
    executed.append("relayHeartbeats")

    events += _stage_bus_attributed_alerts(scope, incidents, lost_ticks)
    executed.append("busAttributedAlerts")

    events += _stage_row_level_alerts(scope, incidents, row_data)
    executed.append("rowLevelAlerts")

    assert tuple(executed) == STAGE_ORDER
    return EngineResult(events=tuple(events), stage_order=tuple(executed), red_pipeline_ids=red_pipeline_ids)
