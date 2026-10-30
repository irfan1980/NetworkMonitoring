from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from croniter import croniter

from _scheduler.model import CRON_PRESETS, REBOOT, Trigger
from _scheduler.utc import iso, now_utc, to_utc

_DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def validate_cron(expr: str) -> None:
    if expr == REBOOT or expr in CRON_PRESETS:
        return
    if expr.startswith("@"):
        raise ValueError(f"unknown cron preset {expr!r}")
    if len(expr.split()) != 5:
        raise ValueError("cron needs exactly 5 fields: minute hour day-of-month month day-of-week")
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression {expr!r}")


def is_reboot(trigger: Trigger) -> bool:
    return trigger.kind == "cron" and trigger.cron == REBOOT


def compile_cron(trigger: Trigger) -> Optional[str]:
    if trigger.kind == "cron":
        return None if is_reboot(trigger) else trigger.cron
    if trigger.kind in ("daily", "weekly", "monthly"):
        hour, minute = (int(p) for p in trigger.time.split(":"))
        if trigger.kind == "daily":
            return f"{minute} {hour} * * *"
        if trigger.kind == "weekly":
            return f"{minute} {hour} * * {','.join(str(d) for d in trigger.days_of_week)}"
        dom = "L" if trigger.day_of_month == -1 else str(trigger.day_of_month)
        return f"{minute} {hour} {dom} * *"
    return None


def next_fire(trigger: Trigger, after: datetime) -> Optional[datetime]:
    after = to_utc(after)
    cron = compile_cron(trigger)
    if cron is not None:
        return croniter(cron, after).get_next(datetime)
    if trigger.kind == "once":
        return trigger.at if trigger.at > after else None
    if trigger.kind == "interval":
        anchor = trigger.start_at
        if anchor > after:
            return anchor
        step = timedelta(seconds=trigger.every_seconds)
        n = int((after - anchor) / step) + 1
        return anchor + n * step
    return None


def preview(trigger: Trigger, count: int = 5, after: Optional[datetime] = None) -> list[datetime]:
    cursor = to_utc(after) if after else now_utc()
    out: list[datetime] = []
    while len(out) < count:
        nxt = next_fire(trigger, cursor)
        if nxt is None:
            break
        out.append(nxt)
        cursor = nxt
    return out


def summarize(trigger: Trigger) -> str:
    kind = trigger.kind
    if kind == "cron":
        return "at relay start" if is_reboot(trigger) else f"cron {trigger.cron} UTC"
    if kind == "once":
        return f"once at {iso(trigger.at)}"
    if kind == "interval":
        return f"every {_span(trigger.every_seconds)}"
    if kind == "daily":
        return f"daily at {trigger.time} UTC"
    if kind == "weekly":
        days = ",".join(_DAY_NAMES[d] for d in trigger.days_of_week)
        return f"weekly on {days} at {trigger.time} UTC"
    if kind == "monthly":
        day = "last day" if trigger.day_of_month == -1 else f"day {trigger.day_of_month}"
        return f"monthly on {day} at {trigger.time} UTC"
    if trigger.event == "machine_online":
        return "when the machine comes online"
    if trigger.event == "relay_start":
        return "at relay start"
    return f"after schedule #{trigger.after_schedule_id} ({trigger.on})"


def _span(seconds: int) -> str:
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "min")):
        if seconds % size == 0:
            n = seconds // size
            return f"{n} {unit}" + ("s" if n != 1 and unit != "min" else "")
    return f"{seconds}s"
