from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from _scheduler.utc import UtcDatetime, now_utc

TriggerKind = Literal["cron", "once", "interval", "daily", "weekly", "monthly", "event"]
EventKind = Literal["machine_online", "relay_start", "after_schedule"]
AfterOn = Literal["success", "failure", "any"]

CRON_PRESETS = ("@hourly", "@daily", "@midnight", "@weekly", "@monthly", "@yearly", "@annually")
REBOOT = "@reboot"

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TriggerKind
    cron: Optional[str] = None
    at: Optional[UtcDatetime] = None
    every_seconds: Optional[int] = Field(None, ge=1)
    start_at: Optional[UtcDatetime] = None
    time: Optional[str] = None
    days_of_week: list[int] = Field(default_factory=list, description="0=Sunday .. 6=Saturday.")
    day_of_month: Optional[int] = Field(None, description="1-31, or -1 for the last day of the month.")
    event: Optional[EventKind] = None
    after_schedule_id: Optional[int] = None
    on: Optional[AfterOn] = None

    @model_validator(mode="after")
    def _check(self) -> "Trigger":
        from _scheduler.triggers import validate_cron

        kind = self.kind
        if kind == "cron":
            if not self.cron or not self.cron.strip():
                raise ValueError("cron trigger needs a cron expression")
            self.cron = self.cron.strip()
            validate_cron(self.cron)
        elif kind == "once":
            if self.at is None:
                raise ValueError("once trigger needs 'at' (ISO 8601 UTC time)")
        elif kind == "interval":
            if self.every_seconds is None:
                raise ValueError("interval trigger needs every_seconds")
            if self.start_at is None:
                self.start_at = now_utc().replace(microsecond=0)
        elif kind in ("daily", "weekly", "monthly"):
            if not self.time or not _TIME_RE.match(self.time):
                raise ValueError(f"{kind} trigger needs time as HH:MM (UTC)")
            if kind == "weekly":
                if not self.days_of_week:
                    raise ValueError("weekly trigger needs days_of_week")
                if any(d < 0 or d > 6 for d in self.days_of_week):
                    raise ValueError("days_of_week values must be 0 (Sunday) to 6 (Saturday)")
                self.days_of_week = sorted(set(self.days_of_week))
            if kind == "monthly":
                dom = self.day_of_month
                if dom is None or not (dom == -1 or 1 <= dom <= 31):
                    raise ValueError("monthly trigger needs day_of_month 1-31 or -1 for the last day")
        elif kind == "event":
            if self.event is None:
                raise ValueError("event trigger needs event: machine_online, relay_start or after_schedule")
            if self.event == "after_schedule":
                if self.after_schedule_id is None:
                    raise ValueError("after_schedule event needs after_schedule_id")
                if self.on is None:
                    self.on = "success"
        return self


class Controls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_s: Optional[float] = Field(None, ge=0, description="0 means no timeout; null inherits.")
    retries: Optional[int] = Field(None, ge=0)
    retry_delay_s: Optional[float] = Field(None, ge=0)
    no_overlap: bool = True
    active_from: Optional[UtcDatetime] = None
    active_until: Optional[UtcDatetime] = None
    run_if_missed: Optional[bool] = None

    @model_validator(mode="after")
    def _window(self) -> "Controls":
        if self.active_from and self.active_until and self.active_until <= self.active_from:
            raise ValueError("active_until must be after active_from")
        return self


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    machine_id: int
    script: str = Field(..., min_length=1)
    args: list[str] = Field(default_factory=list)
    enabled: bool = True
    trigger: Trigger
    controls: Controls = Field(default_factory=Controls)
    fail_threshold: Optional[int] = Field(None, ge=1)


class Schedule(ScheduleInput):
    id: int
