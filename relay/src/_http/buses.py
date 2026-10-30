from __future__ import annotations

from fastapi import APIRouter, Request

from _http.live_attribution import refresh_attribution
from _metrics.readability import ReadStatus

router = APIRouter()


@router.get("/api/buses")
async def list_buses(request: Request) -> list[dict]:
    config_store = request.app.state.config_store
    config = config_store.get()

    result = await refresh_attribution(request.app.state)

    out: list[dict] = []
    for bus in config.service_busses:
        endpoint_id = f"bus:{bus.id}:endpoint"
        emitted = result.emitted.get(endpoint_id, ReadStatus.OK)
        out.append(
            {
                "id": bus.id,
                "name": bus.name,
                "ok": emitted == ReadStatus.OK,
                "blockedBy": result.blocked_by.get(endpoint_id),
                "topics": [bus.topic],
                "usedBy": [s.id for s in config.servers if bus.id in s.used_buses],
            }
        )
    return out
