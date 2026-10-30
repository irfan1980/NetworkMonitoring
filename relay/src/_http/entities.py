from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from _db.factory import build_driver
from _http.live_attribution import refresh_attribution
from _lib.expected_errors import describe_driver_error
from _metrics.readability import ReadStatus
from _ws.pipeline_direction import normalize_pipeline_row
from _ws.window import InvalidWindowParam, validate_window_param

router = APIRouter()
logger = logging.getLogger("relay.http.entities")

EMPTY_SIDE = {"pipelines": [], "agg": [], "sent": [], "applied": [], "triggers": []}
OPERATION_ORDER = {"Insert": 0, "Update": 1, "Delete": 2}


def _find_server(config, server_id: int):
    for s in config.servers:
        if s.id == server_id:
            return s
    return None


def _resolve_window(raw: Optional[str], default_seconds: int) -> float:
    if raw is None:
        return float(default_seconds)
    try:
        return validate_window_param(raw)
    except InvalidWindowParam as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _representative_trigger(triggers: list[dict], pipeline_id: int) -> Optional[dict]:
    candidates = [t for t in triggers if t["pipeline_id"] == pipeline_id]
    if not candidates:
        return None
    for t in candidates:
        if t["operation"] == "Insert":
            return t
    return candidates[0]


async def _load_host(driver, host_id: int, window_seconds: float) -> dict:
    pipelines = [normalize_pipeline_row(p, host_id) for p in await driver.fetch_pipelines_with_metrics()]
    return {
        "pipelines": pipelines,
        "triggers": await driver.fetch_triggers(),
        "sent": await driver.fetch_outbox_sent(int(window_seconds)),
        "applied": [r for r in await driver.fetch_inbox_since(int(window_seconds)) if r["status"] == "Applied"],
    }


async def _slice(driver, host: dict, pipelines: list[dict], outgoing: bool) -> dict:
    ids = tuple(p["pipelineId"] for p in pipelines)
    if outgoing:
        agg = await driver.fetch_outbox_entity_agg(ids) if ids else []
    else:
        agg = await driver.fetch_inbox_entity_agg(ids) if ids else []
    return {
        "pipelines": pipelines,
        "agg": agg,
        "sent": [r for r in host["sent"] if r["pipeline_id"] in ids] if outgoing else [],
        "applied": [] if outgoing else [r for r in host["applied"] if r["pipeline_id"] in ids],
        "triggers": host["triggers"],
    }


async def _read_pair_side(driver, server, from_id: int, to_id: int, window_seconds: float, outgoing: bool) -> tuple[dict, dict]:
    try:
        host = await _load_host(driver, from_id if outgoing else to_id, window_seconds)
        unwanted = "incoming" if outgoing else "outgoing"
        pipelines = [
            p for p in host["pipelines"]
            if str(p["sourceSystem"]) == str(from_id) and str(p["targetSystem"]) == str(to_id) and p.get("direction") != unwanted
        ]
        return await _slice(driver, host, pipelines, outgoing), {"available": True}
    except Exception as exc:
        is_expected, message = describe_driver_error(exc, server.platform, f"server {server.id} ({server.platform})")
        if not is_expected:
            raise
        logger.warning("pair entities: %s", message)
        return EMPTY_SIDE, {"available": False, "reason": "db_unreachable", "detail": message}


def _operations(triggers: list[dict], pipeline_ids: set) -> list[str]:
    seen: list[str] = []
    for t in triggers:
        op = t.get("operation")
        if t["pipeline_id"] in pipeline_ids and op and op not in seen:
            seen.append(op)
    return sorted(seen, key=lambda o: OPERATION_ORDER.get(o, 9))


def _latest(rows: list[dict], pipeline_ids: set, column: str) -> Optional[str]:
    values = [r[column] for r in rows if r["pipeline_id"] in pipeline_ids and r.get(column)]
    return max(values) if values else None


def _count_by_entity(rows: list[dict], pipelines: list[dict]) -> dict[str, int]:
    entity_of = {p["pipelineId"]: p["entityType"] for p in pipelines}
    out: dict[str, int] = {}
    for r in rows:
        et = entity_of.get(r["pipeline_id"])
        if et:
            out[et] = out.get(et, 0) + 1
    return out


def _entity_rows(side_from: dict, side_to: dict) -> list[dict]:
    pf, pt = side_from["pipelines"], side_to["pipelines"]
    outbox = {r["entity_type"]: r for r in side_from["agg"]}
    inbox = {r["entity_type"]: r for r in side_to["agg"]}
    sent_n = _count_by_entity(side_from["sent"], pf)
    applied_n = _count_by_entity(side_to["applied"], pt)

    rows: list[dict] = []
    for et in sorted({p["entityType"] for p in pf} | {p["entityType"] for p in pt}):
        ids_from = {p["pipelineId"] for p in pf if p["entityType"] == et}
        ids_to = {p["pipelineId"] for p in pt if p["entityType"] == et}
        pipe_from = next((p for p in pf if p["entityType"] == et), None)
        pipe_to = next((p for p in pt if p["entityType"] == et), None)
        trig_from = _representative_trigger(side_from["triggers"], pipe_from["pipelineId"]) if pipe_from else None
        trig_to = _representative_trigger(side_to["triggers"], pipe_to["pipelineId"]) if pipe_to else None
        ob, ib = outbox.get(et, {}), inbox.get(et, {})
        err = None
        if ib.get("lastError") is not None:
            err = {"at": ib.get("lastErrorAt"), "message": str(ib["lastError"]), "messageId": ib.get("lastErrorMessageId")}
        rows.append({
            "entity": et,
            "pipelineId": str((pipe_from or pipe_to)["pipelineId"]),
            "senderFunction": trig_from["function"] if trig_from else None,
            "senderFormat": trig_from.get("format") if trig_from else None,
            "receiverProcedure": trig_to["function"] if trig_to else None,
            "receiverFormat": trig_to.get("format") if trig_to else None,
            "operations": _operations(side_from["triggers"], ids_from) or _operations(side_to["triggers"], ids_to),
            "severity": (trig_from or trig_to or {}).get("severity"),
            "pending": int(ob.get("pending") or 0),
            "sentInWindow": sent_n.get(et, 0),
            "appliedInWindow": applied_n.get(et, 0),
            "errors": int(ib.get("unresolved") or 0),
            "lastSentAt": _latest(side_from["sent"], ids_from, "sent_at"),
            "lastAppliedAt": _latest(side_to["applied"], ids_to, "applied_at"),
            "lastError": err,
            "queued": int(ib.get("received") or 0),
        })
    return rows


async def _platform_entities(driver, server_id: int, window_seconds: float) -> dict:
    host = await _load_host(driver, server_id, window_seconds)
    me = str(server_id)
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for p in host["pipelines"]:
        src, tgt = str(p["sourceSystem"]), str(p["targetSystem"])
        if src == me and tgt != me and p.get("direction") != "incoming":
            outgoing.setdefault(tgt, []).append(p)
        elif tgt == me and src != me and p.get("direction") != "outgoing":
            incoming.setdefault(src, []).append(p)
    out_groups, in_groups = [], []
    for other, pipes in sorted(outgoing.items()):
        side = await _slice(driver, host, pipes, True)
        out_groups.append({"to": other, "entities": _entity_rows(side, EMPTY_SIDE)})
    for other, pipes in sorted(incoming.items()):
        side = await _slice(driver, host, pipes, False)
        in_groups.append({"from": other, "entities": _entity_rows(EMPTY_SIDE, side)})
    return {"platform": me, "outgoing": out_groups, "incoming": in_groups}


@router.get("/api/pairs/{from_id}/{to_id}/entities")
async def pair_entities(from_id: int, to_id: int, request: Request, window: Optional[str] = None) -> dict:
    config_store = request.app.state.config_store
    config = config_store.get()

    from_server = _find_server(config, from_id)
    to_server = _find_server(config, to_id)
    if from_server is None:
        raise HTTPException(status_code=404, detail=f"unknown server id {from_id}")
    if to_server is None:
        raise HTTPException(status_code=404, detail=f"unknown server id {to_id}")

    window_seconds = _resolve_window(window, config.settings.e2e_window_seconds)

    driver_from = build_driver(from_server, config.settings)
    driver_to = build_driver(to_server, config.settings)
    try:
        (data_from, state_from), (data_to, state_to) = await asyncio.gather(
            _read_pair_side(driver_from, from_server, from_id, to_id, window_seconds, True),
            _read_pair_side(driver_to, to_server, from_id, to_id, window_seconds, False),
        )
    except Exception:
        logger.error("pair entities failed unexpectedly", exc_info=True)
        raise HTTPException(status_code=500, detail="internal error")

    bus = await _bus_state(request, config, from_server, to_server)
    if not state_from["available"] and not state_to["available"]:
        detail = {"error": "nothing_readable", "from": state_from, "to": state_to, "bus": bus}
        raise HTTPException(status_code=503, detail=detail)

    return {
        "from": str(from_id),
        "to": str(to_id),
        "window": {"seconds": int(window_seconds)},
        "entities": _entity_rows(data_from, data_to),
        "sides": {"from": state_from, "to": state_to},
        "bus": bus,
    }


async def _bus_state(request: Request, config, from_server, to_server) -> dict:
    shared = set(from_server.used_buses) & set(to_server.used_buses)
    ids = shared or (set(from_server.used_buses) | set(to_server.used_buses))
    try:
        result = await refresh_attribution(request.app.state)
        down = [
            {"id": b.id, "name": b.name, "blockedBy": result.blocked_by.get(f"bus:{b.id}:endpoint")}
            for b in config.service_busses
            if b.id in ids and result.emitted.get(f"bus:{b.id}:endpoint", ReadStatus.OK) != ReadStatus.OK
        ]
    except Exception:
        logger.warning("pair entities: bus status unavailable", exc_info=True)
        return {"available": False, "reason": "bus_status_unknown"}
    if down:
        return {"available": False, "reason": "bus_down", "buses": down}
    return {"available": True}


async def _server_entities(server_id: int, request: Request, window: Optional[str] = None) -> dict:
    config_store = request.app.state.config_store
    config = config_store.get()
    server = _find_server(config, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail=f"unknown server id {server_id}")
    window_seconds = _resolve_window(window, config.settings.e2e_window_seconds)
    driver = build_driver(server, config.settings)
    try:
        return await _platform_entities(driver, server_id, window_seconds)
    except Exception as exc:
        raise _entities_http_error(exc, [server], f"server {server_id} entities")


def _entities_http_error(exc: Exception, servers: list, what: str) -> HTTPException:
    for server in servers:
        is_expected, message = describe_driver_error(exc, server.platform, f"server {server.id} ({server.platform})")
        if is_expected:
            logger.warning("%s unavailable: %s", what, message)
            return HTTPException(status_code=503, detail=message)
    logger.error("%s failed unexpectedly", what, exc_info=True)
    return HTTPException(status_code=500, detail="internal error")


@router.get("/api/servers/{server_id}/entities")
async def server_entities(server_id: int, request: Request, window: Optional[str] = None) -> dict:
    return await _server_entities(server_id, request, window)


@router.get("/api/platforms/{server_id}/entities")
async def platform_entities_alias(server_id: int, request: Request, window: Optional[str] = None) -> dict:
    return await _server_entities(server_id, request, window)
