from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class WindowError(ValueError):
    pass


def _minutes(value, name: str) -> int:
    match = _HHMM.match(value) if isinstance(value, str) else None
    if match is None:
        raise WindowError(f"{name} must be HH:MM (00:00-23:59)")
    return int(match.group(1)) * 60 + int(match.group(2))


def parse_schedule(raw) -> dict:
    if not isinstance(raw, dict):
        raise WindowError("window must be an object")
    tz = raw.get("timezone")
    try:
        if not isinstance(tz, str) or not tz or ZoneInfo(tz) is None:
            raise ZoneInfoNotFoundError(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise WindowError("timezone must be an IANA name such as Asia/Karachi") from None
    days = raw.get("days")
    if (
        not isinstance(days, list) or not days
        or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days)
    ):
        raise WindowError("days must be a non-empty list of integers 0-6 (0 = Monday)")
    _minutes(raw.get("start"), "start")
    _minutes(raw.get("end"), "end")
    return {"timezone": tz, "days": sorted(set(days)), "start": raw["start"], "end": raw["end"]}


def is_active(schedule: dict, now_epoch: float) -> bool:
    local = datetime.fromtimestamp(now_epoch, ZoneInfo(schedule["timezone"]))
    minute = local.hour * 60 + local.minute
    start, end = _minutes(schedule["start"], "start"), _minutes(schedule["end"], "end")
    today, yesterday = local.weekday(), (local.weekday() - 1) % 7
    if end > start:
        return today in schedule["days"] and start <= minute < end
    return (today in schedule["days"] and minute >= start) or (yesterday in schedule["days"] and minute < end)
