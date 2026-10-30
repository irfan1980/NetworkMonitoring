from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from _ops.silence import PinRateLimited, PinRejected

router = APIRouter()


class SilenceCreateRequest(BaseModel):
    reason: str
    minutes: Optional[float] = None


def _client_id(request: Request, x_client_id: Optional[str], clientId: Optional[str]) -> str:
    session = getattr(request.state, "session", None)
    return session.account["name"] if session is not None else (x_client_id or clientId or "unknown")


@router.post("/ops/silence")
async def create_silence(
    body: SilenceCreateRequest,
    request: Request,
    x_client_id: Optional[str] = Header(None),
    x_client_secret: Optional[str] = Header(None),
    x_ack_pin: Optional[str] = Header(None),
    clientId: Optional[str] = None,
    clientSecret: Optional[str] = None,
    pin: Optional[str] = None,
) -> dict:
    config = request.app.state.config_store.get()
    store = request.app.state.silence_store
    client_id = _client_id(request, x_client_id, clientId)
    supplied_pin = x_ack_pin or pin

    try:
        store.verify_pin(client_id, supplied_pin, config.settings.ack_pin)
    except PinRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except PinRejected as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    minutes = body.minutes if body.minutes is not None else config.settings.device_silence_minutes
    silence = store.create(reason=body.reason, minutes=minutes, created_by=client_id)
    return silence.as_dict()


@router.get("/ops/silences")
async def list_silences(request: Request) -> list[dict]:
    store = request.app.state.silence_store
    return [s.as_dict() for s in store.list_active()]


@router.delete("/ops/silence/{silence_id}")
async def delete_silence(
    silence_id: int,
    request: Request,
    x_client_id: Optional[str] = Header(None),
    x_ack_pin: Optional[str] = Header(None),
    clientId: Optional[str] = None,
    pin: Optional[str] = None,
) -> dict:
    config = request.app.state.config_store.get()
    store = request.app.state.silence_store
    client_id = _client_id(request, x_client_id, clientId)
    supplied_pin = x_ack_pin or pin

    try:
        store.verify_pin(client_id, supplied_pin, config.settings.ack_pin)
    except PinRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except PinRejected as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    found = store.delete(silence_id, deleted_by=client_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"unknown silence id {silence_id}")
    return {"deleted": True, "id": silence_id}
