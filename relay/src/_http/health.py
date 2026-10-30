from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    poll_stats = request.app.state.poll_stats
    return {
        "status": "ok",
        "pollStats": poll_stats.snapshot(),
    }
