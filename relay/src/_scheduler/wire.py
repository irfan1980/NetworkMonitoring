from __future__ import annotations

from typing import Optional

from _alerts.dispatch import channels_for_severity
from _scheduler.effective import effective_controls, resolve_controls
from _scheduler.health_probe import AUTH_REJECTED_TEXT, DOWN_TEXT, FORBIDDEN_IP_TEXT
from _scheduler.model import Schedule
from _scheduler.triggers import is_reboot, next_fire, summarize
from _scheduler.utc import iso, now_utc


def _machine_reason(machine, health) -> Optional[str]:
    if machine is None:
        return "machine is not in the config"
    if not machine.machine_scheduler:
        return "machine scheduler not enabled"
    return health.reason(machine)


def schedule_row(schedule: Schedule, config, health, runlog, loop) -> dict:
    machine = next((m for m in config.machines if m.machine_id == schedule.machine_id), None)
    eff = effective_controls(schedule, config.scheduler, machine)
    run_state = runlog.state(schedule.id)
    threshold = eff["fail_threshold"]
    state = run_state.color(threshold)
    reason: Optional[str] = None

    blocked = _machine_reason(machine, health)
    if not schedule.enabled:
        state, reason = "grey", "disabled"
    elif blocked is not None:
        state, reason = "grey", blocked
    elif state == "grey":
        if run_state.last_outcome is None:
            reason = "never run"
        elif run_state.last_outcome == "missed" and run_state.last_reason:
            reason = f"no completed run yet (last attempt missed: {run_state.last_reason})"
        else:
            reason = "no completed run yet"
    elif state == "red":
        reason = f"{run_state.consecutive_failures} consecutive failures: {run_state.last_reason or 'failed'}"
    elif run_state.consecutive_failures:
        reason = f"{run_state.consecutive_failures} failed run(s) in a row, alert at {threshold}"

    running = loop.is_running(schedule.id)
    if running and loop.is_waiting(schedule.id):
        reason = "waiting for endpoint"

    next_run = None
    if schedule.enabled and blocked is None and not is_reboot(schedule.trigger):
        nxt = next_fire(schedule.trigger, now_utc())
        next_run = iso(nxt) if nxt else None

    row = {
        "id": schedule.id,
        "name": schedule.name,
        "machine_id": schedule.machine_id,
        "script": schedule.script,
        "trigger_summary": summarize(schedule.trigger),
        "next_run": next_run,
        "last_run": run_state.last_run,
        "last_outcome": run_state.last_outcome,
        "consecutive_failures": run_state.consecutive_failures,
        "fail_threshold": threshold,
        "state": state,
        "running": running,
    }
    if reason is not None:
        row["reason"] = reason
    return row


def schedule_detail(schedule: Schedule, config, health, runlog, loop) -> dict:
    row = schedule_row(schedule, config, health, runlog, loop)
    machine = next((m for m in config.machines if m.machine_id == schedule.machine_id), None)
    effective, source = resolve_controls(schedule, config.scheduler, machine)
    return {
        **row,
        "args": list(schedule.args),
        "enabled": schedule.enabled,
        "trigger": schedule.trigger.model_dump(mode="json"),
        "controls": schedule.controls.model_dump(mode="json"),
        "stored_fail_threshold": schedule.fail_threshold,
        "effective": effective,
        "timeout_display": "none" if not effective["timeout_s"] else f"{effective['timeout_s']:g}s",
        "effective_source": source,
    }


def schedules_wire(app_state, config) -> list[dict]:
    store = getattr(app_state, "schedule_store", None)
    if store is None:
        return []
    return [
        schedule_row(s, config, app_state.health_probe, app_state.run_log, app_state.scheduler_loop)
        for s in store.list()
    ]


def scheduler_alerts_wire(schedule_incidents, endpoint_incidents, config) -> list[dict]:
    machines = {m.machine_id: m for m in config.machines}
    out: list[dict] = []
    for alert_id, record in endpoint_incidents.items():
        machine = machines.get(record["machine_id"])
        label = machine.machine_name if machine else f"machine {record['machine_id']}"
        if record["state"] == "auth_rejected":
            text = FORBIDDEN_IP_TEXT if record.get("forbidden_ip") else AUTH_REJECTED_TEXT
        else:
            text = DOWN_TEXT
        out.append({
            "alertId": alert_id,
            "kind": "scheduler_endpoint",
            "machine_id": record["machine_id"],
            "rootCause": "scheduler_endpoint",
            "reason": f"{label}: {text}",
            "detail": record.get("detail"),
            "severity": record["severity"],
            "affects": [],
            "channels": channels_for_severity(config.alerts, record["severity"], escalated=False),
            "acknowledged": False,
        })
    for alert_id, record in schedule_incidents.items():
        out.append({
            "alertId": alert_id,
            "kind": "schedule",
            "schedule_id": record["schedule_id"],
            "rootCause": "schedule",
            "reason": f"schedule #{record['schedule_id']} '{record['name']}' failed "
                      f"{record['consecutive_failures']} times in a row (alert at {record['threshold']})",
            "detail": record.get("last_reason"),
            "severity": record["severity"],
            "affects": [],
            "channels": channels_for_severity(config.alerts, record["severity"], escalated=False),
            "acknowledged": False,
        })
    return out
