from __future__ import annotations

from fastapi import APIRouter, Request

from config_issues import compute_config_issues

router = APIRouter()


@router.get("/api/config/check")
async def config_check(request: Request) -> dict:
    config_store = request.app.state.config_store
    config = config_store.get()

    schedules = request.app.state.schedule_store.list()
    issues = compute_config_issues(config, schedules, getattr(request.app.state, "state_dir", None)) + list(getattr(request.app.state, "orphan_pipeline_issues", []))
    return {
        "servers": [
            {
                "id": s.id,
                "name": s.name,
                "platform": s.platform,
                "host": s.host,
                "port": s.port,
                "usedBuses": s.used_buses,
            }
            for s in config.servers
        ],
        "service_busses": [
            {
                "id": b.id,
                "name": b.name,
                "health_url": b.health_url,
                "amqp_port": b.amqp_port,
                "topic": b.topic,
            }
            for b in config.service_busses
        ],
        "configIssues": [issue.model_dump() for issue in issues],
    }
