from __future__ import annotations

import re
from typing import Callable, Optional

from _alerts.shared_ack import apply_acks
from _scheduler.wire import scheduler_alerts_wire
from _ws.estate_payload import machine_alerts_wire, object_alert_info, pipeline_alerts_wire
from _ws.machine_scope import server_machine_map


def active_alerts(app_state, config) -> list[dict]:
    return apply_acks(app_state, _raw_alerts(app_state, config))


def _raw_alerts(app_state, config) -> list[dict]:
    alerts = machine_alerts_wire(app_state.machine_alert_incidents, config.alerts)
    alerts += pipeline_alerts_wire(
        app_state.pipeline_alert_incidents,
        config.alerts,
        getattr(app_state, "last_pipeline_queue_counts", {}),
        getattr(app_state, "last_lost_by_pipeline", {}),
        getattr(app_state, "last_pipeline_lookup", {}),
        object_alert_info(config),
    )
    alerts += scheduler_alerts_wire(app_state.schedule_incidents, app_state.scheduler_endpoint_incidents, config)
    return alerts


_OBJECT_SERVER = re.compile(r"^db:[^:]+:(\d+):")
PIPELINE_KINDS = frozenset({"pipeline", "bus", "lost", "relay_heartbeat"})

Scope = tuple[str, Optional[str]]


def server_scopes(config) -> dict[int, Scope]:
    out: dict[int, Scope] = {}
    for machine in config.machines:
        for node in machine.nodes:
            out.setdefault(node.server_id, (str(machine.machine_id), node.node_id))
    return out


def alert_scopes(
    alert: dict, config, pipelines: Optional[dict[str, dict]] = None, schedule_machine: Optional[Callable[[int], Optional[int]]] = None,
) -> list[Scope]:
    kind = alert.get("kind")
    if alert.get("machine_id") is not None:
        return [(str(alert["machine_id"]), None)]
    if kind == "schedule" and schedule_machine is not None:
        machine_id = schedule_machine(alert.get("schedule_id"))
        return [] if machine_id is None else [(str(machine_id), None)]
    by_server = server_scopes(config)
    if kind == "object":
        match = _OBJECT_SERVER.match(str((alert.get("affects") or [""])[0] or ""))
        scope = by_server.get(int(match.group(1))) if match else None
        return [] if scope is None else [scope]
    if kind in PIPELINE_KINDS and alert.get("affects"):
        pid = str(alert["affects"][0])
        row = (pipelines or {}).get(pid) or {}
        server_ids = [row.get("sourceSystem"), row.get("targetSystem")] if row else [pid.rpartition(":")[0]]
        scopes: list[Scope] = []
        for raw in server_ids:
            try:
                scope = by_server.get(int(raw))
            except (TypeError, ValueError):
                continue
            if scope is not None and scope not in scopes:
                scopes.append(scope)
        return scopes
    return []
