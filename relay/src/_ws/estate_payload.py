from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from _alerts.dedup import AlertKey, OpenIncidentIndex
from _alerts.degraded import evaluate_degraded
from _alerts.dispatch import channels_for_severity
from _alerts.engine import MachineAlertScope, PipelineSnapshot, evaluate_machine_alerts
from _alerts.severity import pipeline_severity
from _alerts.summary import SummaryAggregates, pipeline_text, compute_summary, compute_summary_aggregates, down_pending_level
from _db.factory import build_driver
from _http.live_attribution import refresh_attribution
from _lib.expected_errors import describe_driver_error
from _lib.transition_log import TransitionLogger
from _ws.pipeline_direction import normalize_pipeline_row
from _metrics.attribution import classify_heartbeat_staleness
from _metrics.bus_incident import LostAlertTracker, resolve_bus_incident
from _metrics.pair_scan import clamp_packet_count
from _metrics.pair_scan_runner import PairScanResult, PairScanRunner
from _metrics.readability import ReadStatus
from _alerts.shared_ack import apply_acks
from _scheduler.wire import scheduler_alerts_wire
from config_issues import compute_config_issues, is_foreign_row, excluded_row_keys, orphan_pipeline_issues

logger = logging.getLogger("relay.ws.estate")

_pipelines_transition_log = TransitionLogger()
_triggers_transition_log = TransitionLogger()

_RED_YELLOW_GREEN_TO_WIRE_LEVEL = {"red": "problem", "yellow": "attention", "green": "good"}
_GREY_BRANCH_TO_WIRE_LEVEL = {
    "all_relays_stopped": "offline",
    "service_bus_down": "outage",
}


def _wire_summary_level(result) -> str:
    if result.level == "grey":
        return _GREY_BRANCH_TO_WIRE_LEVEL.get(result.branch, "outage")
    return _RED_YELLOW_GREEN_TO_WIRE_LEVEL.get(result.level, "attention")


def _db_object_id(server) -> str:
    return f"db:{server.platform}:{server.id}:query"


def _bus_health_id(bus) -> str:
    return f"bus:{bus.id}:health"


def _bus_endpoint_id(bus) -> str:
    return f"bus:{bus.id}:endpoint"


def _server_wire(server) -> dict:
    return {
        "id": str(server.id),
        "label": server.name,
        "type": server.platform,
        "usedBuses": [str(b) for b in server.used_buses],
    }


_server_errors: dict[int, str] = {}


def _object_server_id(object_id) -> Optional[int]:
    parts = str(object_id).split(":")
    return int(parts[2]) if len(parts) > 3 and parts[0] == "db" and parts[2].isdigit() else None


def object_alert_info(config) -> dict[int, dict]:
    standalone = {n.server_id for n in config.services}
    return {
        s.id: {"name": s.name, "standalone": s.id in standalone, "error": _server_errors.get(s.id)}
        for s in config.servers
    }


async def _fetch_pipelines_for(server, settings) -> list[dict]:
    key = f"pipelines:{server.id}"
    label = f"server {server.id} ({server.platform})"
    try:
        driver = build_driver(server, settings)
        rows = await driver.fetch_pipelines_with_metrics()
    except Exception as exc:
        is_expected, message = describe_driver_error(exc, server.platform, label)
        _server_errors[server.id] = message
        if is_expected:
            _pipelines_transition_log.down(logger, key, f"{message} (pipelines unreadable this tick)")
        else:
            logger.error("estate_payload: fetch_pipelines_with_metrics failed unexpectedly for %s", label, exc_info=True)
        return []
    _server_errors.pop(server.id, None)
    _pipelines_transition_log.ok(logger, key, label)
    return rows


def _pipeline_display_label(row: dict, servers_by_id: dict) -> Optional[str]:
    try:
        src = servers_by_id.get(int(row.get("sourceSystem")))
        tgt = servers_by_id.get(int(row.get("targetSystem")))
    except (TypeError, ValueError):
        src = tgt = None
    if src is not None and tgt is not None:
        return f"{src.name} → {tgt.name}"
    return row.get("name") or None


def _pipeline_health(row: dict, db_ok: bool, backlog_max: int, heartbeat_class: str = "ok", never_pinged: bool = False) -> tuple[str, Optional[str]]:
    if not db_ok:
        return "Down", None
    if never_pinged:
        return "Down", None
    if heartbeat_class == "down":
        return "Down", None
    if heartbeat_class == "blocked_by_relay":
        return "Degraded", "heartbeat_stale"
    failed_outbox = row.get("failedOutbox") or 0
    unresolved_inbox = row.get("unresolvedInbox") or 0
    pending_outbox = row.get("pendingOutbox") or 0
    pending_inbox = row.get("pendingInbox") or 0
    if failed_outbox:
        return "Degraded", "failed_rows"
    if unresolved_inbox:
        return "Degraded", "unresolved_rows"
    if pending_outbox > backlog_max or pending_inbox > backlog_max:
        return "Degraded", "backlog"
    return "Healthy", None


def _pipeline_min_severity(pipeline_id, triggers: list[dict], default_severity: int) -> int:
    rows = [t for t in triggers if t.get("pipeline_id") == pipeline_id and t.get("severity") is not None]
    if not rows:
        return default_severity
    return min(int(t["severity"]) for t in rows)


async def _fetch_triggers_for(server, settings) -> list[dict]:
    key = f"triggers:{server.id}"
    label = f"server {server.id} ({server.platform})"
    try:
        driver = build_driver(server, settings)
        rows = await driver.fetch_triggers()
    except Exception as exc:
        is_expected, message = describe_driver_error(exc, server.platform, label)
        if is_expected:
            _triggers_transition_log.down(logger, key, f"{message} (triggers unreadable this tick)")
        else:
            logger.error("estate_payload: fetch_triggers failed unexpectedly for %s", label, exc_info=True)
        return []
    _triggers_transition_log.ok(logger, key, label)
    return rows


async def _build_pipelines_and_triggers(config, settings, attribution) -> tuple[list[dict], dict[int, list[dict]]]:
    pipelines_wire: list[dict] = []
    triggers_by_server: dict[int, list[dict]] = {}
    servers_by_id = {s.id: s for s in config.servers}
    pipeline_rows_per_server, triggers_per_server = await asyncio.gather(
        asyncio.gather(*(_fetch_pipelines_for(server, settings) for server in config.servers)),
        asyncio.gather(*(_fetch_triggers_for(server, settings) for server in config.servers)),
    )
    for server, rows, triggers in zip(config.servers, pipeline_rows_per_server, triggers_per_server):
        db_ok = attribution.emitted.get(_db_object_id(server)) == ReadStatus.OK
        triggers_by_server[server.id] = triggers
        for row in rows:
            row = normalize_pipeline_row(row, server.id)
            heartbeat_class = classify_heartbeat_staleness(
                row.get("heartbeatAgeSeconds"),
                settings.ping_seconds,
                down_multiplier=settings.ping_down_multiplier,
                blocked_multiplier=settings.ping_blocked_multiplier,
            )
            health, degraded_by = _pipeline_health(
                row, db_ok, settings.backlog_max, heartbeat_class,
                never_pinged=row.get("heartbeatAgeSeconds") is None,
            )
            pipelines_wire.append(
                {
                    "pipelineId": f"{server.id}:{row['pipelineId']}",
                    "pipelineNo": row["pipelineId"],
                    "name": row.get("name"),
                    "displayName": _pipeline_display_label(row, servers_by_id),
                    "sourceSystem": row.get("sourceSystem"),
                    "targetSystem": row.get("targetSystem"),
                    "direction": row.get("direction"),
                    "health": health,
                    "degradedBy": degraded_by,
                    "pendingOutbox": row.get("pendingOutbox") or 0,
                    "failedOutbox": row.get("failedOutbox") or 0,
                    "pendingInbox": row.get("pendingInbox") or 0,
                    "unresolvedInbox": row.get("unresolvedInbox") or 0,
                    "failedInbox": row.get("unresolvedInbox") or 0,
                    "sendLagSeconds": row.get("sendLagSeconds"),
                    "applyLagSeconds": row.get("applyLagSeconds"),
                    "heartbeatAgeSeconds": row.get("heartbeatAgeSeconds"),
                    "minSeverity": _pipeline_min_severity(
                        row["pipelineId"], triggers, config.alerts.object_severities.db
                    ),
                    "_hostServerId": server.id,
                    "_isEnabled": row.get("isEnabled"),
                    "_queueOrTopic": row.get("queueOrTopic"),
                }
            )
    return pipelines_wire, triggers_by_server


def _half_online(host_rows: list[dict], settings) -> bool:
    if not host_rows:
        return True
    for r in host_rows:
        age = r.get("heartbeatAgeSeconds")
        if age is None:
            continue
        if classify_heartbeat_staleness(
            age, settings.ping_seconds,
            down_multiplier=settings.ping_down_multiplier,
            blocked_multiplier=settings.ping_blocked_multiplier,
        ) != "down":
            return True
    return False


def _strip_internal(pipelines_wire: list[dict]) -> list[dict]:
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in pipelines_wire]


def _shared_bus(server_a, server_b) -> Optional[int]:
    shared = set(server_a.used_buses) & set(server_b.used_buses)
    return min(shared) if shared else None


async def _build_pairs(config, settings, pipelines_wire: list[dict], attribution, pair_scan: "PairScanResult") -> list[dict]:
    servers_by_id = {s.id: s for s in config.servers}
    pair_keys: dict[tuple[int, int], list[dict]] = {}
    for row in pipelines_wire:
        src, tgt = row.get("sourceSystem"), row.get("targetSystem")
        try:
            src_id, tgt_id = int(src), int(tgt)
        except (TypeError, ValueError):
            continue
        if src_id not in servers_by_id or tgt_id not in servers_by_id or src_id == tgt_id:
            continue
        pair_keys.setdefault((src_id, tgt_id), []).append(row)

    pairs_wire: list[dict] = []
    for (from_id, to_id), rows in sorted(pair_keys.items()):
        from_server, to_server = servers_by_id[from_id], servers_by_id[to_id]
        from_rows = [r for r in rows if r.get("_hostServerId") == from_id]
        has_sender_half = bool(from_rows)
        has_receiver_half = any(r.get("_hostServerId") == to_id for r in rows)
        if has_sender_half and not has_receiver_half:
            missing_end, missing_reason = "receiver", "unconfigured"
        elif has_receiver_half and not has_sender_half:
            missing_end, missing_reason = "sender", "unconfigured"
        else:
            missing_end = missing_reason = None
        pending_outbox = sum(r.get("pendingOutbox") or 0 for r in from_rows)
        failed_outbox = sum(r.get("failedOutbox") or 0 for r in from_rows)
        failed_inbox = sum(r.get("unresolvedInbox") or 0 for r in from_rows)

        pair_key = (from_id, to_id)
        if pair_scan.scan_ok_by_server.get(from_id, False):
            queued = pair_scan.queued_by_pair.get(pair_key, 0)
            queued_unknown = False
        else:
            queued = None
            queued_unknown = True
        lost = pair_scan.lost_by_pair.get(pair_key, 0)
        packets = clamp_packet_count(pair_scan.packets_by_pair.get(pair_key, 0))
        if os.environ.get("RELAY_PACKETS_TRACE") == "1":
            logger.info(
                "PKTTRACE wire pair=%s->%s raw=%s wire=%s scan_ok=%s", from_id, to_id,
                pair_scan.packets_by_pair.get(pair_key, 0), packets, pair_scan.scan_ok_by_server.get(from_id),
            )

        sender_ok = attribution.emitted.get(_db_object_id(from_server)) == ReadStatus.OK and _half_online(
            [r for r in rows if r.get("_hostServerId") == from_id], settings
        )
        receiver_ok = attribution.emitted.get(_db_object_id(to_server)) == ReadStatus.OK and _half_online(
            [r for r in rows if r.get("_hostServerId") == to_id], settings
        )
        bus_id = _shared_bus(from_server, to_server)
        bus = next((b for b in config.service_busses if b.id == bus_id), None) if bus_id is not None else None
        bus_ok = attribution.emitted.get(_bus_endpoint_id(bus)) == ReadStatus.OK if bus else None

        pairs_wire.append(
            {
                "id": f"{from_id}->{to_id}",
                "from": str(from_id),
                "to": str(to_id),
                "pendingOutbox": pending_outbox,
                "queued": None if queued_unknown else queued,
                "queuedUnknown": queued_unknown or None,
                "lost": lost,
                "packets": packets,
                "failedOutbox": failed_outbox,
                "failedInbox": failed_inbox,
                "senderOk": sender_ok,
                "receiverOk": receiver_ok,
                "bus": str(bus_id) if bus_id is not None else None,
                "busName": bus.name if bus else None,
                "missingEnd": missing_end,
                "missingEndReason": missing_reason,
                "status": "grey" if (not sender_ok or not receiver_ok or bus_ok is False) else (
                    "yellow" if pending_outbox >= settings.pending_yellow_threshold else "green"
                ),
            }
        )
    return pairs_wire


def _build_buses(config, attribution) -> list[dict]:
    out: list[dict] = []
    for bus in config.service_busses:
        endpoint_id = _bus_endpoint_id(bus)
        emitted = attribution.emitted.get(endpoint_id, ReadStatus.OK)
        out.append(
            {
                "id": str(bus.id),
                "name": bus.name,
                "label": bus.name,
                "ok": emitted == ReadStatus.OK,
                "blockedBy": attribution.blocked_by.get(endpoint_id),
                "topics": [bus.topic],
                "usedBy": [str(s.id) for s in config.servers if bus.id in s.used_buses],
            }
        )
    return out


def _standalone_services_wire(config, attribution) -> list[dict]:
    out: list[dict] = []
    for node in config.services:
        if node.node_type == "Database":
            status = attribution.emitted.get(_db_object_id_for(node.node_platform, node.server_id))
            status_value = status.value if status else "unknown"
        else:
            status_value = "unknown"
        out.append(
            {
                "node_id": node.node_id,
                "node_name": node.node_name,
                "node_type": node.node_type,
                "node_platform": node.node_platform,
                "server_id": node.server_id,
                "status": status_value,
            }
        )
    return out


def service_flow(config, pipelines_wire: list[dict], pairs_wire: list[dict]) -> tuple[list[dict], list[dict]]:
    service_ids = {str(n.server_id) for n in config.services}
    if not service_ids:
        return [], []
    pipelines = [
        {**p, "cross_machine": None}
        for p in pipelines_wire
        if str(p.get("sourceSystem")) in service_ids or str(p.get("targetSystem")) in service_ids
    ]
    pairs = [p for p in pairs_wire if str(p.get("from")) in service_ids or str(p.get("to")) in service_ids]
    return pairs, pipelines


def _db_object_id_for(platform: str, server_id: int) -> str:
    return f"db:{platform}:{server_id}:query"


def _build_topology_nodes(config, attribution, pipelines_wire: list[dict]) -> list[dict]:
    nodes: list[dict] = []
    for server in config.servers:
        db_ok = attribution.emitted.get(_db_object_id(server)) == ReadStatus.OK
        own_out = [p for p in pipelines_wire if str(p.get("sourceSystem")) == str(server.id)]
        own_in = [p for p in pipelines_wire if str(p.get("targetSystem")) == str(server.id)]
        pipeline_ids = sorted({p["pipelineId"] for p in own_out + own_in})
        nodes.append(
            {
                "id": str(server.id),
                "kind": "platform",
                "label": server.name,
                "type": server.platform,
                "dbOk": db_ok,
                "senderOk": db_ok,
                "receiverOk": db_ok,
                "senderPendingCount": sum(p.get("pendingOutbox") or 0 for p in own_out) if db_ok else None,
                "receiverPendingCount": sum((p.get("pendingInbox") or 0) + (p.get("unresolvedInbox") or 0) for p in own_in) if db_ok else None,
                "issues": [],
                "pipelines": pipeline_ids,
                "heartbeatReadable": db_ok,
            }
        )
    for bus in config.service_busses:
        health_id, endpoint_id = _bus_health_id(bus), _bus_endpoint_id(bus)
        ok = attribution.emitted.get(endpoint_id) == ReadStatus.OK and attribution.emitted.get(health_id) == ReadStatus.OK
        nodes.append({"id": f"bus:{bus.id}", "kind": "service_bus", "label": bus.name, "dbOk": ok})
    return nodes


def _objects_wire(config, attribution) -> list[dict]:
    out: list[dict] = []
    for server in config.servers:
        oid = _db_object_id(server)
        status = attribution.emitted.get(oid)
        out.append(
            {
                "id": oid,
                "kind": "db_query",
                "status": status.value if status else "unknown",
                "label": server.name,
                "detail": attribution.blocked_by.get(oid),
            }
        )
    for bus in config.service_busses:
        for oid, label, kind in (
            (_bus_health_id(bus), f"{bus.name} health", "bus_health"),
            (_bus_endpoint_id(bus), f"{bus.name} endpoint", "bus_endpoint"),
        ):
            status = attribution.emitted.get(oid)
            out.append(
                {
                    "id": oid,
                    "kind": kind,
                    "status": status.value if status else "unknown",
                    "label": label,
                    "detail": attribution.blocked_by.get(oid),
                }
            )
    return out


def _build_summary(config, settings, attribution, pipelines_wire: list[dict], pairs_wire: list[dict], pair_scan: "PairScanResult"):
    any_db_object_down = any(
        attribution.emitted.get(_db_object_id(s)) != ReadStatus.OK for s in config.servers
    )
    any_service_bus_down = any(
        attribution.emitted.get(_bus_endpoint_id(b)) != ReadStatus.OK
        or attribution.emitted.get(_bus_health_id(b)) != ReadStatus.OK
        for b in config.service_busses
    )
    any_pipeline_degraded = any(p["health"] in ("Degraded", "Down") for p in pipelines_wire)

    pipeline_queue_counts = {p["pipelineId"]: p.get("pendingOutbox") or 0 for p in pipelines_wire}
    pipeline_bus_id: dict[int, Optional[int]] = {}
    for pair in pairs_wire:
        for p in pipelines_wire:
            if str(p.get("sourceSystem")) == str(pair["from"]) and str(p.get("targetSystem")) == str(pair["to"]):
                pipeline_bus_id[p["pipelineId"]] = pair.get("bus")

    lost_pipeline_ids = {pid for pid, count in pair_scan.lost_by_pipeline.items() if count > 0}
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


def _build_alert_scope(config, settings, attribution, pipelines_wire: list[dict], pair_scan: "PairScanResult") -> "MachineAlertScope":
    pipelines: dict[int, "PipelineSnapshot"] = {}
    for row in pipelines_wire:
        pid = row["pipelineId"]
        queued_count = row.get("pendingOutbox") or 0
        lost_count = pair_scan.lost_by_pipeline.get(pid, 0)
        queued_countable = pair_scan.queued_countable_by_pipeline.get(pid, 0) > 0
        matched_recently = pair_scan.matched_recently_by_pipeline.get(pid, False)
        min_severity = row.get("minSeverity")
        heartbeat_class = classify_heartbeat_staleness(
            row.get("heartbeatAgeSeconds"),
            settings.ping_seconds,
            down_multiplier=settings.ping_down_multiplier,
            blocked_multiplier=settings.ping_blocked_multiplier,
        )
        degraded_result = evaluate_degraded(
            queued_count=queued_count,
            backlog_max=settings.backlog_max,
            red_on_the_map=False,
            lost_count=lost_count,
            matched_recently=matched_recently,
            queued_countable=queued_countable,
            send_oldest_pending_seconds=row.get("sendLagSeconds"),
            send_avg_lag_seconds=None,
            apply_oldest_pending_seconds=row.get("applyLagSeconds"),
            apply_avg_lag_seconds=None,
            lag_max=settings.lag_max,
        )
        pipelines[pid] = PipelineSnapshot(
            pipeline_id=pid,
            row_severities=[int(min_severity)] if min_severity is not None else [],
            degraded=degraded_result.degraded,
            degraded_reasons=degraded_result.reasons,
            heartbeat_class=heartbeat_class,
            bus_attribution=None,
            lost_count=lost_count,
        )

    emitted: dict[str, ReadStatus] = dict(attribution.emitted)
    object_kind_of: dict[str, str] = {}
    for server in config.servers:
        object_kind_of[_db_object_id(server)] = "db"
    for bus in config.service_busses:
        object_kind_of[_bus_health_id(bus)] = "bus"
        object_kind_of[_bus_endpoint_id(bus)] = "bus"

    return MachineAlertScope(machine_id=0, pipelines=pipelines, emitted=emitted, object_kind_of=object_kind_of)


def _attach_bus_attribution(
    config, settings, attribution, pipelines_wire: list[dict], scope: "MachineAlertScope",
    previous_attribution: dict[int, object],
) -> None:
    servers_by_id = {s.id: s for s in config.servers}
    for row in pipelines_wire:
        pid = row["pipelineId"]
        snap = scope.pipelines.get(pid)
        if snap is None:
            continue
        try:
            src_id, tgt_id = int(row.get("sourceSystem")), int(row.get("targetSystem"))
        except (TypeError, ValueError):
            continue
        source_server, target_server = servers_by_id.get(src_id), servers_by_id.get(tgt_id)
        if source_server is None or target_server is None:
            continue
        bus_id = _shared_bus(source_server, target_server)
        bus = next((b for b in config.service_busses if b.id == bus_id), None) if bus_id is not None else None
        if bus is None:
            continue
        target_db_object_id = _db_object_id(target_server)
        attribution_result = resolve_bus_incident(
            bus_health_status=attribution.emitted.get(_bus_health_id(bus), ReadStatus.OK),
            bus_endpoint_status=attribution.emitted.get(_bus_endpoint_id(bus), ReadStatus.OK),
            bus_health_object_id=_bus_health_id(bus),
            bus_endpoint_object_id=_bus_endpoint_id(bus),
            target_db_status=attribution.emitted.get(target_db_object_id, ReadStatus.OK),
            target_db_object_id=target_db_object_id,
            target_heartbeat_class=snap.heartbeat_class,
            target_read_status=attribution.emitted.get(target_db_object_id, ReadStatus.OK),
            source_read_status=attribution.emitted.get(_db_object_id(source_server), ReadStatus.OK),
            queued_data_available=True,
            last_known=previous_attribution.get(pid),
        )
        previous_attribution[pid] = attribution_result
        snap.bus_attribution = attribution_result


def _format_duration_seconds(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "unknown"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


def pipeline_alerts_wire(
    incidents: "OpenIncidentIndex",
    alerts_settings,
    pipeline_queue_counts: dict[int, int],
    lost_by_pipeline: dict[int, int],
    pipeline_lookup: Optional[dict[int, dict]] = None,
    object_info: Optional[dict[int, dict]] = None,
) -> list[dict]:
    pipeline_lookup = pipeline_lookup or {}
    object_info = object_info or {}

    def _pipeline_label(pid) -> str:
        row = pipeline_lookup.get(pid) if pid is not None else None
        if row:
            name = row.get("displayName") or row.get("name")
            if name:
                return name
        return f"pipeline {pipeline_text(pid)}" if pid is not None else "This pipeline"

    out: list[dict] = []
    for alert_id, record in incidents.items():
        kind = alert_id.split(":", 1)[0]
        severity = record.get("severity", 3)
        pipeline_id = record.get("pipeline_id")
        label = _pipeline_label(pipeline_id)
        if kind == "object":
            affects = [record.get("object_id")]
            reason = f"{record.get('object_id')} ({record.get('kind')}) is not ok"
            server_id = _object_server_id(record.get("object_id"))
            info = object_info.get(server_id) if server_id is not None else None
        elif kind == "pipeline":
            affects = [str(pipeline_id)]
            reason = f"{label} is degraded"
        elif kind == "bus":
            affects = [str(pipeline_id)]
            reason = f"{label} blocked by {record.get('cause')}"
        elif kind == "lost":
            affects = [str(pipeline_id)]
            reason = f"{label} has lost messages"
        elif kind == "relay_heartbeat":
            affects = [str(pipeline_id)]
            reason = f"{label}: heartbeat hasn't been seen recently"
        else:
            affects = []
            reason = alert_id
        entry = {
            "alertId": alert_id,
            "kind": kind,
            "rootCause": kind,
            "reason": reason,
            "severity": severity,
            "affects": affects,
            "channels": channels_for_severity(alerts_settings, severity, escalated=False),
            "acknowledged": False,
        }
        if kind == "object" and info is not None:
            error = info.get("error")
            reason = f"{info['name']} (server {server_id}): {error}" if error else f"{info['name']} (server {server_id}) is not ok"
            entry["reason"] = reason
            entry["server_id"] = server_id
            if error:
                entry["detail"] = error
            if info["standalone"]:
                entry["machine_id"] = None
        if kind == "relay_heartbeat":
            age = pipeline_lookup.get(pipeline_id, {}).get("heartbeatAgeSeconds") if pipeline_id is not None else None
            if age is not None:
                entry["detail"] = f"Heartbeat age: {_format_duration_seconds(age)}"
        elif pipeline_id is not None:
            queued_count = pipeline_queue_counts.get(pipeline_id, 0)
            lost_count = lost_by_pipeline.get(pipeline_id, 0)
            if queued_count:
                entry["queuedCount"] = queued_count
            if lost_count:
                entry["lostCount"] = lost_count
        out.append(entry)
    return out


def machine_alerts_wire(incidents, alerts_settings) -> list[dict]:
    return [
        {
            "alertId": alert_id,
            "kind": "machine",
            "machine_id": record["machine_id"],
            "rootCause": "machine",
            "reason": f"machine {record['machine_id']} is offline",
            "severity": record["severity"],
            "affects": [],
            "channels": channels_for_severity(alerts_settings, record["severity"], escalated=False),
            "acknowledged": False,
        }
        for alert_id, record in incidents.items()
    ]


def static_snapshot_sections(config) -> dict:
    settings = config.settings
    servers_wire = [_server_wire(s) for s in config.servers]
    topology_nodes = [
        {"id": str(s.id), "kind": "platform", "label": s.name, "type": s.platform, "pipelines": []}
        for s in config.servers
    ] + [{"id": f"bus:{b.id}", "kind": "service_bus", "label": b.name} for b in config.service_busses]
    return {
        "servers": servers_wire,
        "platforms": servers_wire,
        "pipelines": [],
        "objects": [],
        "topology": {"nodes": topology_nodes, "edges": [], "busEdges": []},
        "pairs": [],
        "buses": [{"id": str(b.id), "name": b.name, "label": b.name, "ok": None, "blockedBy": None, "topics": [b.topic], "usedBy": [str(s.id) for s in config.servers if b.id in s.used_buses]} for b in config.service_busses],
        "services": [
            {"node_id": n.node_id, "node_name": n.node_name, "node_type": n.node_type, "node_platform": n.node_platform, "server_id": n.server_id, "status": "unknown"}
            for n in config.services
        ],
        "activeAlerts": [],
        "summary": {"level": "attention", "headline": "Starting up", "reason": "Waiting for the first poll cycle.", "since": None},
        "configIssues": [issue.model_dump(exclude_none=True) for issue in compute_config_issues(config)],
        "thresholds": {
            "yellow": settings.pending_yellow_threshold,
            "degraded": settings.backlog_max,
            "red": settings.pending_red_threshold,
        },
    }


async def build_estate_sections(app_state) -> dict:
    config_store = app_state.config_store
    config = config_store.get()
    settings = config.settings

    attribution = await refresh_attribution(app_state)
    pipelines_wire, _triggers = await _build_pipelines_and_triggers(config, settings, attribution)
    pipeline_issues = orphan_pipeline_issues(config, pipelines_wire)
    pipelines_wire = [p for p in pipelines_wire if not is_foreign_row(p)]

    pair_scan_runner = getattr(app_state, "pair_scan_runner", None)
    if pair_scan_runner is None:
        pair_scan_runner = PairScanRunner()
        app_state.pair_scan_runner = pair_scan_runner
    pair_scan = await pair_scan_runner.tick(config, settings, app_state.down_confirm, pipelines_wire)

    pairs_wire = await _build_pairs(config, settings, pipelines_wire, attribution, pair_scan)
    servers_wire = [_server_wire(s) for s in config.servers]

    if not hasattr(app_state, "pipeline_alert_incidents"):
        app_state.pipeline_alert_incidents = OpenIncidentIndex()
    if not hasattr(app_state, "alert_previous_emitted"):
        app_state.alert_previous_emitted = {}
    if not hasattr(app_state, "alert_previous_degraded"):
        app_state.alert_previous_degraded = {}
    if not hasattr(app_state, "alert_previous_bus_attribution"):
        app_state.alert_previous_bus_attribution = {}
    if not hasattr(app_state, "lost_alert_tracker"):
        app_state.lost_alert_tracker = LostAlertTracker.from_settings(settings)

    orphans = excluded_row_keys(config, pipelines_wire)
    app_state.orphan_pipeline_issues = pipeline_issues
    scope = _build_alert_scope(
        config, settings, attribution, [p for p in pipelines_wire if (p["_hostServerId"], p["pipelineId"]) not in orphans], pair_scan
    )
    scope.previous_emitted = app_state.alert_previous_emitted
    _attach_bus_attribution(config, settings, attribution, pipelines_wire, scope, app_state.alert_previous_bus_attribution)

    lost_ticks = {
        pid: app_state.lost_alert_tracker.observe(str(pid), snap.lost_count, pipeline_severity(list(snap.row_severities)))
        for pid, snap in scope.pipelines.items()
    }
    row_data: dict[int, list[dict]] = {}

    evaluate_machine_alerts(
        scope,
        app_state.pipeline_alert_incidents,
        object_severities=config.alerts.object_severities,
        previous_degraded=app_state.alert_previous_degraded,
        lost_ticks=lost_ticks,
        row_data=row_data,
    )
    app_state.alert_previous_emitted = dict(attribution.emitted)
    app_state.alert_previous_degraded = {pid: snap.degraded for pid, snap in scope.pipelines.items()}

    pipeline_queue_counts = {p["pipelineId"]: p.get("pendingOutbox") or 0 for p in pipelines_wire}
    app_state.last_pipeline_queue_counts = pipeline_queue_counts
    app_state.last_lost_by_pipeline = dict(pair_scan.lost_by_pipeline)
    pipeline_lookup = {p["pipelineId"]: p for p in pipelines_wire}
    app_state.last_pipeline_lookup = pipeline_lookup
    active_alerts = machine_alerts_wire(app_state.machine_alert_incidents, config.alerts) + pipeline_alerts_wire(
        app_state.pipeline_alert_incidents, config.alerts, pipeline_queue_counts, pair_scan.lost_by_pipeline, pipeline_lookup,
        object_alert_info(config),
    )
    schedule_store = getattr(app_state, "schedule_store", None)
    if schedule_store is not None:
        active_alerts += scheduler_alerts_wire(app_state.schedule_incidents, app_state.scheduler_endpoint_incidents, config)
    apply_acks(app_state, active_alerts)

    pipelines_out = _strip_internal(pipelines_wire)
    service_pairs, service_pipelines = service_flow(config, pipelines_out, pairs_wire)

    return {
        "servers": servers_wire,
        "platforms": servers_wire,
        "pipelines": pipelines_out,
        "servicePairs": service_pairs,
        "servicePipelines": service_pipelines,
        "objects": _objects_wire(config, attribution),
        "topology": {"nodes": _build_topology_nodes(config, attribution, pipelines_wire), "edges": [], "busEdges": []},
        "pairs": pairs_wire,
        "packets": [
            {"pipelineId": pid, "count": clamp_packet_count(n)}
            for pid, n in sorted(pair_scan.packets_by_pipeline.items()) if n > 0
        ],
        "buses": _build_buses(config, attribution),
        "services": _standalone_services_wire(config, attribution),
        "activeAlerts": active_alerts,
        "summary": _build_summary(
            config, settings, attribution, [p for p in pipelines_wire if (p["_hostServerId"], p["pipelineId"]) not in orphans], pairs_wire, pair_scan
        ),
        "configIssues": [
            issue.model_dump(exclude_none=True)
            for issue in compute_config_issues(
                config, schedule_store.list() if schedule_store is not None else None, getattr(app_state, "state_dir", None)
            )
            + app_state.orphan_pipeline_issues
        ],
        "thresholds": {
            "yellow": settings.pending_yellow_threshold,
            "degraded": settings.backlog_max,
            "red": settings.pending_red_threshold,
        },
    }
