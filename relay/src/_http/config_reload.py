from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/config/reload")
async def config_reload(request: Request) -> dict:
    config_store = request.app.state.config_store
    ok, error = config_store.force_reload()
    return {"reloaded": ok, "error": error}
