from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

REPORT_BOUNDS = (0, 3600)
REPEAT_BOUNDS = (60, 86400)
FIELDS = {
    "alert_report_interval": ("report", REPORT_BOUNDS),
    "alert_repeat_interval": ("repeat", REPEAT_BOUNDS),
    "alert_acknoledge_repeat_interval": ("acked_repeat", REPEAT_BOUNDS),
    "alert_acknowledge_repeat_interval": ("acked_repeat", REPEAT_BOUNDS),
}


@dataclass(frozen=True)
class MachineAlert:
    enabled: bool = True
    report: Optional[int] = None
    repeat: Optional[int] = None
    acked_repeat: Optional[int] = None


def parse_machine_alert(raw: Any) -> tuple[MachineAlert, list[str]]:
    if raw is None:
        return MachineAlert(), []
    if not isinstance(raw, dict):
        return MachineAlert(), ["machine_alert must be an object"]
    problems: list[str] = []
    values: dict[str, Any] = {}
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        problems.append("enabled must be true or false")
        enabled = True
    for key, value in raw.items():
        if key == "enabled":
            continue
        if key not in FIELDS:
            problems.append(f"unknown key {key!r}")
            continue
        name, (low, high) = FIELDS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            problems.append(f"{key} must be a whole number of seconds between {low} and {high}")
            continue
        values[name] = value
    return MachineAlert(enabled=enabled, **values), problems


def machine_alert_for(config, machine_id: str) -> MachineAlert:
    for machine in config.machines:
        if str(machine.machine_id) == machine_id:
            return parse_machine_alert(getattr(machine, "machine_alert", None))[0]
    return MachineAlert()


@dataclass(frozen=True)
class Cadence:
    enabled: bool
    grace: float
    repeat: float
    acked_repeat: float


def resolve_cadence(scopes, config, push) -> Cadence:
    machines = [machine_alert_for(config, m) for m, _svc in scopes]
    default_repeat = push.repeat_minutes_unacknowledged * 60
    default_acked = push.repeat_minutes_acknowledged * 60
    if not machines:
        return Cadence(True, push.grace_seconds, default_repeat, default_acked)
    return Cadence(
        enabled=any(m.enabled for m in machines),
        grace=min(m.report if m.report is not None else push.grace_seconds for m in machines),
        repeat=min(m.repeat if m.repeat is not None else default_repeat for m in machines),
        acked_repeat=min(m.acked_repeat if m.acked_repeat is not None else default_acked for m in machines),
    )
