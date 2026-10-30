from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from _db.factory import build_driver
from _lib.expected_errors import describe_driver_error

router = APIRouter()
logger = logging.getLogger("relay.http.pipeline_errors")

_FULL_HISTORY_LIMIT = 100_000


def _split_pipeline_ref(ref: str) -> tuple[Optional[int], int]:
    server_part, sep, number = ref.rpartition(":")
    try:
        return (int(server_part) if sep else None), int(number)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid pipeline id {ref!r}; use <serverId>:<number>")


async def _find_owning_servers_and_rows(config, pipeline_id: int, only_server: Optional[int]):
    found = []
    for server in config.servers:
        if only_server is not None and server.id != only_server:
            continue
        driver = build_driver(server, config.settings)
        try:
            rows = await driver.fetch_registry_rows()
        except Exception as exc:
            is_expected, message = describe_driver_error(exc, server.platform, f"server {server.id} ({server.platform})")
            if is_expected:
                logger.warning("pipeline_errors: skipping %s while resolving pipeline %s owner: %s", server.id, pipeline_id, message)
            else:
                logger.error("pipeline_errors: registry read for server %s failed unexpectedly", server.id, exc_info=True)
            continue
        if any(r.pipeline_id == pipeline_id for r in rows):
            found.append((server, driver))
    return found


@router.get("/api/pipelines/{pipeline_ref}/errors")
async def pipeline_errors(pipeline_ref: str, request: Request) -> list[dict]:
    config_store = request.app.state.config_store
    config = config_store.get()

    only_server, pipeline_id = _split_pipeline_ref(pipeline_ref)
    found = await _find_owning_servers_and_rows(config, pipeline_id, only_server)
    if not found:
        raise HTTPException(status_code=404, detail=f"unknown pipeline id {pipeline_ref}")
    if len(found) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ambiguous_pipeline",
                "message": f"pipeline {pipeline_id} exists on several servers; use <serverId>:<number>",
                "candidates": [f"{s.id}:{pipeline_id}" for s, _ in found],
            },
        )
    server, driver = found[0]

    try:
        rows = await driver.fetch_error_rows(limit=_FULL_HISTORY_LIMIT)
    except Exception as exc:
        is_expected, message = describe_driver_error(exc, server.platform, f"server {server.id} ({server.platform})")
        if is_expected:
            logger.warning("pipeline_errors: server %s unavailable: %s", server.id, message)
            raise HTTPException(status_code=503, detail=message) from exc
        logger.error("pipeline_errors: fetch_error_rows for server %s failed unexpectedly", server.id, exc_info=True)
        raise HTTPException(status_code=500, detail="internal error") from exc
    return [r for r in rows if r["pipeline_id"] == pipeline_id]
