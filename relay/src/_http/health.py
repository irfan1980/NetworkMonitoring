from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    poll_stats = request.app.state.poll_stats
    body = {
        "status": "ok",
        "configMode": "fallback" if getattr(request.app.state, "fallback_config", False) else "main",
        "pollStats": poll_stats.snapshot(),
    }
    if body["configMode"] == "fallback":
        body["configNote"] = "running on the blank fallback config: relay_config.json is missing; no logins, no polling"
    return body
