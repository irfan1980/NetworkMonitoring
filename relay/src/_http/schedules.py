from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from _ops.silence import PinRateLimited, PinRejected
from _scheduler.executor_client import EndpointAuthRejected, EndpointError
from _scheduler.health_probe import AUTH_REJECTED_TEXT
from _scheduler.model import ScheduleInput, Trigger
from _scheduler.triggers import is_reboot, preview, summarize
from _scheduler.utc import iso
from _scheduler.wire import schedule_detail

router = APIRouter()

RUNNING_MESSAGE = "running, try again after it finishes"


def _err(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, **extra})


def _validation_error(code: str, exc: ValidationError) -> JSONResponse:
    errors = [
        {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"].removeprefix("Value error, ")}
        for e in exc.errors()
    ]
    first = errors[0]
    prefix = f"{first['field']}: " if first["field"] else ""
    return _err(422, code, prefix + first["message"], errors=errors)


class PinGateError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def pin_error_handler(_request: Request, exc: PinGateError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.code, "error": exc.code, "message": exc.message})


async def pin_gate(
    request: Request,
    x_client_id: Optional[str] = Header(None),
    x_ack_pin: Optional[str] = Header(None),
    clientId: Optional[str] = None,
    pin: Optional[str] = None,
) -> None:
    config = request.app.state.config_store.get()
    session = getattr(request.state, "session", None)
    client_id = session.account["name"] if session is not None else (x_client_id or clientId or "unknown")
    try:
        request.app.state.silence_store.verify_pin(client_id, x_ack_pin or pin, config.settings.ack_pin)
    except PinRateLimited as exc:
        raise PinGateError(429, str(exc), "too many wrong PINs, try again later") from exc
    except PinRejected as exc:
        raise PinGateError(401, str(exc), "wrong PIN") from exc


async def write_gate(request: Request, _pin: None = Depends(pin_gate)) -> None:
    if not request.app.state.scheduler_loop.enabled:
        raise HTTPException(
            status_code=409,
            detail={"error": "scheduler_disabled", "message": "another relay instance holds the scheduler lock"},
        )


def _detail(request: Request, schedule) -> dict:
    state = request.app.state
    return schedule_detail(schedule, state.config_store.get(), state.health_probe, state.run_log, state.scheduler_loop)


def _check_machine(config, machine_id: int) -> Optional[JSONResponse]:
    machine = next((m for m in config.machines if m.machine_id == machine_id), None)
    if machine is None:
        return _err(422, "invalid_machine", f"machine {machine_id} does not exist")
    if not machine.machine_scheduler:
        return _err(422, "invalid_machine", f"machine {machine_id} has machine_scheduler false")
    return None


def _check_refs(request: Request, data: ScheduleInput, self_id: Optional[int]) -> Optional[JSONResponse]:
    store = request.app.state.schedule_store
    bad = _check_machine(request.app.state.config_store.get(), data.machine_id)
    if bad is not None:
        return bad
    trigger = data.trigger
    if trigger.kind == "event" and trigger.event == "after_schedule":
        target = trigger.after_schedule_id
        if store.get(target) is None:
            return _err(422, "invalid_trigger", f"after_schedule_id {target} does not exist")
        if target == self_id:
            return _err(422, "invalid_trigger", "a schedule cannot run after itself")
        seen = set()
        cursor = target
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            current = store.get(cursor)
            nxt = None
            if current and current.trigger.kind == "event" and current.trigger.event == "after_schedule":
                nxt = current.trigger.after_schedule_id
            if self_id is not None and nxt == self_id:
                return _err(422, "invalid_trigger", "after_schedule would create a cycle")
            cursor = nxt
    return None


def _parse(body: dict) -> ScheduleInput | JSONResponse:
    try:
        return ScheduleInput.model_validate(body)
    except ValidationError as exc:
        return _validation_error("invalid_schedule", exc)


def _not_found(schedule_id: int) -> JSONResponse:
    return _err(404, "not_found", f"unknown schedule id {schedule_id}")


@router.get("/api/schedules")
async def list_schedules(request: Request) -> dict:
    store = request.app.state.schedule_store
    return {"schedules": [_detail(request, s) for s in store.list()]}


@router.post("/api/schedules/preview")
async def preview_trigger(body: dict = Body(...)):
    try:
        trigger = Trigger.model_validate(body.get("trigger", body))
    except ValidationError as exc:
        return _validation_error("invalid_trigger", exc)
    times = [] if is_reboot(trigger) else preview(trigger, 5)
    return {"next": [iso(t) for t in times], "summary": summarize(trigger)}


@router.post("/api/schedules/check-script", dependencies=[Depends(write_gate)])
async def check_script(request: Request, body: dict = Body(...)):
    machine_id, script = body.get("machine_id"), body.get("script")
    if not isinstance(machine_id, int) or isinstance(machine_id, bool) or not isinstance(script, str) or not script.strip():
        return _err(422, "invalid_request", "machine_id (int) and script (string) are required")
    config = request.app.state.config_store.get()
    bad = _check_machine(config, machine_id)
    if bad is not None:
        return bad
    machine = next(m for m in config.machines if m.machine_id == machine_id)
    try:
        return await request.app.state.executor_client.check_script(machine, script)
    except EndpointAuthRejected as exc:
        return _err(502, "auth_rejected", AUTH_REJECTED_TEXT, detail=str(exc))
    except EndpointError as exc:
        return _err(502, "endpoint_unavailable", f"scheduler endpoint unreachable: {exc}")


@router.get("/api/schedules/{schedule_id}")
async def get_schedule(schedule_id: int, request: Request):
    schedule = request.app.state.schedule_store.get(schedule_id)
    if schedule is None:
        return _not_found(schedule_id)
    return _detail(request, schedule)


@router.get("/api/schedules/{schedule_id}/runs")
async def list_runs(
    schedule_id: int,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    before: Optional[int] = Query(None, ge=0),
):
    if request.app.state.schedule_store.get(schedule_id) is None:
        return _not_found(schedule_id)
    runs, next_before = request.app.state.run_log.read(schedule_id, limit, before)
    return {"runs": runs, "next_before": next_before}


@router.post("/api/schedules", status_code=201, dependencies=[Depends(write_gate)])
async def create_schedule(request: Request, body: dict = Body(...)):
    data = _parse(body)
    if isinstance(data, JSONResponse):
        return data
    bad = _check_refs(request, data, None)
    if bad is not None:
        return bad
    schedule = request.app.state.schedule_store.create(data)
    return JSONResponse(status_code=201, content=_detail(request, schedule))


@router.put("/api/schedules/{schedule_id}", dependencies=[Depends(write_gate)])
async def update_schedule(schedule_id: int, request: Request, body: dict = Body(...)):
    state = request.app.state
    if state.schedule_store.get(schedule_id) is None:
        return _not_found(schedule_id)
    if state.scheduler_loop.is_running(schedule_id):
        return _err(409, "running", RUNNING_MESSAGE)
    data = _parse(body)
    if isinstance(data, JSONResponse):
        return data
    bad = _check_refs(request, data, schedule_id)
    if bad is not None:
        return bad
    schedule = state.schedule_store.replace(schedule_id, data)
    state.scheduler_loop.sync_alerts(state.config_store.get())
    return _detail(request, schedule)


@router.delete("/api/schedules/{schedule_id}", dependencies=[Depends(write_gate)])
async def delete_schedule(schedule_id: int, request: Request):
    state = request.app.state
    if state.schedule_store.get(schedule_id) is None:
        return _not_found(schedule_id)
    if state.scheduler_loop.is_running(schedule_id):
        return _err(409, "running", RUNNING_MESSAGE)
    state.schedule_store.delete(schedule_id)
    state.run_log.delete(schedule_id)
    state.scheduler_loop.forget(schedule_id)
    state.scheduler_loop.sync_alerts(state.config_store.get())
    return {"deleted": True, "id": schedule_id}


async def _set_enabled(schedule_id: int, request: Request, enabled: bool):
    schedule = request.app.state.schedule_store.set_enabled(schedule_id, enabled)
    if schedule is None:
        return _not_found(schedule_id)
    return _detail(request, schedule)


@router.post("/api/schedules/{schedule_id}/enable", dependencies=[Depends(write_gate)])
async def enable_schedule(schedule_id: int, request: Request):
    return await _set_enabled(schedule_id, request, True)


@router.post("/api/schedules/{schedule_id}/disable", dependencies=[Depends(write_gate)])
async def disable_schedule(schedule_id: int, request: Request):
    return await _set_enabled(schedule_id, request, False)


@router.post("/api/schedules/{schedule_id}/run", dependencies=[Depends(write_gate)])
async def run_schedule(schedule_id: int, request: Request):
    state = request.app.state
    schedule = state.schedule_store.get(schedule_id)
    if schedule is None:
        return _not_found(schedule_id)
    bad = _check_machine(state.config_store.get(), schedule.machine_id)
    if bad is not None:
        return bad
    result = state.scheduler_loop.dispatch(schedule, "manual")
    content = {"run_id": result.run_id, "status": result.status}
    if result.reason:
        content["reason"] = result.reason
    return JSONResponse(status_code=202 if result.status == "started" else 200, content=content)


@router.post("/api/schedules/{schedule_id}/stop", dependencies=[Depends(write_gate)])
async def stop_schedule(schedule_id: int, request: Request):
    state = request.app.state
    if state.schedule_store.get(schedule_id) is None:
        return _not_found(schedule_id)
    result = await state.scheduler_loop.stop_runs(schedule_id)
    if result is None:
        return _err(409, "not_running", "no run is in flight for this schedule")
    return result
