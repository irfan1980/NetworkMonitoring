from __future__ import annotations

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from _push.accounts import PLATFORMS, AccountsFileError

router = APIRouter()


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": code, "error": code, "message": message})


@router.post("/api/auth/login")
async def login(request: Request, body: dict = Body(...)):
    secret, platform, device_name = body.get("account_secret"), body.get("platform"), body.get("device_name", "")
    device_id = body.get("device_id")
    if not isinstance(secret, str) or not secret or platform not in PLATFORMS or not isinstance(device_name, str) or len(device_name) > 120:
        return _err(422, "invalid_request", "account_secret (string), platform (android|ios|web) and device_name (string) are required")
    if device_id is not None and (not isinstance(device_id, str) or not 8 <= len(device_id) <= 128):
        return _err(422, "invalid_request", "device_id must be a string of 8 to 128 characters")
    state = request.app.state
    limiter = state.login_rate_limiter
    key = f"login:{request.client.host if request.client else 'unknown'}"
    if limiter.is_locked(key):
        return _err(429, "rate_limited", "too many wrong account secrets, try again later")
    state.sync_accounts_path()
    account = state.push.accounts.find_by_secret(secret)
    if account is None:
        limiter.record_failure(key)
        return _err(401, "invalid_account_secret", "invalid account secret")
    token, session = state.sessions.create(account, device_name, platform, device_id)
    return {
        "token": token,
        "account": account["name"],
        "min_severity": account["min_severity"],
        "expires_at": session.expires_at_iso,
    }


protected = APIRouter()


@protected.post("/api/auth/logout")
async def logout(request: Request):
    state, session = request.app.state, request.state.session
    if session.record.push_token:
        try:
            state.push.accounts.unregister(session.record.push_token)
        except AccountsFileError:
            pass
    state.sessions.revoke(session)
    return {"logged_out": True}


@protected.get("/api/auth/me")
async def me(request: Request):
    session = request.state.session
    return {
        "account": session.account["name"],
        "min_severity": session.account["min_severity"],
        "expires_at": session.expires_at_iso,
        "device_id": session.record.device_id,
    }
