from __future__ import annotations

from typing import Optional

from _scheduler.model import Schedule


def resolve_controls(schedule: Schedule, settings, machine=None) -> tuple[dict, dict]:
    c = schedule.controls
    md = getattr(machine, "scheduler_defaults", None)

    def pick(own, machine_value, default):
        if own is not None:
            return own, "schedule"
        if machine_value is not None:
            return machine_value, "machine"
        return default, "default"

    machine_timeout = md.timeout_minutes * 60 if md and md.timeout_minutes is not None else None
    fields = {
        "timeout_s": pick(c.timeout_s, machine_timeout, settings.default_timeout_minutes * 60),
        "retries": pick(c.retries, md.retries if md else None, settings.default_retries),
        "retry_delay_s": pick(c.retry_delay_s, md.retry_delay_seconds if md else None, settings.default_retry_delay_seconds),
        "run_if_missed": pick(c.run_if_missed, md.run_if_missed if md else None, settings.default_run_if_missed),
        "fail_threshold": pick(schedule.fail_threshold, md.fail_threshold if md else None, settings.default_fail_threshold),
    }
    return {k: v[0] for k, v in fields.items()}, {k: v[1] for k, v in fields.items()}


def effective_controls(schedule: Schedule, settings, machine: Optional[object] = None) -> dict:
    return resolve_controls(schedule, settings, machine)[0]
