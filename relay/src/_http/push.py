from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from _push.accounts import PLATFORMS, AccountsFileError, account_key
from _push.schedule_window import WindowError, parse_schedule

router = APIRouter()
logger = logging.getLogger("relay.push")

MAX_TOKEN_LENGTH = 4096


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": code, "error": code, "message": message})


def _text(body: dict, name: str, limit: int) -> Optional[str]:
    value = body.get(name)
    return value if isinstance(value, str) and 0 < len(value.strip()) <= limit else None


def _session_key(request: Request) -> str:
    return account_key(request.state.session.account["account_secret"])


@router.post("/api/push/register")
async def register(request: Request, body: dict = Body(...)):
    token = _text(body, "token", MAX_TOKEN_LENGTH)
    platform = body.get("platform")
    device_name = body.get("device_name", "")
    if token is None or platform not in PLATFORMS or not isinstance(device_name, str) or len(device_name) > 120:
        return _err(422, "invalid_request", "token (string), platform (android|ios|web) and device_name (string) are required")
    state, session = request.app.state, request.state.session
    previous = state.push.accounts.account_for_token(token)
    is_new = previous is None or account_key(previous["account_secret"]) != _session_key(request)
    try:
        registered = state.push.accounts.register(
            session.account["account_secret"], token, platform, device_name, session.record.device_id
        )
    except AccountsFileError as exc:
        return _err(503, "accounts_file_unreadable", str(exc))
    if registered is None:
        return _err(401, "session_invalid", "the account no longer exists")
    state.sessions.set_push_token(session, token)
    sent, error = False, None
    if is_new:
        try:
            sent, error = await asyncio.get_running_loop().run_in_executor(
                None, state.push.send_confirmation, registered["name"], registered["min_severity"], token, device_name
            )
        except Exception as exc:
            logger.warning("push confirmation crashed: %s", exc)
            sent, error = False, f"{type(exc).__name__}"
    return {
        "account": registered["name"], "min_severity": registered["min_severity"],
        "confirmation_sent": bool(sent), "confirmation_error": error,
    }


@router.post("/api/push/unregister")
async def unregister(request: Request, body: dict = Body(...)):
    token = _text(body, "token", MAX_TOKEN_LENGTH)
    if token is None:
        return _err(422, "invalid_request", "token (string) is required")
    state, session = request.app.state, request.state.session
    owner = state.push.accounts.account_for_token(token)
    if owner is None or account_key(owner["account_secret"]) != _session_key(request):
        return {"unregistered": False}
    try:
        removed = state.push.accounts.unregister(token)
    except AccountsFileError as exc:
        return _err(503, "accounts_file_unreadable", str(exc))
    if session.record.push_token == token:
        state.sessions.set_push_token(session, None)
    return {"unregistered": removed}


@router.get("/api/push/status")
async def status(request: Request, token: str = Query(..., min_length=1, max_length=MAX_TOKEN_LENGTH)):
    push = request.app.state.push
    owner = push.accounts.account_for_token(token)
    mine = owner is not None and account_key(owner["account_secret"]) == _session_key(request)
    return {
        "registered": mine,
        "account": owner["name"] if mine else None,
        "min_severity": owner["min_severity"] if mine else None,
        "push_enabled": bool(request.app.state.config_store.get().settings.push.enabled and push.available),
    }


@router.get("/api/push/mutes")
async def mutes(request: Request):
    return {"muted": request.app.state.push.mutes.muted_alerts(_session_key(request))}


async def _set_mute(request: Request, body: dict, mute: bool):
    alert_id = _text(body, "alertId", 300)
    if alert_id is None:
        return _err(422, "invalid_request", "alertId (string) is required")
    push = request.app.state.push
    if mute and alert_id not in {a["alertId"] for a in (push.current_alerts() or [])}:
        return _err(404, "unknown_alert", f"alert {alert_id!r} is not active")
    (push.mutes.mute if mute else push.mutes.unmute)(alert_id, _session_key(request))
    return {"alertId": alert_id, "muted": mute}


@router.post("/api/push/mute")
async def mute(request: Request, body: dict = Body(...)):
    return await _set_mute(request, body, True)


@router.post("/api/push/unmute")
async def unmute(request: Request, body: dict = Body(...)):
    return await _set_mute(request, body, False)


@router.get("/api/push/window")
async def get_window(request: Request):
    account = request.state.session.account
    return {"window": account.get("push_window")}


@router.put("/api/push/window")
async def put_window(request: Request, body: dict = Body(...)):
    raw = body["window"] if "window" in body else body
    try:
        window = None if raw is None else parse_schedule(raw)
    except WindowError as exc:
        return _err(400, "invalid_window", str(exc))
    try:
        saved = request.app.state.push.accounts.set_window(request.state.session.account["account_secret"], window)
    except AccountsFileError as exc:
        return _err(503, "accounts_file_unreadable", str(exc))
    if not saved:
        return _err(401, "session_invalid", "the account no longer exists")
    return {"window": window}
