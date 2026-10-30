from __future__ import annotations

from typing import Optional

from _alerts.shared_ack import apply_acks
from _alerts.summary import compute_summary, compute_summary_aggregates
from _ws.estate_payload import _wire_summary_level, pipeline_alerts_wire


def hosted_server_ids(machine) -> set[int]:
    return {n.server_id for n in machine.nodes if n.node_type in ("Database", "POS")}


def used_bus_ids(config, machine, hosted_ids: Optional[set[int]] = None) -> set[int]:
    if hosted_ids is None:
        hosted_ids = hosted_server_ids(machine)
    servers_by_id = {s.id: s for s in config.servers}
    ids: set[int] = set()
    for sid in hosted_ids:
        server = servers_by_id.get(sid)
        if server is not None:
            ids.update(server.used_buses)
    return ids


def server_machine_map(config) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for machine in config.machines:
        for sid in hosted_server_ids(machine):
            mapping.setdefault(sid, machine.machine_id)
    return mapping


def _pair_bus_lookup(pairs_wire: list[dict]) -> dict[tuple[int, int], Optional[str]]:
    out: dict[tuple[int, int], Optional[str]] = {}
    for pair in pairs_wire:
        try:
            out[(int(pair["from"]), int(pair["to"]))] = pair.get("bus")
        except (KeyError, TypeError, ValueError):
            continue
    return out


def scope_pipelines_for_machine(
    pipelines_wire: list[dict], machine_id: int, server_machine: dict[int, int], pairs_wire: list[dict]
) -> list[dict]:
    bus_lookup = _pair_bus_lookup(pairs_wire)
    out: list[dict] = []
    for row in pipelines_wire:
        try:
            src_id, tgt_id = int(row.get("sourceSystem")), int(row.get("targetSystem"))
        except (TypeError, ValueError):
            continue
        src_machine = server_machine.get(src_id)
        tgt_machine = server_machine.get(tgt_id)
        if machine_id not in (src_machine, tgt_machine):
            continue
        entry = dict(row)
        if src_machine is not None and tgt_machine is not None and src_machine != tgt_machine:
            if machine_id == src_machine:
                direction, counterpart = "to", tgt_machine
            else:
                direction, counterpart = "from", src_machine
            entry["cross_machine"] = {
                "direction": direction,
                "counterpart_machine_id": counterpart,
                "service_bus_id": bus_lookup.get((src_id, tgt_id)),
            }
        else:
            entry["cross_machine"] = None
        out.append(entry)
    return out


def scope_pairs_for_machine(pairs_wire: list[dict], machine_id: int, server_machine: dict[int, int]) -> list[dict]:
    out: list[dict] = []
    for pair in pairs_wire:
        try:
            from_id, to_id = int(pair["from"]), int(pair["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if server_machine.get(from_id) == machine_id or server_machine.get(to_id) == machine_id:
            out.append(pair)
    return out


def scope_objects_for_machine(objects_wire: list[dict], hosted_ids: set[int], bus_ids: set[int]) -> list[dict]:
    hosted_str = {str(i) for i in hosted_ids}
    out: list[dict] = []
    for obj in objects_wire:
        oid = obj.get("id", "")
        parts = oid.split(":")
        if oid.startswith("db:") and len(parts) >= 3 and parts[2] in hosted_str:
            out.append(obj)
        elif oid.startswith("bus:") and len(parts) >= 2:
            try:
                bid = int(parts[1])
            except ValueError:
                continue
            if bid in bus_ids:
                out.append(obj)
    return out


def scope_servers_for_machine(servers_wire: list[dict], hosted_ids: set[int]) -> list[dict]:
    hosted_str = {str(i) for i in hosted_ids}
    return [s for s in servers_wire if s.get("id") in hosted_str]


def scope_buses_for_machine(buses_wire: list[dict], bus_ids: set[int]) -> list[dict]:
    bus_str = {str(b) for b in bus_ids}
    return [b for b in buses_wire if b.get("id") in bus_str]


def scope_topology_nodes_for_machine(nodes: list[dict], hosted_ids: set[int], bus_ids: set[int]) -> list[dict]:
    hosted_str = {str(i) for i in hosted_ids}
    bus_node_ids = {f"bus:{b}" for b in bus_ids}
    return [n for n in nodes if n.get("id") in hosted_str or n.get("id") in bus_node_ids]


def machine_summary(settings, machine_pipelines: list[dict], machine_pairs: list[dict], machine_objects: list[dict], machine_buses: list[dict], lost_by_pipeline: dict[int, int]) -> dict:
    any_db_object_down = any(o.get("kind") == "db_query" and o.get("status") != "ok" for o in machine_objects)
    any_service_bus_down = any(b.get("ok") is False for b in machine_buses)
    any_pipeline_degraded = any(p.get("health") in ("Degraded", "Down") for p in machine_pipelines)

    pipeline_queue_counts = {p["pipelineId"]: p.get("pendingOutbox") or 0 for p in machine_pipelines}
    pipeline_bus_id: dict[int, Optional[str]] = {}
    for pair in machine_pairs:
        for p in machine_pipelines:
            if str(p.get("sourceSystem")) == str(pair.get("from")) and str(p.get("targetSystem")) == str(pair.get("to")):
                pipeline_bus_id[p["pipelineId"]] = pair.get("bus")

    lost_pipeline_ids = {p["pipelineId"] for p in machine_pipelines if lost_by_pipeline.get(p["pipelineId"], 0) > 0}
    aggregates = compute_summary_aggregates(
        pipeline_queue_counts=pipeline_queue_counts,
        pipeline_bus_id=pipeline_bus_id,
        lost_pipeline_ids=lost_pipeline_ids,
        yellow_threshold=settings.pending_yellow_threshold,
        red_threshold=settings.pending_red_threshold,
        material_backlog_factor=settings.material_backlog_factor,
    )
    result = compute_summary(
        all_relays_stopped=False,
        any_service_bus_down=any_service_bus_down,
        any_db_object_down=any_db_object_down,
        any_relay_stopped=False,
        any_relay_unreadable=False,
        any_pipeline_degraded=any_pipeline_degraded,
        aggregates=aggregates,
    )
    return {
        "level": _wire_summary_level(result),
        "headline": result.note,
        "reason": result.note,
        "since": None,
        "lostTotal": aggregates.lost_summary["count"],
    }


def machine_active_alerts(app_state, config, machine_pipeline_ids: set[int]) -> list[dict]:
    incidents = getattr(app_state, "pipeline_alert_incidents", None)
    if not incidents:
        return []
    scoped = {alert_id: record for alert_id, record in incidents.items() if record.get("pipeline_id") in machine_pipeline_ids}
    queue_counts = {pid: c for pid, c in getattr(app_state, "last_pipeline_queue_counts", {}).items() if pid in machine_pipeline_ids}
    lost_counts = {pid: c for pid, c in getattr(app_state, "last_lost_by_pipeline", {}).items() if pid in machine_pipeline_ids}
    pipeline_lookup = {
        pid: row for pid, row in getattr(app_state, "last_pipeline_lookup", {}).items() if pid in machine_pipeline_ids
    }
    return apply_acks(app_state, pipeline_alerts_wire(scoped, config.alerts, queue_counts, lost_counts, pipeline_lookup))


def _hosted_platforms_wire(config, machine) -> list[dict]:
    servers_by_id = {s.id: s for s in config.servers}
    out: list[dict] = []
    for sid in sorted(hosted_server_ids(machine)):
        server = servers_by_id.get(sid)
        if server is None:
            continue
        out.append({"id": str(server.id), "label": server.name, "type": server.platform})
    return out


def _nodes_wire(machine, state) -> list[dict]:
    last_known_by_id = {
        entry.get("node_id"): entry for entry in state.last_known_services if isinstance(entry, dict)
    }
    out: list[dict] = []
    for node in machine.nodes:
        last = last_known_by_id.get(node.node_id)
        last_known_status = last.get("status") if last else None
        last_known_ts = last.get("last_probe_ts") if last else None
        out.append(
            {
                "node_id": node.node_id,
                "node_name": node.node_name,
                "node_type": node.node_type,
                "node_platform": node.node_platform,
                "server_id": node.server_id,
                "status": (last_known_status or "unknown") if state.status == "online" else state.status,
                "last_known_status": last_known_status or "unknown",
                "last_known_ts": last_known_ts,
            }
        )
    return out


def _scheduler_state(machine, health) -> str:
    if not machine.machine_scheduler:
        return "off"
    return health.wire_state(machine) if health is not None else "down"


def _scheduler_defaults(machine) -> Optional[dict]:
    defaults = machine.scheduler_defaults
    return defaults.model_dump(exclude={"enabled"}, exclude_none=True) if defaults is not None else None


def _scheduler_reason(machine, health) -> Optional[str]:
    if health is None or not machine.machine_scheduler or health.wire_state(machine) == "up":
        return None
    return health.reason(machine)


def machines_wire(config, registry, health=None) -> list[dict]:
    out: list[dict] = []
    for machine in config.machines:
        state = registry.state_for(machine.machine_id)
        ping = {"rtt_ms": state.ping_ms} if state.status == "online" and state.ping_ms is not None else None
        out.append(
            {
                "machine_id": machine.machine_id,
                "machine_label_id": machine.machine_label_id or str(machine.machine_id),
                "machine_name": machine.machine_name,
                "machine_type": machine.machine_type,
                "machine_os": machine.machine_os,
                "machine_ip": machine.machine_ip,
                "status": state.status,
                "last_probe_ts": state.last_probe_ts,
                "last_active_ts": state.last_active_ts,
                "ping": ping,
                "node_count": len(machine.nodes),
                "platforms": _hosted_platforms_wire(config, machine),
                "nodes": _nodes_wire(machine, state),
                "machine_scheduler": machine.machine_scheduler,
                "scheduler_state": _scheduler_state(machine, health),
                "scheduler_defaults": _scheduler_defaults(machine),
                "scheduler_reason": _scheduler_reason(machine, health),
            }
        )
    return out


def build_machine_sections(app_state, config, machine, estate_sections: dict) -> dict:
    hosted_ids = hosted_server_ids(machine)
    bus_ids = used_bus_ids(config, machine, hosted_ids)
    server_machine = server_machine_map(config)

    pipelines_wire = estate_sections.get("pipelines", [])
    pairs_wire = estate_sections.get("pairs", [])
    objects_wire = estate_sections.get("objects", [])
    buses_wire = estate_sections.get("buses", [])
    topology_nodes = estate_sections.get("topology", {}).get("nodes", [])

    machine_pipelines = scope_pipelines_for_machine(pipelines_wire, machine.machine_id, server_machine, pairs_wire)
    machine_pairs = scope_pairs_for_machine(pairs_wire, machine.machine_id, server_machine)
    machine_objects = scope_objects_for_machine(objects_wire, hosted_ids, bus_ids)
    machine_buses = scope_buses_for_machine(buses_wire, bus_ids)
    machine_topology_nodes = scope_topology_nodes_for_machine(topology_nodes, hosted_ids, bus_ids)

    pipeline_ids = {p["pipelineId"] for p in machine_pipelines}
    lost_by_pipeline = getattr(app_state, "last_lost_by_pipeline", {})

    settings = config.settings
    return {
        "emulator": {},
        "buses": machine_buses,
        "pipelines": machine_pipelines,
        "objects": machine_objects,
        "topology": {"nodes": machine_topology_nodes, "edges": [], "busEdges": []},
        "pairs": machine_pairs,
        "packets": [p for p in estate_sections.get("packets", []) if p["pipelineId"] in pipeline_ids],
        "activeAlerts": machine_active_alerts(app_state, config, pipeline_ids),
        "summary": machine_summary(config.settings, machine_pipelines, machine_pairs, machine_objects, machine_buses, lost_by_pipeline),
        "thresholds": {
            "yellow": settings.pending_yellow_threshold,
            "degraded": settings.backlog_max,
            "red": settings.pending_red_threshold,
        },
    }
