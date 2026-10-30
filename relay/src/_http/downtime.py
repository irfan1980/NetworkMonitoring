from __future__ import annotations

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from _push.downtime import parse_window
from _push.schedule_window import WindowError

router = APIRouter()


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": code, "error": code, "message": message})


def _check_target(request: Request, body: dict) -> None:
    window = parse_window(body, "")
    machines = {str(m.machine_id): m for m in request.app.state.config_store.get().machines}
    machine = machines.get(window["machine"])
    if machine is None:
        raise WindowError(f"unknown machine {window['machine']!r}; known machines: {', '.join(sorted(machines)) or 'none'}")
    nodes = sorted(n.node_id for n in machine.nodes)
    if window["service"] is not None and window["service"] not in nodes:
        raise WindowError(
            f"service {window['service']!r} is not a Database/POS node on machine {window['machine']}; nodes: {', '.join(nodes) or 'none'}"
        )


@router.get("/api/downtime")
async def list_windows(request: Request):
    return {"windows": request.app.state.push.downtime.list()}


@router.post("/api/downtime")
async def create_window(request: Request, body: dict = Body(...)):
    try:
        _check_target(request, body)
        return request.app.state.push.downtime.create(body)
    except WindowError as exc:
        return _err(400, "invalid_window", str(exc))


@router.put("/api/downtime/{window_id}")
async def update_window(window_id: str, request: Request, body: dict = Body(...)):
    try:
        _check_target(request, body)
        window = request.app.state.push.downtime.update(window_id, body)
    except WindowError as exc:
        return _err(400, "invalid_window", str(exc))
    if window is None:
        return _err(404, "unknown_window", f"downtime window {window_id!r} does not exist")
    return window


@router.delete("/api/downtime/{window_id}")
async def delete_window(window_id: str, request: Request):
    if not request.app.state.push.downtime.delete(window_id):
        return _err(404, "unknown_window", f"downtime window {window_id!r} does not exist")
    return {"id": window_id, "deleted": True}
