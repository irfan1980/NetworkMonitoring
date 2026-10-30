from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from _alerts.shared_ack import apply_acks
from _lib.auth import REJECTION_REASON, authenticate, bearer_token
from _lib.origin_check import is_origin_allowed
from _push.sources import active_alerts
from _ws.close_codes import CLOSE_AUTH_FAILED, CLOSE_MACHINE_OFFLINE, CLOSE_ORIGIN_FORBIDDEN, CLOSE_UNKNOWN_MACHINE
from _ws.estate_payload import machine_alerts_wire, static_snapshot_sections
from _ws.machine_scope import build_machine_sections, machines_wire
from _ws.payload import _ordered, _now_iso
from _scheduler.wire import scheduler_alerts_wire, schedules_wire
from _ops.silence import PinRateLimited, PinRejected
from _ws.rate_limit import AckRateLimiter
from _ws.session import WsSession

logger = logging.getLogger("relay.ws.router")

FULL_WS_SNAPSHOT_KEY_ORDER = [
    "type", "seq", "ts", "source", "servers", "platforms", "pipelines", "objects",
    "topology", "pairs", "buses", "activeAlerts", "summary", "configIssues", "thresholds",
]

THIN_WS_SNAPSHOT_KEY_ORDER = [
    "type", "seq", "ts", "source", "machines", "services", "buses", "activeAlerts", "schedules", "configIssues",
    "pairs", "pipelines",
]

MACHINE_SNAPSHOT_KEY_ORDER = [
    "type", "seq", "ts", "source", "machine_id", "active_services", "emulator", "buses",
    "pipelines", "objects", "topology", "pairs", "packets", "activeAlerts", "summary", "thresholds",
]

REAL_SNAPSHOT_SOURCE = "live"

router = APIRouter()

_PUSH_POLL_SECONDS = 1.0


def _ack_handler(app_state, session_ref: list, account_name: str):
    async def on_ack(msg) -> None:
        session = session_ref[0]
        client_id = msg.ackedBy or msg.clientId or session.client_id or "unknown"
        try:
            app_state.silence_store.verify_pin(client_id, msg.pin, app_state.config_store.get().settings.ack_pin)
        except PinRateLimited:
            await session.send_ack_result(msg.alertId, False, "rate_limited")
        except PinRejected:
            await session.send_ack_result(msg.alertId, False, "invalid_pin")
        else:
            if msg.alertId not in {a["alertId"] for a in active_alerts(app_state, app_state.config_store.get())}:
                await session.send_ack_result(msg.alertId, False, "unknown_alert")
                return
            app_state.alert_acks.acknowledge(msg.alertId, account_name)
            await session.send_ack_result(msg.alertId, True)
            await app_state.estate_loop.push_snapshots()

    return on_ack


async def _check_origin(websocket: WebSocket) -> bool:
    config = websocket.app.state.config_store.get()
    origin = websocket.headers.get("origin")
    if not is_origin_allowed(origin, config.settings.ws_allowed_origins):
        await websocket.close(code=CLOSE_ORIGIN_FORBIDDEN, reason="origin not allowed")
        return False
    return True


async def _authenticate(websocket: WebSocket):
    token, source = bearer_token(websocket.headers, websocket.query_params)
    session, reason = authenticate(websocket.app.state, token)
    if session is None:
        logger.warning(
            "ws session rejected: %s (token from %s) path=%s client_ip=%s",
            reason, source, websocket.url.path, websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=CLOSE_AUTH_FAILED, reason=REJECTION_REASON)
        return None
    return session


@router.websocket("/ws")
async def ws_thin_inventory(websocket: WebSocket) -> None:
    await websocket.accept()
    if not await _check_origin(websocket):
        return
    ws_session = await _authenticate(websocket)
    if ws_session is None:
        return

    app_state = websocket.app.state
    config_store = websocket.app.state.config_store
    estate_loop = websocket.app.state.estate_loop
    registry = websocket.app.state.machine_registry
    incidents = websocket.app.state.machine_alert_incidents
    ack_limiter = AckRateLimiter.from_settings(config_store.get().settings)

    def _build_snapshot(seq: int) -> dict:
        config = config_store.get()
        estate_sections = estate_loop.latest or static_snapshot_sections(config)
        body = {
            "type": "snapshot",
            "seq": seq,
            "ts": _now_iso(),
            "source": REAL_SNAPSHOT_SOURCE,
            "machines": machines_wire(config, registry, app_state.health_probe),
            "services": estate_sections.get("services", []),
            "buses": estate_sections.get("buses", []),
            "activeAlerts": apply_acks(app_state, machine_alerts_wire(incidents, config.alerts)
            + scheduler_alerts_wire(app_state.schedule_incidents, app_state.scheduler_endpoint_incidents, config)),
            "schedules": schedules_wire(app_state, config),
            "configIssues": estate_sections.get("configIssues", []),
            "pairs": estate_sections.get("servicePairs", []),
            "pipelines": estate_sections.get("servicePipelines", []),
        }
        return _ordered(body, THIN_WS_SNAPSHOT_KEY_ORDER)

    session = WsSession(send_json=websocket.send_json, ack_limiter=ack_limiter, build_snapshot=_build_snapshot)
    session.on_ack = _ack_handler(app_state, [session], ws_session.account["name"])
    estate_loop.register(session)

    try:
        while True:
            raw = await websocket.receive_json()
            await session.handle_inbound(raw)
    except WebSocketDisconnect:
        return
    finally:
        estate_loop.unregister(session)


@router.websocket("/ws/machine/{machine_id}")
async def ws_machine(websocket: WebSocket, machine_id: int) -> None:
    await websocket.accept()
    if not await _check_origin(websocket):
        return
    ws_session = await _authenticate(websocket)
    if ws_session is None:
        return

    config_store = websocket.app.state.config_store
    config = config_store.get()
    machine = next((m for m in config.machines if m.machine_id == machine_id), None)
    if machine is None:
        await websocket.close(code=CLOSE_UNKNOWN_MACHINE, reason="unknown machine_id")
        return

    registry = websocket.app.state.machine_registry
    state = registry.state_for(machine_id)
    if state.status != "online":
        await websocket.close(code=CLOSE_MACHINE_OFFLINE, reason="machine not confirmed online")
        return

    estate_loop = websocket.app.state.estate_loop
    ack_limiter = AckRateLimiter.from_settings(config.settings)
    closed = asyncio.Event()

    async def _close_now() -> None:
        closed.set()
        try:
            await websocket.close(code=CLOSE_MACHINE_OFFLINE, reason="machine went offline")
        except Exception:
            pass

    conn_id = state.register_connection(_close_now)

    def _build_snapshot(seq: int) -> dict:
        estate_sections = estate_loop.latest
        if estate_sections is None:
            estate_sections = static_snapshot_sections(config_store.get())
        machine_body = build_machine_sections(websocket.app.state, config_store.get(), machine, estate_sections)
        body = {
            "type": "snapshot",
            "seq": seq,
            "ts": _now_iso(),
            "source": REAL_SNAPSHOT_SOURCE,
            "machine_id": machine_id,
            "active_services": state.active_services,
            **machine_body,
        }
        return _ordered(body, MACHINE_SNAPSHOT_KEY_ORDER)

    session = WsSession(send_json=websocket.send_json, ack_limiter=ack_limiter, build_snapshot=_build_snapshot)
    session.on_ack = _ack_handler(websocket.app.state, [session], ws_session.account["name"])
    previous_active_services = list(state.active_services)

    async def _pusher() -> None:
        nonlocal previous_active_services
        last_estate_tick = estate_loop.latest
        last_acks = websocket.app.state.alert_acks.version
        while not closed.is_set():
            await asyncio.sleep(_PUSH_POLL_SECONDS)
            if closed.is_set() or not session.hello_received:
                continue
            current_tick = estate_loop.latest
            services_changed = state.active_services != previous_active_services
            tick_changed = current_tick is not last_estate_tick
            acks_changed = websocket.app.state.alert_acks.version != last_acks
            if services_changed or tick_changed or acks_changed:
                last_acks = websocket.app.state.alert_acks.version
                previous_active_services = list(state.active_services)
                last_estate_tick = current_tick
                if os.environ.get("RELAY_PACKETS_TRACE") == "1":
                    logger.info("PKTTRACE push machine=%s services_changed=%s tick_changed=%s", machine_id, services_changed, tick_changed)
                await session.send_snapshot()

    pusher_task = asyncio.create_task(_pusher())
    try:
        while True:
            raw = await websocket.receive_json()
            await session.handle_inbound(raw)
    except WebSocketDisconnect:
        return
    finally:
        pusher_task.cancel()
        state.unregister_connection(conn_id)
